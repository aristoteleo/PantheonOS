# File: rl_gene_panel.py
"""
RL-based Gene Panel Selection Algorithm.

This module implements a reinforcement learning approach for selecting optimal
gene panels from single-cell RNA sequencing data. The algorithm uses:
- Batched actor-critic networks for parallel gene evaluation
- Fixed-size state encoding from expression statistics
- Epsilon-greedy exploration with Gaussian noise
- Knowledge injection from prior gene selection methods

Evolution Targets:
1. reward_panel() - Reward function combining ARI and size penalty
2. SmartCurationTrainer.explore() - Exploration strategy
3. SmartCurationTrainer.optimize() - Policy gradient optimization
"""

import math
import random
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np
import scipy.sparse as sp
import torch
import torch.nn as nn
import torch.nn.functional as F
from tqdm import tqdm
import logging

# Hoist sklearn imports to module scope to avoid repeated import overhead in reward_panel()
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, silhouette_score

from copy import deepcopy

logger = logging.getLogger(__name__)

# =============================================================================
# BATCHED NEURAL NETWORK LAYERS
# =============================================================================

class BatchedLinear(nn.Module):
    """Linear layer with separate weights per gene, computed in parallel via einsum."""

    def __init__(self, n_genes: int, in_features: int, out_features: int):
        super().__init__()
        self.n_genes = n_genes
        self.in_features = in_features
        self.out_features = out_features
        self.weight = nn.Parameter(torch.empty(n_genes, in_features, out_features))
        self.bias = nn.Parameter(torch.empty(n_genes, out_features))
        self._init_weights()

    def _init_weights(self):
        for i in range(self.n_genes):
            nn.init.kaiming_uniform_(self.weight[i], a=math.sqrt(5))
            fan_in = self.in_features
            bound = 1 / math.sqrt(fan_in)
            nn.init.uniform_(self.bias[i], -bound, bound)

    def forward(self, x):
        if x.dim() == 2:
            out = torch.einsum('bi,gio->bgo', x, self.weight) + self.bias
        else:
            out = torch.einsum('bgi,gio->bgo', x, self.weight) + self.bias
        return out


class BatchedLayerNorm(nn.Module):
    """LayerNorm with separate learnable params per gene."""

    def __init__(self, n_genes: int, normalized_shape: int, eps: float = 1e-5):
        super().__init__()
        self.n_genes = n_genes
        self.normalized_shape = normalized_shape
        self.eps = eps
        self.gamma = nn.Parameter(torch.ones(n_genes, normalized_shape))
        self.beta = nn.Parameter(torch.zeros(n_genes, normalized_shape))

    def forward(self, x):
        mean = x.mean(dim=-1, keepdim=True)
        var = x.var(dim=-1, unbiased=False, keepdim=True)
        x_norm = (x - mean) / torch.sqrt(var + self.eps)
        return self.gamma * x_norm + self.beta


# =============================================================================
# STATE ENCODER
# =============================================================================

class StateEncoder(nn.Module):
    """
    Fixed-size state representation using global statistics.
    Encodes 16 features from expression matrix into 64-dim latent space.
    """

    def __init__(self, latent_dim: int = 64, device=None):
        super().__init__()
        self.latent_dim = latent_dim
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")

        input_dim = 16

        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.LayerNorm(128),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(128, 64),
            nn.LayerNorm(64),
            nn.ReLU(),
            nn.Linear(64, self.latent_dim)
        )

        self.decoder = nn.Sequential(
            nn.Linear(self.latent_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 128),
            nn.ReLU(),
            nn.Linear(128, input_dim)
        )

        self.to(self.device)

    @staticmethod
    def _to_dense(X):
        if sp.issparse(X):
            return X.toarray()
        return np.asarray(X)

    @staticmethod
    def compute_stats(X_cells_by_genes: np.ndarray):
        """
        Compute FIXED-SIZE global statistics (16 features).
        Independent of number of genes.
        """
        X = X_cells_by_genes.astype(np.float32, copy=False)
        n_cells, n_genes = X.shape

        gene_means = X.mean(axis=0)
        gene_stds = X.std(axis=0)

        global_mean = X.mean()
        global_std = X.std()
        global_sparsity = float((X == 0).mean())

        flat = X.flatten()
        q10, q25, q50, q75, q90 = np.quantile(flat, [0.1, 0.25, 0.5, 0.75, 0.9])

        mean_of_gene_means = gene_means.mean()
        std_of_gene_means = gene_means.std()
        mean_of_gene_stds = gene_stds.mean()
        std_of_gene_stds = gene_stds.std()

        size_feature = n_genes / 2000.0

        S = np.array([
            global_mean, global_std, global_sparsity,
            q10, q25, q50, q75, q90,
            mean_of_gene_means, std_of_gene_means,
            mean_of_gene_stds, std_of_gene_stds,
            size_feature,
            float(n_genes),
            gene_means.max(), gene_stds.max()
        ], dtype=np.float32)

        S = np.nan_to_num(S, nan=0.0, posinf=1e6, neginf=-1e6)
        return S

    def forward(self, stats_vector: torch.Tensor):
        if stats_vector.dim() == 1:
            stats_vector = stats_vector.unsqueeze(0)
        z = self.encoder(stats_vector)
        recon = self.decoder(z)
        return z.squeeze(0), recon.squeeze(0)

    @torch.no_grad()
    def encode(self, stats_vector: torch.Tensor):
        if stats_vector.dim() == 1:
            stats_vector = stats_vector.unsqueeze(0)
        z = self.encoder(stats_vector)
        return z.squeeze(0)


# =============================================================================
# BATCHED ACTOR-CRITIC NETWORKS
# =============================================================================

class BatchedActor(nn.Module):
    """All gene actors in a single batched network."""

    def __init__(self, n_genes: int, state_dim: int, hidden=(128, 32), device=None):
        super().__init__()
        self.n_genes = n_genes
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.fc1 = BatchedLinear(n_genes, state_dim, hidden[0])
        self.ln1 = BatchedLayerNorm(n_genes, hidden[0])
        self.fc2 = BatchedLinear(n_genes, hidden[0], hidden[1])
        self.fc3 = BatchedLinear(n_genes, hidden[1], 1)
        self.dropout = nn.Dropout(0.1)
        self.to(self.device)

    def forward(self, S):
        x = self.fc1(S)
        x = self.ln1(x)
        x = F.relu(x)
        x = self.dropout(x)
        x = self.fc2(x)
        x = F.relu(x)
        x = self.fc3(x)
        return x.squeeze(-1)


class ScalarCritic(nn.Module):
    """Single scalar value function V(S) aligned with global panel reward."""

    def __init__(self, state_dim: int, hidden=(128, 64), device=None):
        super().__init__()
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden[0]),
            nn.LayerNorm(hidden[0]),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden[0], hidden[1]),
            nn.ReLU(),
            nn.Linear(hidden[1], 1),
        )
        self.to(self.device)

    def forward(self, S):
        if S.dim() == 1:
            S = S.unsqueeze(0)
        return self.net(S)


# =============================================================================
# REPLAY BUFFER
# =============================================================================

class RolloutBuffer:
    """On-policy rollout buffer for PPO/GAE."""

    def __init__(self):
        self.clear()

    def clear(self):
        self.S = []
        # actions are (remove_idx, add_idx) for swap-based local-edit MDP
        self.actions = []
        self.logp = []
        self.r = []
        self.done = []

    def push(self, S_t: torch.Tensor, actions_pair: torch.Tensor, logp_old: torch.Tensor, r_t: float, done: bool):
        self.S.append(S_t.detach().cpu().float())
        self.actions.append(actions_pair.detach().cpu().long())
        self.logp.append(logp_old.detach().cpu().float())
        self.r.append(float(r_t))
        self.done.append(bool(done))

    def to_tensors(self, device=None):
        device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        S = torch.stack(self.S).to(device)
        actions = torch.stack(self.actions).to(device)  # (T, 2) long: [remove_idx, add_idx]
        logp = torch.stack(self.logp).to(device).squeeze(-1)
        r = torch.tensor(self.r, dtype=torch.float32, device=device)
        done = torch.tensor(self.done, dtype=torch.float32, device=device)
        return S, actions, logp, r, done

    def __len__(self):
        return len(self.S)


# =============================================================================
# REWARD FUNCTION (Tertiary Evolution Target)
# =============================================================================

# Try to import GPU-accelerated rapids_singlecell, fall back to scanpy
try:
    import rapids_singlecell as rsc
    _USE_GPU = True
except ImportError:
    _USE_GPU = False

# Hoist scanpy import to module scope (avoid repeated import/branch overhead in reward_panel)
try:
    import scanpy as sc  # type: ignore
    _HAVE_SCANPY = True
except Exception:
    sc = None  # type: ignore
    _HAVE_SCANPY = False


def reward_panel(
    adata,
    genes: List[str],
    label_key: str = 'cell_type',
    *,
    n_neighbors: int = 15,
    resolution: float = 1.0,
    n_pcs: int = 50,
    # NOTE: alpha kept for backward compatibility but no longer used as a 2-term mixer.
    alpha: float = 0.8,
    K_target: int = 500,
    K_max: int = 1000,
    beta: float = 1.5,
    w_ari: float = 0.5,
    w_nmi: float = 0.3,
    w_si: float = 0.2,
    si_subsample: int = 2000,
    # Reward shaping (tertiary evolution target)
    quality_power: float = 1.5,
    c_missing: float = 0.05,
) -> Dict:
    """
    Evaluate a candidate gene panel by clustering performance and size compliance.
    Uses GPU-accelerated rapids_singlecell if available, otherwise falls back to scanpy.

    Reward is multi-metric (ARI, NMI, Silhouette) plus a soft size compliance term.
    The trainer can additionally apply a Lagrangian size penalty externally.

    Args:
        adata: AnnData object with expression data
        genes: List of gene names to evaluate
        label_key: Column in adata.obs with true labels
        n_neighbors: Number of neighbors for graph construction
        resolution: Leiden clustering resolution
        n_pcs: Number of PCA components
        alpha: Backward-compat parameter (deprecated mixer)
        K_target: Target panel size (used for size_term only)
        K_max: Maximum panel size (zero size_term above this)
        beta: Shape parameter for size penalty curve
        w_ari, w_nmi, w_si: Weights for quality metrics
        si_subsample: If ad.n_obs > si_subsample, compute silhouette on a deterministic subsample
        quality_power: Nonlinear sharpening exponent for quality term
        c_missing: Penalty coefficient for panels smaller than K_target after filtering

    Returns:
        Dictionary with reward, quality, ari, nmi, si, size_term, and num_genes
    """

    genes = [g for g in genes if g in adata.var_names]
    K = len(genes)

    if K < 10:
        return dict(reward=0.0, quality=0.0, ari=0.0, nmi=0.0, si=0.0, size_term=0.0, num_genes=K)

    ad = adata[:, genes].copy()

    n_comps = min(n_pcs, K - 1, ad.n_obs - 1)

    if _USE_GPU:
        # GPU-accelerated path using rapids_singlecell
        rsc.pp.pca(ad, n_comps=n_comps)
        rsc.pp.neighbors(ad, n_neighbors=n_neighbors)

        # Make clustering deterministic if supported by installed RAPIDS version
        try:
            rsc.tl.leiden(ad, resolution=resolution, random_state=0)
        except TypeError:
            try:
                rsc.tl.leiden(ad, resolution=resolution, seed=0)
            except TypeError:
                rsc.tl.leiden(ad, resolution=resolution)
    else:
        # CPU fallback using scanpy (import hoisted to module scope)
        if not _HAVE_SCANPY:
            raise ImportError("scanpy is required for CPU reward_panel() path but is not installed.")
        sc.pp.pca(ad, n_comps=n_comps)
        sc.pp.neighbors(ad, n_neighbors=n_neighbors, use_rep='X_pca')
        sc.tl.leiden(ad, resolution=resolution, random_state=0)

    clusters = ad.obs['leiden']
    true = ad.obs[label_key]

    ari = adjusted_rand_score(true, clusters)
    nmi = normalized_mutual_info_score(true, clusters)

    # Silhouette score: compute on PCA representation (if invalid, fall back to 0.0)
    # Potentially subsample cells for speed on large n_obs.
    si = 0.0
    try:
        # silhouette_score expects >1 cluster
        if len(np.unique(np.asarray(clusters))) > 1 and getattr(ad, "obsm", None) is not None and "X_pca" in ad.obsm:
            Xp = ad.obsm["X_pca"]
            cl = np.asarray(clusters)

            if si_subsample is not None and int(si_subsample) > 0 and ad.n_obs > int(si_subsample):
                # Deterministic, allocation-light subsample (stable across calls)
                idx = np.linspace(0, ad.n_obs - 1, int(si_subsample), dtype=int)
                si = float(silhouette_score(Xp[idx], cl[idx]))
            else:
                si = float(silhouette_score(Xp, cl))
    except Exception:
        si = 0.0

    # Scale SI into a bounded range so it doesn't dominate
    si_scaled = float(np.tanh(si))

    quality = w_ari * float(ari) + w_nmi * float(nmi) + w_si * float(si_scaled)

    # Keep size_term for compatibility/diagnostics (trainer may apply Lagrangian penalty)
    if K <= K_target:
        size_term = 1.0
    elif K_target < K <= K_max:
        size_term = (1 - (K - K_target) / (K_max - K_target)) ** beta
    else:
        size_term = 0.0

    # Reward shaping to amplify useful differences (stable clamping + exponent)
    quality_clamped = float(np.clip(quality, 0.0, 1.0))
    quality_sharp = float(quality_clamped ** float(max(1.0, quality_power)))

    missing_pen = 0.0
    if K < int(K_target) and int(K_target) > 0:
        missing_pen = float(c_missing) * float((int(K_target) - K) / float(int(K_target)))

    reward = quality_sharp - missing_pen + 0.0 * float(alpha)  # alpha retained but not used

    return dict(
        reward=float(reward),
        quality=float(quality_sharp),
        ari=float(ari),
        nmi=float(nmi),
        si=float(si),
        size_term=float(size_term),
        num_genes=K,
    )


# =============================================================================
# RL TRAINER
# =============================================================================

class SmartCurationTrainer:
    """
    Gene panel selection trainer.

    NOTE: The original swap-based PPO MDP has been replaced with a Cross-Entropy
    Method (CEM) / elite evolutionary search over K-subsets.

    Key changes:
    - Robust sanitization of gene universe and prior subsets to avoid KeyError
    - Exploration samples full K-sized panels (global exploration + local mutation)
    - Optimization updates a probability vector over genes using reward-weighted elites
    - Reward memoization to reduce expensive clustering recomputation
    """

    def __init__(
        self,
        adata,
        gtilde: List[str],
        encoder: StateEncoder,
        reward_fn,
        label_key: str,
        prior_subsets: Optional[Dict[str, List[str]]] = None,
        K_target: int = 500,
        K_max: int = 1000,
        alpha: float = 0.9,
        gamma: float = 0.99,
        memory_size: int = 2000,
        actor_lr: float = 1e-3,
        critic_lr: float = 5e-4,
        minibatch: int = 64,
        device=None,
        rng=None,
        epsilon: float = 0.5,
        ae_epochs: int = 5,
        encoder_lr: float = 1e-3,
        entropy_beta: float = 0.01,
    ):
        self.adata = adata
        self.K_target = K_target
        self.K_max = K_max
        self.encoder = encoder
        self.reward_fn = reward_fn
        self.label_key = label_key
        self.alpha = alpha
        self.gamma = gamma
        self.minibatch = minibatch
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.rng = rng or np.random.default_rng(42)

        # ---------------------------------------------------------------------
        # Mandatory robustness fix: sanitize gene universe & priors
        # ---------------------------------------------------------------------
        var_set = set(getattr(self.adata, "var_names", []))

        # Deduplicate gtilde while preserving order, then intersect with adata.var_names
        seen = set()
        gtilde_unique = []
        for g in list(gtilde):
            if g in seen:
                continue
            seen.add(g)
            gtilde_unique.append(g)
        gtilde_sanitized = [g for g in gtilde_unique if g in var_set]

        if len(gtilde_sanitized) == 0:
            raise ValueError("After sanitization, gtilde has 0 genes intersecting adata.var_names.")

        self.G_all = list(gtilde_sanitized)

        # Sanitize prior_subsets to only include genes in G_all, drop empties
        self.prior_subsets = None
        if prior_subsets:
            Gall_set = set(self.G_all)
            pri = {}
            for name, genes in prior_subsets.items():
                if genes is None:
                    continue
                # preserve order + dedupe inside each prior
                seen2 = set()
                filtered = []
                for g in genes:
                    if g in seen2:
                        continue
                    seen2.add(g)
                    if g in Gall_set:
                        filtered.append(g)
                if len(filtered) > 0:
                    pri[name] = filtered
            self.prior_subsets = pri if len(pri) > 0 else None

        # Ensure K_target feasible
        self.K_target = int(min(self.K_target, len(self.G_all)))
        if self.K_target < 1:
            raise ValueError("K_target must be >= 1 after sanitization.")

        # ---------------------------------------------------------------------
        # CEM hyperparameters/state
        # ---------------------------------------------------------------------
        # Probability vector over genes used for without-replacement sampling.
        self.cem_M = 32      # population size per epoch (can be overridden via reward_kwargs)
        self.cem_rho = 0.2   # elite fraction per epoch (can be overridden via reward_kwargs)
        self.p_min = 1e-4
        self.p_max = 1.0 - 1e-4

        # Temperature for Boltzmann exploration over p
        self.T = 1.0
        self.T_min = 0.5
        self.T_max = 3.0

        # Used for adaptive alpha/rho based on reward spread
        self.last_reward_spread = None
        self.last_best_R = -np.inf

        # Initialize p using priors (if available), else uniform
        self.p = np.ones(len(self.G_all), dtype=np.float32)
        if self.prior_subsets:
            prior_union = []
            for _, genes in self.prior_subsets.items():
                prior_union.extend(genes[: self.K_target])
            prior_union = list(dict.fromkeys(prior_union))  # dedupe preserve order
            if len(prior_union) > 0:
                Gall_set = set(self.G_all)
                prior_union = [g for g in prior_union if g in Gall_set]
                if len(prior_union) > 0:
                    # bias priors moderately (logit-space update will keep stability)
                    for g in prior_union:
                        self.p[self.G_all.index(g)] *= 3.0
        self.p = self.p / self.p.sum()

        # Maintain logits for logit-space updates (stabilizes vs simplex averaging)
        self.logits = np.log(np.clip(self.p, self.p_min, 1.0))

        # ---------------------------------------------------------------------
        # Surrogate reward model (online linear ridge w/ diagonal uncertainty)
        # Used by explore() Stage-A acquisition (UCB) to rank cheap candidates.
        # ---------------------------------------------------------------------
        self.surr_lambda = 1.0
        self.surr_kappa = 1.0
        self.surr_w = np.zeros(len(self.G_all), dtype=np.float64)
        # Diagonal posterior precision accumulator for ridge: A = lambda*I + sum x_i^2
        self.surr_A_diag = (self.surr_lambda * np.ones(len(self.G_all), dtype=np.float64))
        self.surr_b = np.zeros(len(self.G_all), dtype=np.float64)
        self.surr_n = 0

        # Cache prior union indices for prior-preserving sampling
        self._prior_union_idx = None
        if self.prior_subsets:
            prior_union = []
            for _, genes in self.prior_subsets.items():
                prior_union.extend(genes)
            prior_union = list(dict.fromkeys(prior_union))
            self._prior_union_idx = [i for i, g in enumerate(self.G_all) if g in set(prior_union)]
            if len(self._prior_union_idx) == 0:
                self._prior_union_idx = None

        # Bookkeeping / history
        self.reward_history = []
        self.size_history = []
        self.eps_history = []
        self.epoch_rewards = []
        self.epoch_rewards_min = []
        self.epoch_rewards_max = []

        self.best_G = None
        self.best_R = -np.inf

        # Maintain current subset (best-so-far panel acts as "current")
        self.current_subset = set(self.rng.choice(self.G_all, self.K_target, replace=False))

        # Keep encoder training option (unchanged)
        self.ae_epochs = ae_epochs
        self.enc_opt = torch.optim.Adam(self.encoder.parameters(), lr=encoder_lr)

        # Keep these instantiated for backward compatibility, but they are unused by CEM
        self.state_dim = self.encoder.latent_dim
        self.n_genes = len(self.G_all)
        self.gene_to_idx = {g: i for i, g in enumerate(self.G_all)}
        self.actor = BatchedActor(self.n_genes, self.state_dim, hidden=(128, 32), device=self.device)
        self.critic = ScalarCritic(self.state_dim, hidden=(128, 64), device=self.device)
        self.opt_actor = torch.optim.Adam(self.actor.parameters(), lr=actor_lr)
        self.opt_critic = torch.optim.Adam(self.critic.parameters(), lr=critic_lr)

        # Backward-compat field (no longer used for exploration)
        self.eps = epsilon
        self.entropy_beta = entropy_beta
        self.entropy_beta_init = entropy_beta

        # Reward memoization (bounded FIFO eviction)
        self._reward_cache: Dict[Tuple[int, ...], float] = {}
        self._reward_cache_order = deque()
        self._reward_cache_max = int(max(256, memory_size))

        # CEM epoch cache
        self._cem_last_population = []
        self._cem_last_rewards = []
        self._cem_last_panels_idx = []

        # Preserve multiple elites between epochs
        self._elite_archive: List[List[str]] = []

    def _train_encoder(self, S: torch.Tensor, n_epochs: int = 5):
        self.encoder.train()
        if S.dim() == 1:
            S = S.unsqueeze(0)

        for _ in range(n_epochs):
            self.enc_opt.zero_grad()
            z, recon = self.encoder(S.squeeze(0))
            recon = recon.unsqueeze(0)
            loss = F.mse_loss(recon, S)
            loss.backward()
            self.enc_opt.step()

    def _extract_block(self, genes: List[str]) -> np.ndarray:
        return self.encoder._to_dense(self.adata[:, genes].X)

    def _encode_state(self, genes: List[str], train_encoder=False):
        X = self._extract_block(genes)
        S_np = self.encoder.compute_stats(X)
        S = torch.from_numpy(S_np).to(self.device, dtype=torch.float32)

        if train_encoder:
            self._train_encoder(S, n_epochs=self.ae_epochs)

        self.encoder.eval()
        with torch.no_grad():
            z = self.encoder.encode(S)
        return z

    def _panel_reward_idx(self, key: Tuple[int, ...], **reward_kwargs) -> float:
        """
        Index-based reward path.

        `key` must be the canonical representation: sorted, unique gene indices
        of length K_target. Uses `key` directly for memoization and converts to
        gene names only once when calling `reward_fn`.
        """
        if key in self._reward_cache:
            return float(self._reward_cache[key])

        genes_ordered = [self.G_all[i] for i in key]
        out = self.reward_fn(self.adata, genes_ordered, label_key=self.label_key, **reward_kwargs)
        r = float(out["reward"])

        # Insert into bounded FIFO cache
        self._reward_cache[key] = r
        self._reward_cache_order.append(key)
        while len(self._reward_cache_order) > self._reward_cache_max:
            old = self._reward_cache_order.popleft()
            self._reward_cache.pop(old, None)

        return r

    def _panel_reward(self, genes: List[str], **reward_kwargs):
        # Backward-compatible wrapper: normalize genes -> canonical index tuple
        idx = [self.gene_to_idx[g] for g in genes if g in self.gene_to_idx]
        key = tuple(sorted(set(idx)))
        return self._panel_reward_idx(key, **reward_kwargs)

    def _normalize_reward(self, r: float) -> float:
        return (r - self.reward_mean) / (self.reward_std + 1e-8)

    def _update_reward_stats(self, r: float):
        self.reward_mean = 0.99 * self.reward_mean + 0.01 * r
        self.reward_std = 0.99 * self.reward_std + 0.01 * abs(r - self.reward_mean)

    def _inject_knowledge(self, prior_subsets: Dict[str, List[str]], n_samples: int = 100):
        """Deprecated for PPO: kept for API compatibility."""
        logger.warning("_inject_knowledge is deprecated for PPO on-policy training; skipping.")
        return

    def explore(self, N_explore: int, **reward_kwargs) -> List[float]:
        """
        CEM exploration with a two-stage sampler to reduce expensive reward calls and
        increase search breadth:

          Stage A (cheap): generate a large candidate pool M_raw using fast sampling/mutation
            and rank by a proxy score:
              proxy = sum(logits[idx]) - lambda_overlap * max_jaccard(candidate, elites/best)
            (optionally) penalize excessive overlap with prior union after warmup.

          Stage B (expensive): evaluate reward_panel() only on:
            - top M_top by proxy
            - plus M_rand random from remainder
            - always include best_G and a few archive elites

        Also replaces without-replacement multinomial sampling with Gumbel-topK
        (Plackett–Luce) for faster, better behaved sampling.

        N_explore is interpreted as the expensive evaluation budget M (unless reward_kwargs
        overrides via `cem_M`).

        Returns:
            List of rewards for the evaluated population (size ~M).
        """
        # Expensive evaluation budget
        M = int(reward_kwargs.pop("cem_M", N_explore))
        rho = float(reward_kwargs.pop("cem_rho", self.cem_rho))
        M = max(2, M)
        rho = float(np.clip(rho, 0.05, 0.5))
        self.cem_rho = rho  # keep in sync for optimize()

        # Candidate pool size factor (two-stage exploration)
        raw_factor = float(np.clip(reward_kwargs.pop("cem_raw_factor", 6.0), 2.0, 15.0))
        M_raw = int(max(M * 2, round(raw_factor * M)))

        # Split between proxy-top and random remainder for expensive evaluation
        top_frac = float(np.clip(reward_kwargs.pop("cem_top_frac", 0.65), 0.3, 0.95))
        rand_frac = float(np.clip(reward_kwargs.pop("cem_rand_frac", 0.25), 0.0, 0.7))
        lambda_overlap = float(np.clip(reward_kwargs.pop("lambda_overlap", 1.5), 0.0, 10.0))

        # Strategy mixture hyperparams (kept internal to avoid public API changes)
        frac_local = float(np.clip(reward_kwargs.pop("frac_local", 0.45), 0.0, 0.9))
        frac_prior = float(np.clip(reward_kwargs.pop("frac_prior", 0.15), 0.0, 0.5))
        max_swaps = int(np.clip(reward_kwargs.pop("max_swaps", 5), 1, 25))
        # IMPORTANT: read once here; do not pop inside inner samplers (called repeatedly)
        prior_keep_frac = float(np.clip(reward_kwargs.pop("prior_keep_frac", 0.3), 0.2, 0.6))

        # Novelty controls vs elites/best (adaptive threshold based on last reward spread)
        last_spread = float(self.last_reward_spread) if self.last_reward_spread is not None else 0.0
        # If spread is low, enforce stronger novelty (lower overlap cap)
        if last_spread < 0.05:
            max_jaccard_accept = float(np.clip(reward_kwargs.pop("max_jaccard_accept", 0.90), 0.75, 0.98))
        elif last_spread > 0.15:
            max_jaccard_accept = float(np.clip(reward_kwargs.pop("max_jaccard_accept", 0.98), 0.85, 0.995))
        else:
            max_jaccard_accept = float(np.clip(reward_kwargs.pop("max_jaccard_accept", 0.95), 0.8, 0.99))

        # Penalize prior overlap after warmup to avoid lock-in (optional)
        warmup_epochs = int(np.clip(reward_kwargs.pop("prior_warmup_epochs", 5), 0, 1000))
        lambda_prior_overlap = float(np.clip(reward_kwargs.pop("lambda_prior_overlap", 0.25), 0.0, 5.0))
        penalize_prior = (len(self.reward_history) >= warmup_epochs) and (self._prior_union_idx is not None)

        rewards_collected: List[float] = []
        population: List[List[str]] = []

        # ------------------------------------------------------------------
        # Helpers operate on indices (sorted tuples) to avoid mapping overhead
        # ------------------------------------------------------------------
        evaluated_keys = set()
        candidate_keys = set()

        # Hoist temperature-scaled logits once per explore() call (Change 2)
        T_clipped = float(np.clip(self.T, self.T_min, self.T_max))
        scores_T = (self.logits.astype(np.float64, copy=False) / T_clipped)

        # Build reference sets for novelty checks (best + top few elites)
        ref_sets = []
        if self.best_G is not None and len(self.best_G) == self.K_target:
            best_idx = tuple(sorted(self.gene_to_idx[g] for g in self.best_G if g in self.gene_to_idx))
            if len(best_idx) == self.K_target:
                ref_sets.append(set(best_idx))

        # include only a few elites for speed
        elite_cap = 5
        for p in (self._elite_archive or [])[:elite_cap]:
            idx = tuple(sorted(self.gene_to_idx[g] for g in p if g in self.gene_to_idx))
            if len(idx) == self.K_target:
                ref_sets.append(set(idx))

        prior_set = set(self._prior_union_idx) if self._prior_union_idx is not None else None

        def _jaccard(a: Tuple[int, ...], b_set: set) -> float:
            if not a or not b_set:
                return 0.0
            inter = 0
            for x in a:
                if x in b_set:
                    inter += 1
            denom = (len(a) + len(b_set) - inter)
            return float(inter / denom) if denom > 0 else 0.0

        def _max_jaccard_to_refs(key: Tuple[int, ...]) -> float:
            if not ref_sets:
                return 0.0
            mj = 0.0
            for s in ref_sets:
                mj = max(mj, _jaccard(key, s))
            return float(mj)

        def _prior_jaccard(key: Tuple[int, ...]) -> float:
            if prior_set is None:
                return 0.0
            return float(_jaccard(key, prior_set))

        def _proxy_score(key: Tuple[int, ...]) -> float:
            # proxy = log-prob mass under current policy (logits sum)
            s = float(np.sum(self.logits[np.asarray(key, dtype=np.int64)]))
            mj = _max_jaccard_to_refs(key)
            s -= float(lambda_overlap) * mj
            if penalize_prior:
                s -= float(lambda_prior_overlap) * _prior_jaccard(key)
            return s

        # ------------------------------------------------------------------
        # Gumbel-topK sampler (Plackett–Luce)
        # ------------------------------------------------------------------
        def _gumbel_topk(scores_base: np.ndarray, k: int, mask: Optional[np.ndarray] = None) -> np.ndarray:
            # scores_base is float64 logits/T
            g = self.rng.gumbel(size=scores_base.shape[0]).astype(np.float64, copy=False)
            s = scores_base + g
            if mask is not None:
                s = s.copy()
                s[~mask] = -1e30
            idx = np.argpartition(s, -k)[-k:]
            idx = np.sort(idx)
            return idx

        def _sample_global_idx() -> np.ndarray:
            return _gumbel_topk(scores_T, self.K_target)

        def _sample_prior_preserving_idx() -> np.ndarray:
            keep = int(np.clip(int(round(prior_keep_frac * self.K_target)), 1, self.K_target))
            if self._prior_union_idx is None:
                return _sample_global_idx()

            prior_idx = np.asarray(self._prior_union_idx, dtype=np.int64)
            keep = min(keep, int(prior_idx.size))
            # Choose kept prior genes uniformly for stability (avoid reintroducing prior lock-in via logits)
            kept = self.rng.choice(prior_idx, size=keep, replace=False)

            rest = self.K_target - int(kept.size)
            if rest > 0:
                mask = np.ones(self.n_genes, dtype=bool)
                mask[kept] = False
                add = _gumbel_topk(scores_T, rest, mask=mask)
                idx = np.concatenate([kept, add]).astype(np.int64, copy=False)
            else:
                idx = kept.astype(np.int64, copy=False)

            idx = np.unique(idx)
            if idx.size != self.K_target:
                # repair
                missing = self.K_target - int(idx.size)
                mask = np.ones(self.n_genes, dtype=bool)
                mask[idx] = False
                add = _gumbel_topk(scores_T, missing, mask=mask)
                idx = np.unique(np.concatenate([idx, add]).astype(np.int64, copy=False))

            idx = np.sort(idx)[: self.K_target]
            return idx

        def _mutate_idx(idx_in: np.ndarray) -> np.ndarray:
            # swap-based local mutation using probability-biased remove + masked Gumbel add
            if idx_in.size != self.K_target:
                return _sample_global_idx()

            n_swaps = int(self.rng.integers(1, max_swaps + 1))

            # Maintain membership mask
            in_mask = np.zeros(self.n_genes, dtype=bool)
            in_mask[idx_in] = True

            # Work on a Python list to avoid per-swap array allocations
            idx_list = idx_in.tolist()

            for _ in range(n_swaps):
                if not idx_list:
                    break

                # Remove low-prob genes (use p)
                p_in = np.maximum(self.p[np.asarray(idx_list, dtype=np.int64)].astype(np.float64, copy=False), 1e-12)
                remove_w = (1.0 / p_in)
                remove_w_sum = float(remove_w.sum())
                if not np.isfinite(remove_w_sum) or remove_w_sum <= 0:
                    rem_pos = int(self.rng.integers(0, len(idx_list)))
                else:
                    remove_w = remove_w / remove_w_sum
                    rem_pos = int(self.rng.choice(len(idx_list), p=remove_w))
                rem_gene = int(idx_list[rem_pos])

                # swap-remove O(1)
                idx_list[rem_pos] = idx_list[-1]
                idx_list.pop()
                in_mask[rem_gene] = False

                # Add gene via masked Gumbel-top1 (directed + stochastic), reuse scores_T (Change 2)
                mask = ~in_mask
                add_gene = int(_gumbel_topk(scores_T, 1, mask=mask)[0])

                idx_list.append(add_gene)
                in_mask[add_gene] = True

            idx = np.unique(np.asarray(idx_list, dtype=np.int64))
            if idx.size != self.K_target:
                # repair to size K_target
                if idx.size > self.K_target:
                    idx = np.sort(idx)[: self.K_target]
                else:
                    missing = self.K_target - int(idx.size)
                    mask = np.ones(self.n_genes, dtype=bool)
                    mask[idx] = False
                    add = _gumbel_topk(scores_T, missing, mask=mask)
                    idx = np.unique(np.concatenate([idx, add]).astype(np.int64, copy=False))
                    idx = np.sort(idx)[: self.K_target]

            return np.sort(idx)[: self.K_target]

        def _try_add_candidate(key: Tuple[int, ...]) -> bool:
            if len(key) != self.K_target:
                return False
            if key in candidate_keys:
                return False

            # hard novelty rejection for near-duplicates
            mj = _max_jaccard_to_refs(key)
            if mj >= max_jaccard_accept:
                return False

            candidate_keys.add(key)
            return True

        # ------------------------------------------------------------------
        # Seed candidate pool with elites + best/current (always considered)
        # ------------------------------------------------------------------
        def _panel_from_current() -> Tuple[int, ...]:
            cur = [g for g in list(self.current_subset) if g in set(self.G_all)]
            cur = list(dict.fromkeys(cur))
            if len(cur) >= self.K_target:
                cur = cur[: self.K_target]
            else:
                missing = self.K_target - len(cur)
                complement = [g for g in self.G_all if g not in set(cur)]
                if missing > 0 and len(complement) > 0:
                    cur.extend(list(self.rng.choice(complement, min(missing, len(complement)), replace=False)))
            idx = [self.gene_to_idx[g] for g in cur if g in self.gene_to_idx]
            idx = sorted(set(idx))[: self.K_target]
            return tuple(idx)

        seed_keys: List[Tuple[int, ...]] = []
        # archive elites
        E_seed = max(2, int(0.1 * M))
        for p in (self._elite_archive or [])[:E_seed]:
            idx = tuple(sorted(self.gene_to_idx[g] for g in p if g in self.gene_to_idx))
            if len(idx) == self.K_target:
                seed_keys.append(idx)

        # best or current
        if self.best_G is not None and len(self.best_G) == self.K_target:
            idx = tuple(sorted(self.gene_to_idx[g] for g in self.best_G if g in self.gene_to_idx))
            if len(idx) == self.K_target:
                seed_keys.append(idx)
        else:
            seed_keys.append(_panel_from_current())

        for k in seed_keys:
            _try_add_candidate(k)

        # ------------------------------------------------------------------
        # Build candidate pool (cheap stage)
        # ------------------------------------------------------------------
        n_prior = int(round(frac_prior * M_raw)) if self._prior_union_idx is not None else 0
        n_local = int(round(frac_local * M_raw))
        n_global = max(0, M_raw - len(candidate_keys) - n_prior - n_local)

        # prior-preserving candidates
        for _ in range(max(0, n_prior)):
            if len(candidate_keys) >= M_raw:
                break
            idx = _sample_prior_preserving_idx()
            _try_add_candidate(tuple(idx.tolist()))

        # local mutation candidates around seed pool
        base_pool = seed_keys[:] if seed_keys else [tuple(_sample_global_idx().tolist())]
        for _ in range(max(0, n_local)):
            if len(candidate_keys) >= M_raw:
                break
            base = base_pool[int(self.rng.integers(0, len(base_pool)))]
            idx = _mutate_idx(np.asarray(base, dtype=np.int64))
            _try_add_candidate(tuple(idx.tolist()))

        # global fill candidates
        tries = 0
        while len(candidate_keys) < M_raw and tries < M_raw * 20:
            tries += 1
            idx = _sample_global_idx()
            _try_add_candidate(tuple(idx.tolist()))

        candidates = list(candidate_keys)
        # Ensure seeds included even if novelty rejection was strong
        for k in seed_keys:
            candidate_keys.add(k)
        candidates = list(candidate_keys)

        # ------------------------------------------------------------------
        # Select subset to evaluate expensively (vectorized proxy + soft sampling)
        # ------------------------------------------------------------------
        # Always include seeds in evaluated set
        for k in seed_keys:
            evaluated_keys.add(k)

        remaining = [k for k in candidates if k not in evaluated_keys]
        if remaining:
            # Vectorized acquisition scoring (surrogate UCB + penalties)
            cand = np.asarray(remaining, dtype=np.int64)  # (Nc, K)

            # Surrogate mean: mu = sum(w_i) over selected genes (K-hot linear model)
            mu = self.surr_w[cand].sum(axis=1).astype(np.float64, copy=False)

            # Surrogate uncertainty (diag approx): sigma = sqrt(sum(1/A_ii)) over selected genes
            # Note: A_diag = lambda + sum x_i^2, and x_i in {0,1} for K-hot -> sum of inverse diag
            invA = 1.0 / np.clip(self.surr_A_diag, 1e-12, np.inf)
            sigma = np.sqrt(invA[cand].sum(axis=1)).astype(np.float64, copy=False)

            # UCB acquisition
            kappa = float(np.clip(reward_kwargs.pop("surr_kappa", self.surr_kappa), 0.0, 50.0))
            acq = mu + kappa * sigma

            # novelty penalty via ref masks
            max_j = np.zeros(cand.shape[0], dtype=np.float64)
            if ref_sets:
                for s in ref_sets:
                    ref_mask = np.zeros(self.n_genes, dtype=bool)
                    ref_mask[list(s)] = True
                    inter = ref_mask[cand].sum(axis=1).astype(np.float64, copy=False)
                    denom = (float(self.K_target) + float(len(s)) - inter)
                    j = np.divide(inter, denom, out=np.zeros_like(inter), where=(denom > 0))
                    max_j = np.maximum(max_j, j)

            proxy_vals = acq - float(lambda_overlap) * max_j

            # optional prior overlap penalty
            if penalize_prior and prior_set is not None and len(prior_set) > 0:
                prior_mask = np.zeros(self.n_genes, dtype=bool)
                prior_mask[list(prior_set)] = True
                interp = prior_mask[cand].sum(axis=1).astype(np.float64, copy=False)
                denom_p = (float(self.K_target) + float(len(prior_set)) - interp)
                jp = np.divide(interp, denom_p, out=np.zeros_like(interp), where=(denom_p > 0))
                proxy_vals = proxy_vals - float(lambda_prior_overlap) * jp

            # Soft (Boltzmann) selection without replacement based on acquisition/proxy
            budget_left = max(0, M - len(evaluated_keys))
            if budget_left > 0:
                # Adaptive tau: low spread -> flatter (more exploration); high spread -> sharper.
                spread = float(self.last_reward_spread) if self.last_reward_spread is not None else 0.0
                if spread < 0.05:
                    tau_proxy = float(np.clip(reward_kwargs.pop("tau_proxy", 3.0), 1.0, 20.0))
                elif spread > 0.15:
                    tau_proxy = float(np.clip(reward_kwargs.pop("tau_proxy", 1.0), 0.5, 10.0))
                else:
                    tau_proxy = float(np.clip(reward_kwargs.pop("tau_proxy", 1.8), 0.5, 15.0))

                x = (proxy_vals - float(np.max(proxy_vals))) / float(max(1e-6, tau_proxy))
                w = np.exp(np.clip(x, -50.0, 0.0)).astype(np.float64)
                w_sum = float(w.sum())
                if not np.isfinite(w_sum) or w_sum <= 0:
                    p_sel = None
                else:
                    p_sel = (w / w_sum)

                n_pick = min(int(budget_left), int(len(remaining)))
                if n_pick > 0:
                    picked = self.rng.choice(len(remaining), size=n_pick, replace=False, p=p_sel).tolist()
                    for i in picked:
                        evaluated_keys.add(remaining[i])
        else:
            proxy_vals = np.asarray([], dtype=np.float64)

        # If still under budget (e.g., not enough remaining), fill deterministically
        budget_left = max(0, M - len(evaluated_keys))
        if budget_left > 0 and remaining:
            for k in remaining:
                if len(evaluated_keys) >= M:
                    break
                evaluated_keys.add(k)

        # ------------------------------------------------------------------
        # Evaluate expensive rewards (index-based path; Change 1)
        # ------------------------------------------------------------------
        eval_list = list(evaluated_keys)
        population = []
        for key in eval_list:
            r = self._panel_reward_idx(key, **reward_kwargs)
            rewards_collected.append(float(r))

            if r > self.best_R:
                self.best_R = float(r)
                genes = [self.G_all[i] for i in key]
                self.best_G = list(genes)
                self.current_subset = set(genes)

            # store population as gene lists for archive / backward compatibility
            population.append([self.G_all[i] for i in key])

        # Cache for optimize() CEM update
        self._cem_last_population = population
        self._cem_last_rewards = rewards_collected
        self._cem_last_panels_idx = [list(k) for k in eval_list]

        # Track reward spread for adaptive temperature/alpha/rho
        if rewards_collected:
            spread = float(np.max(rewards_collected) - np.min(rewards_collected))
            self.last_reward_spread = spread

            # Stronger adaptive temperature "kick" when spread is low
            if spread < 0.05:
                self.T = float(np.clip(self.T * 1.5, self.T_min, self.T_max))
            elif spread > 0.15:
                self.T = float(np.clip(self.T * 0.9, self.T_min, self.T_max))

            # Preserve top-E elites into archive for next epoch
            topE = max(2, int(0.1 * len(population)))
            elite_idx = np.argsort(-np.asarray(rewards_collected, dtype=np.float32))[:topE].tolist()
            self._elite_archive = [population[i] for i in elite_idx]

        return rewards_collected

    def optimize(self, N_optimize: int):
        """
        CEM optimization:
          - Reward-weighted (softmax) frequency updates (uses magnitude info)
          - Logit-space smoothing: logits <- (1-a)*logits + a*log(freq+eps)
          - Adaptive alpha/rho based on reward spread and best improvement
        """
        if not getattr(self, "_cem_last_population", None) or not getattr(self, "_cem_last_rewards", None):
            return
        if len(self._cem_last_population) != len(self._cem_last_rewards):
            return

        rewards = np.asarray(self._cem_last_rewards, dtype=np.float32)
        M = len(rewards)
        if M < 2:
            return

        # Prefer cached indices from explore()
        if getattr(self, "_cem_last_panels_idx", None) and len(self._cem_last_panels_idx) == M:
            idx_pop = [list(x) for x in self._cem_last_panels_idx]
        else:
            pop = self._cem_last_population
            idx_pop = []
            for panel in pop:
                idx = [self.gene_to_idx[g] for g in panel if g in self.gene_to_idx]
                idx = sorted(set(idx))
                if len(idx) != self.K_target:
                    # repair
                    if len(idx) > self.K_target:
                        idx = idx[: self.K_target]
                    else:
                        missing = self.K_target - len(idx)
                        complement = [i for i in range(self.n_genes) if i not in set(idx)]
                        if missing > 0 and len(complement) > 0:
                            idx.extend(self.rng.choice(complement, min(missing, len(complement)), replace=False).tolist())
                        idx = sorted(set(idx))[: self.K_target]
                idx_pop.append(idx)

        # Adaptive rho and alpha based on reward spread and improvement
        spread = float(self.last_reward_spread) if self.last_reward_spread is not None else float(np.max(rewards) - np.min(rewards))
        base_rho = float(np.clip(self.cem_rho, 0.05, 0.5))
        if spread < 0.05:
            rho = float(np.clip(base_rho * 1.25, 0.1, 0.5))
            alpha = float(np.clip(self.alpha * 0.85, 0.02, 0.95))
        elif spread > 0.15:
            rho = float(np.clip(base_rho * 0.9, 0.05, 0.4))
            alpha = float(np.clip(self.alpha * 1.05, 0.05, 0.98))
        else:
            rho = base_rho
            alpha = float(np.clip(self.alpha, 0.02, 0.98))

        # Encourage stronger updates when best improves
        if self.best_R > self.last_best_R + 1e-6:
            alpha = float(np.clip(alpha * 1.05, 0.02, 0.98))
        self.last_best_R = float(self.best_R)
        self.cem_rho = rho

        n_elite = max(2, int(math.ceil(rho * M)))

        # Sort by reward descending, take elites
        elite_order = np.argsort(-rewards)[:n_elite]
        elite_rewards = rewards[elite_order]

        # Robust elite weighting under low reward spread: rank-based (optionally mixed with magnitude).
        use_rank_weights = True

        if use_rank_weights:
            # ranks are 0..n_elite-1 in elite_order (already sorted by reward desc)
            ranks = np.arange(n_elite, dtype=np.float64)
            tau_rank = float(np.clip(3.0, 1.0, 25.0))
            w = np.exp(-ranks / tau_rank).astype(np.float64)
            w_sum = float(w.sum())
            if not np.isfinite(w_sum) or w_sum <= 0:
                w = np.ones_like(w) / float(len(w))
            else:
                w = w / w_sum
        else:
            # Magnitude-based softmax on rewards (kept for reference)
            std_r = float(np.std(elite_rewards))
            tau_r = float(np.clip(std_r, 0.01, 0.2))
            r0 = elite_rewards - float(np.max(elite_rewards))
            w = np.exp(np.clip(r0 / tau_r, -50.0, 0.0)).astype(np.float64)
            w_sum = float(w.sum())
            if not np.isfinite(w_sum) or w_sum <= 0:
                w = np.ones_like(w) / float(len(w))
            else:
                w = w / w_sum

        # Compute weighted frequencies across elites (vectorized)
        elite_panels = np.asarray([idx_pop[j] for j in elite_order.tolist()], dtype=np.int64)
        flat_idx = elite_panels.ravel()
        weights_rep = np.broadcast_to(w[:, None], elite_panels.shape).ravel()
        freq = np.bincount(flat_idx, weights=weights_rep, minlength=self.n_genes).astype(np.float64)

        freq = np.clip(freq, self.p_min, None)
        freq = freq / freq.sum()

        # Perform potentially multiple smoothing steps against the same weighted target
        target_logits = np.log(np.clip(freq, self.p_min, 1.0))
        for _ in range(max(1, N_optimize)):
            self.logits = (1.0 - alpha) * self.logits + alpha * target_logits

        # Compute p once after smoothing loop (Change 5)
        z = self.logits - np.max(self.logits)
        p = np.exp(np.clip(z, -50.0, 50.0)).astype(np.float64)
        p_sum = float(p.sum())
        if not np.isfinite(p_sum) or p_sum <= 0:
            p = np.ones(self.n_genes, dtype=np.float64) / float(self.n_genes)
        else:
            p = p / p_sum

        # Clamp/renormalize, then refresh logits to match clamped distribution
        p = np.clip(p, self.p_min, None)
        p = p / p.sum()
        self.p = p.astype(np.float32)
        self.logits = np.log(np.clip(self.p, self.p_min, 1.0))

    def train(self, epochs: int, N_explore: int = 10, N_optimize: int = 5, verbose: bool = True, **reward_kwargs) -> Dict:
        """
        Main training loop (CEM).

        Notes:
          - Avoid redundant per-epoch reward evaluation of current_subset; explore()
            already includes best/current in the population.
          - Reward memoization reduces repeated expensive clustering calls.
        """
        for epoch in tqdm(range(epochs)):
            rewards_epoch = self.explore(N_explore, **reward_kwargs)

            # For history/logging, use the reward of the injected best/current panel:
            # explore() seeds population with (elite archive ...) + best/current early;
            # we can't assume position 0 is best/current due to archive, so fall back.
            if rewards_epoch:
                self.epoch_rewards.append(float(np.mean(rewards_epoch)))
                self.epoch_rewards_min.append(float(np.min(rewards_epoch)))
                self.epoch_rewards_max.append(float(np.max(rewards_epoch)))

            # Track reward/size using best-so-far (stable, no extra clustering call)
            R = float(self.best_R) if np.isfinite(self.best_R) else 0.0
            genes = list(self.best_G) if self.best_G is not None else list(self.current_subset)

            self.reward_history.append(float(R))
            self.size_history.append(len(genes))

            # eps_history kept for backward compatibility; track entropy of p
            p_safe = np.clip(self.p, 1e-12, 1.0)
            self.eps_history.append(float(-(p_safe * np.log(p_safe)).sum()))

            # Optimize (CEM update)
            self.optimize(N_optimize)

            if verbose:
                logger.info(
                    f"Epoch {epoch + 1}/{epochs} | M={int(reward_kwargs.get('cem_M', N_explore))} | |G|={len(genes)} | best={self.best_R:.4f} | spread={float(self.last_reward_spread) if self.last_reward_spread is not None else 0.0:.4f} | T={self.T:.3f} | rho={self.cem_rho:.3f}"
                )

        return {
            "best_panel": self.best_G,
            "best_reward": self.best_R,
            "training_history": {
                "reward_history": self.reward_history,
                "size_history": self.size_history,
                "eps_history": self.eps_history,
                "epoch_rewards": self.epoch_rewards,
            }
        }


# =============================================================================
# PUBLIC API
# =============================================================================

def train_gene_panel_selector(
    adata,
    gtilde: List[str],
    prior_subsets: Optional[Dict[str, List[str]]] = None,
    label_key: str = "cell_type",
    K_target: int = 500,
    K_max: int = 1000,
    epochs: int = 50,
    N_explore: int = 12,
    N_optimize: int = 8,
    verbose: bool = True,
    **reward_kwargs
) -> Dict:
    """
    Train an RL-based gene panel selector.

    This is the PUBLIC API that should remain stable during evolution.

    Args:
        adata: AnnData object with expression data and cell type labels
        gtilde: List of candidate genes to select from
        prior_subsets: Optional dict mapping method names to gene lists for knowledge injection
        label_key: Column in adata.obs containing cell type labels
        K_target: Target panel size
        K_max: Maximum panel size
        epochs: Number of training epochs
        N_explore: Exploration steps per epoch
        N_optimize: Optimization steps per epoch
        verbose: Whether to print progress
        **reward_kwargs: Additional arguments for reward function (alpha, beta, etc.)

    Returns:
        Dictionary containing:
        - best_panel: List of genes in the best panel found
        - best_reward: Reward of the best panel
        - training_history: Dict with reward curves and training statistics
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    encoder = StateEncoder(latent_dim=64, device=device)

    trainer = SmartCurationTrainer(
        adata=adata,
        gtilde=gtilde,
        encoder=encoder,
        reward_fn=reward_panel,
        label_key=label_key,
        prior_subsets=prior_subsets,
        K_target=K_target,
        K_max=K_max,
        device=device,
    )

    result = trainer.train(
        epochs=epochs,
        N_explore=N_explore,
        N_optimize=N_optimize,
        verbose=verbose,
        **reward_kwargs
    )

    return result
