# File: rl_gene_panel.py
"""
Gene Panel Selection Algorithm (CEM / elite-selection).

This module implements a distribution-search (Cross-Entropy Method style)
approach for selecting optimal gene panels from single-cell RNA sequencing data.

Key changes vs prior RL actor-critic version:
- Treat panel search as black-box combinatorial optimization / contextual bandit
- Sample full panels from a top-K without-replacement distribution (Plackett–Luce style)
- Update per-gene logits by maximum-likelihood on elite panels (cross-entropy update)
- Remove TD bootstrapping / critic dependency to avoid policy/objective mismatch

Evolution Targets:
1. reward_panel() - Reward function with ARI+NMI+SI-like proxy + size penalty
2. SmartCurationTrainer.explore() - Panel sampling strategy (consistent with update)
3. SmartCurationTrainer.optimize() - Elite-selection / CEM update over panels
"""

import math
import random
from collections import deque
from typing import Dict, List, Optional

import numpy as np
import scipy.sparse as sp
import torch
import torch.nn as nn
import torch.nn.functional as F
from tqdm import tqdm
import logging

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


class BatchedCritic(nn.Module):
    """All gene critics in a single batched network."""

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


# =============================================================================
# REPLAY BUFFER
# =============================================================================

class BatchedReplayBuffer:
    """Single buffer storing evaluated panel samples for CEM/PPO-style updates."""

    def __init__(self, capacity=2000):
        self.buf = deque(maxlen=capacity)

    def push(self, S_t, actions_vec, r_t, S_tp1, logp_old: float = 0.0, chosen_idx=None):
        """
        S_t: (state_dim,) tensor - current state
        actions_vec: (n_genes,) tensor - 1 if gene included, 0 otherwise
        r_t: float - reward for this transition
        S_tp1: (state_dim,) tensor - next state
        logp_old: float - log-probability of sampled panel under behavior policy
        chosen_idx: optional (K,) indices (int64) of selected genes (CPU/GPU ok; stored on CPU)
        """
        if chosen_idx is not None:
            if isinstance(chosen_idx, np.ndarray):
                chosen_idx = torch.from_numpy(chosen_idx)
            if torch.is_tensor(chosen_idx):
                chosen_idx = chosen_idx.detach().to("cpu", dtype=torch.long).contiguous()
            else:
                chosen_idx = torch.tensor(list(chosen_idx), dtype=torch.long)

        self.buf.append((
            S_t.detach().cpu().float(),
            actions_vec.detach().cpu().float(),
            float(r_t),
            S_tp1.detach().cpu().float(),
            float(logp_old),
            chosen_idx,
        ))

    def sample(self, batch_size=64, device=None):
        batch = random.sample(self.buf, k=min(batch_size, len(self.buf)))
        S_t, actions, r_t, S_tp1, logp_old, chosen_idx = zip(*batch)
        device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return (
            torch.stack(S_t).to(device),
            torch.stack(actions).to(device),
            torch.tensor(r_t, dtype=torch.float32, device=device).unsqueeze(1),
            torch.stack(S_tp1).to(device),
            torch.tensor(logp_old, dtype=torch.float32, device=device).unsqueeze(1),
            list(chosen_idx),
        )

    def __len__(self):
        return len(self.buf)


# =============================================================================
# REWARD FUNCTION (Tertiary Evolution Target)
# =============================================================================

# Try to import GPU-accelerated rapids_singlecell, fall back to scanpy
try:
    import rapids_singlecell as rsc
    _USE_GPU = True
except ImportError:
    _USE_GPU = False


def reward_panel(
    adata,
    genes: List[str],
    label_key: str = 'cell_type',
    *,
    n_neighbors: int = 15,
    resolution: float = 1.0,
    n_pcs: int = 50,
    # kept for backward-compatibility with callers; no longer used as ARI/size mixing weight
    alpha: float = 0.8,
    K_target: int = 500,
    K_max: int = 1000,
    beta: float = 1.5,
    # reward shaping / weights
    w_ari: float = 0.45,
    w_nmi: float = 0.45,
    w_si: float = 0.10,
    quality_power: float = 2.0,
    size_lambda: float = 0.25,
) -> Dict:
    """
    Evaluate a candidate gene panel by clustering performance and size compliance.
    Uses GPU-accelerated rapids_singlecell if available, otherwise falls back to scanpy.

    Reward now aligns to multi-metric clustering quality (ARI + NMI + SI-like proxy),
    with nonlinear shaping and a soft size penalty:
        reward = quality - size_lambda * penalty(size)

    Returns:
        Dictionary with reward, ari, nmi, si, size_term, size_penalty, and num_genes
    """
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, silhouette_score

    genes = [g for g in genes if g in adata.var_names]
    K = len(genes)

    if K < 10:
        return dict(reward=0.0, ari=0.0, nmi=0.0, si=0.0, size_term=0.0, size_penalty=1.0, num_genes=K)

    ad = adata[:, genes].copy()

    n_comps = min(n_pcs, K - 1, ad.n_obs - 1)

    if _USE_GPU:
        # GPU-accelerated path using rapids_singlecell
        rsc.pp.pca(ad, n_comps=n_comps)
        rsc.pp.neighbors(ad, n_neighbors=n_neighbors)
        rsc.tl.leiden(ad, resolution=resolution)
    else:
        # CPU fallback using scanpy
        import scanpy as sc
        sc.pp.pca(ad, n_comps=n_comps)
        sc.pp.neighbors(ad, n_neighbors=n_neighbors, use_rep='X_pca')
        sc.tl.leiden(ad, resolution=resolution, random_state=0)

    clusters = ad.obs['leiden']
    true = ad.obs[label_key]

    ari = float(adjusted_rand_score(true, clusters))
    nmi = float(normalized_mutual_info_score(true, clusters))

    # SI-like term: silhouette score on PCA embedding (range [-1, 1]) -> map to [0, 1]
    si = 0.0
    try:
        if getattr(ad, "obsm", None) is not None and "X_pca" in ad.obsm and len(np.unique(clusters)) > 1:
            Xp = ad.obsm["X_pca"]
            si_raw = float(silhouette_score(Xp, clusters))
            si = max(0.0, min(1.0, (si_raw + 1.0) / 2.0))
    except Exception:
        si = 0.0

    def _shape(x: float) -> float:
        x = max(0.0, min(1.0, float(x)))
        return x ** quality_power

    quality = (
        w_ari * _shape(ari) +
        w_nmi * _shape(nmi) +
        w_si * _shape(si)
    )

    if K <= K_target:
        size_term = 1.0
    elif K_target < K <= K_max:
        size_term = (1 - (K - K_target) / (K_max - K_target)) ** beta
    else:
        size_term = 0.0

    # penalty in [0,1], 0 when within target, 1 when beyond max
    size_penalty = 1.0 - float(size_term)

    reward = float(quality - size_lambda * size_penalty)

    return dict(
        reward=reward,
        ari=ari,
        nmi=nmi,
        si=si,
        size_term=float(size_term),
        size_penalty=float(size_penalty),
        num_genes=K,
    )


# =============================================================================
# RL TRAINER
# =============================================================================

class SmartCurationTrainer:
    """
    Trainer for gene panel selection using distribution search (CEM / elite-selection):
    - Fixed-size state representation (still used for optional conditioning / future extensions)
    - Sample full panels from a top-K without-replacement distribution induced by logits
    - Update sampling distribution by KL-regularized CEM (trust-region on per-gene marginals)
    - No TD bootstrapping / critic required (critic left instantiated for API compatibility)
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
        # CEM / elite-selection params
        cem_population: int = 32,
        cem_elite_frac: float = 0.2,
        cem_lr: float = 0.35,
        cem_tau_start: float = 1.5,
        cem_tau_end: float = 0.4,
        # PPO-style stabilization params (kept for backward-compatibility; unused by KL-CEM)
        ppo_clip: float = 0.2,
        entropy_coef: float = 0.00,
        # Two-stage racing / proxy-eval params
        race_pool_mult: int = 6,
        race_top_frac: float = 0.35,
        proxy_n_pcs: int = 25,
        # KL-regularized CEM params
        cem_kl_target: float = 0.05,
        cem_kl_backtrack: float = 0.5,
        # Surrogate-assisted multi-fidelity search params
        use_surrogate: bool = True,
        surrogate_hidden: int = 256,
        surrogate_lr: float = 1e-3,
        surrogate_steps: int = 200,
        surrogate_dropout_T: int = 8,
        surrogate_ucb_lambda: float = 0.5,
        surrogate_warmup: int = 128,
        # Elitism during exploration: always evaluate incumbent/best-so-far
        always_eval_best: bool = True,
    ):
        self.adata = adata
        self.K_target = K_target
        self.K_max = K_max
        self.G_all = list(gtilde)
        self.encoder = encoder
        self.reward_fn = reward_fn
        self.label_key = label_key
        self.alpha = alpha
        self.gamma = gamma
        self.minibatch = minibatch
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.rng = rng or np.random.default_rng(42)

        self.reward_history = []
        self.size_history = []
        self.eps_history = []
        self.epoch_rewards = []
        self.epoch_rewards_min = []
        self.epoch_rewards_max = []

        self.reward_mean = 0.5
        self.reward_std = 0.2

        self.best_G = None
        self.best_R = -np.inf

        self.ae_epochs = ae_epochs
        self.entropy_beta = entropy_beta

        # CEM settings
        self.cem_population = int(cem_population)
        self.cem_elite_frac = float(cem_elite_frac)
        self.cem_lr = float(cem_lr)
        self.cem_tau_start = float(cem_tau_start)
        self.cem_tau_end = float(cem_tau_end)
        self.cem_tau = float(cem_tau_start)

        # KL-regularized CEM settings
        self.cem_kl_target = float(cem_kl_target)
        self.cem_kl_backtrack = float(cem_kl_backtrack)

        # PPO params kept but unused
        self.ppo_clip = float(ppo_clip)
        self.entropy_coef = float(entropy_coef)

        # Racing / proxy evaluation
        self.race_pool_mult = int(race_pool_mult)
        self.race_top_frac = float(race_top_frac)
        self.proxy_n_pcs = int(proxy_n_pcs)

        # Surrogate-assisted search
        self.use_surrogate = bool(use_surrogate)
        self.surrogate_hidden = int(surrogate_hidden)
        self.surrogate_lr = float(surrogate_lr)
        self.surrogate_steps = int(surrogate_steps)
        self.surrogate_dropout_T = int(surrogate_dropout_T)
        self.surrogate_ucb_lambda = float(surrogate_ucb_lambda)
        self.surrogate_warmup = int(surrogate_warmup)
        self.always_eval_best = bool(always_eval_best)

        if prior_subsets:
            best_prior = max(
                prior_subsets.items(),
                key=lambda x: self.reward_fn(adata, x[1], label_key=label_key)['reward']
            )
            self.current_subset = set(best_prior[1][:K_target])
        else:
            self.current_subset = set(self.rng.choice(self.G_all, K_target, replace=False))

        self.enc_opt = torch.optim.Adam(self.encoder.parameters(), lr=encoder_lr)

        self.state_dim = self.encoder.latent_dim
        self.n_genes = len(self.G_all)
        self.gene_to_idx = {g: i for i, g in enumerate(self.G_all)}

        # Keep neural actor for API compatibility, but KL-CEM updates a standalone logit table.
        self.actor = BatchedActor(self.n_genes, self.state_dim, hidden=(128, 32), device=self.device)
        # Critic kept for backwards compatibility; unused by CEM update.
        self.critic = BatchedCritic(self.n_genes, self.state_dim, hidden=(128, 32), device=self.device)

        self.opt_actor = torch.optim.Adam(self.actor.parameters(), lr=actor_lr)
        self.opt_critic = torch.optim.Adam(self.critic.parameters(), lr=critic_lr)

        self.buffer = BatchedReplayBuffer(capacity=memory_size)

        self.eps = epsilon

        # Standalone distribution parameters (per-gene logits) used for sampling/updating.
        self.panel_logits = torch.zeros(self.n_genes, dtype=torch.float32, device=self.device)

        # Reward memoization cache (avoid repeated expensive clustering)
        self._reward_cache = {}
        self._reward_cache_order = deque()
        self._reward_cache_max = 512

        # Surrogate model (trained on replay buffer): index-pooled embedding -> MLP -> scalar
        emb_dim = int(max(16, min(128, self.surrogate_hidden // 4)))
        self.surrogate_emb_dim = emb_dim
        self.surrogate_emb = nn.Embedding(self.n_genes, emb_dim).to(self.device)
        self.surrogate_head = nn.Sequential(
            nn.Linear(emb_dim, self.surrogate_hidden),
            nn.ReLU(),
            nn.Dropout(0.15),
            nn.Linear(self.surrogate_hidden, self.surrogate_hidden // 2),
            nn.ReLU(),
            nn.Dropout(0.15),
            nn.Linear(self.surrogate_hidden // 2, 1),
        ).to(self.device)

        self.opt_surrogate = torch.optim.Adam(
            list(self.surrogate_emb.parameters()) + list(self.surrogate_head.parameters()),
            lr=self.surrogate_lr
        )

        # Adaptive surrogate training schedule
        self._surrogate_train_calls = 0
        self.surrogate_train_every = 2

        if prior_subsets is not None:
            self._inject_knowledge(prior_subsets)

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

    def _panel_reward(self, genes: List[str], **reward_kwargs):
        # Memoize by sorted integer indices to avoid repeated expensive clustering computations
        idx = []
        for g in genes:
            i = self.gene_to_idx.get(g, None)
            if i is not None:
                idx.append(int(i))
        key = tuple(sorted(idx))

        cached = self._reward_cache.get(key, None)
        if cached is not None:
            return float(cached)

        out = self.reward_fn(self.adata, genes, label_key=self.label_key, **reward_kwargs)
        r = float(out["reward"])

        # LRU-style insert
        self._reward_cache[key] = r
        self._reward_cache_order.append(key)
        if len(self._reward_cache_order) > self._reward_cache_max:
            old = self._reward_cache_order.popleft()
            self._reward_cache.pop(old, None)

        return r

    def _normalize_reward(self, r: float) -> float:
        return (r - self.reward_mean) / (self.reward_std + 1e-8)

    def _update_reward_stats(self, r: float):
        self.reward_mean = 0.99 * self.reward_mean + 0.01 * r
        self.reward_std = 0.99 * self.reward_std + 0.01 * abs(r - self.reward_mean)

    def _inject_knowledge(self, prior_subsets: Dict[str, List[str]], n_samples: int = 100):
        """Inject knowledge from prior gene selection methods into replay buffer."""
        S0 = self._encode_state(list(self.current_subset))

        prepared = []
        for name, Gf in prior_subsets.items():
            Gf_set = set(Gf)
            Sf = self._encode_state(Gf)
            r_f = self._panel_reward(Gf)
            prepared.append((Gf_set, Sf, r_f))

        total = len(prior_subsets) * n_samples

        for i in range(total):
            idx = i % len(prepared)
            Gf_set, Sf, r_f = prepared[idx]
            r_norm = self._normalize_reward(r_f)

            actions_vec = torch.zeros(self.n_genes, device=self.device)
            for g in Gf_set:
                if g in self.gene_to_idx:
                    actions_vec[self.gene_to_idx[g]] = 1.0

            # logp_old unknown for injected samples; set to 0 and they will have limited effect under PPO ratio.
            self.buffer.push(S0, actions_vec, r_norm, Sf, logp_old=0.0)

    def _train_surrogate(self, steps: Optional[int] = None, batch_size: int = 128):
        """Train surrogate reward model on replay buffer (predicts normalized reward)."""
        if not self.use_surrogate:
            return
        if len(self.buffer) < max(16, self.surrogate_warmup):
            return

        self._surrogate_train_calls += 1
        if (self._surrogate_train_calls % int(max(1, self.surrogate_train_every))) != 0:
            return

        # Adaptive budget
        if steps is None:
            steps = min(self.surrogate_steps, 50 + len(self.buffer) // 20)
        steps = int(max(10, steps))

        self.surrogate_emb.train()
        self.surrogate_head.train()

        ema = None
        for _ in range(steps):
            S_t, actions, r_t, S_tp1, logp_old, chosen_idx_list = self.buffer.sample(batch_size, self.device)
            y = r_t  # (B,1)

            # Prefer stored indices; fallback by reconstructing from dense actions (for injected priors)
            idx_rows = []
            for b, ci in enumerate(chosen_idx_list):
                if ci is not None:
                    idx_rows.append(ci)
                else:
                    nz = torch.nonzero(actions[b] > 0.5, as_tuple=False).squeeze(1).detach().to("cpu", dtype=torch.long)
                    idx_rows.append(nz)

            # Build padded index matrix (B, Kmax)
            B = len(idx_rows)
            Kmax = max(1, max(int(t.numel()) for t in idx_rows))
            idx_mat = torch.zeros(B, Kmax, dtype=torch.long, device=self.device)
            mask = torch.zeros(B, Kmax, dtype=torch.float32, device=self.device)
            for b, t in enumerate(idx_rows):
                k = int(t.numel())
                if k == 0:
                    continue
                tt = t.to(self.device, dtype=torch.long)
                idx_mat[b, :k] = tt
                mask[b, :k] = 1.0

            self.opt_surrogate.zero_grad()
            E = self.surrogate_emb(idx_mat)  # (B,K,D)
            denom = mask.sum(dim=1, keepdim=True).clamp_min(1.0)
            pooled = (E * mask.unsqueeze(-1)).sum(dim=1) / denom  # (B,D)
            pred = self.surrogate_head(pooled)  # (B,1)

            loss = F.mse_loss(pred, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                list(self.surrogate_emb.parameters()) + list(self.surrogate_head.parameters()),
                max_norm=5.0
            )
            self.opt_surrogate.step()

            # early stop if plateau
            l = float(loss.detach().item())
            ema = l if ema is None else (0.95 * ema + 0.05 * l)
            if ema is not None and l > ema * 0.999:
                # very mild stop criterion to avoid overtraining
                break

    @torch.no_grad()
    def _surrogate_score_ucb(self, idx_mat: torch.Tensor, T: int = 8, ucb_lambda: float = 0.5):
        """
        Dropout-based uncertainty (MC dropout) for index-pooled embedding surrogate:
        returns mean + lambda*std for each candidate.
        """
        if not self.use_surrogate:
            return None
        if len(self.buffer) < max(16, self.surrogate_warmup):
            return None

        T = int(max(1, T))
        ucb_lambda = float(ucb_lambda)

        # enable dropout at inference
        self.surrogate_emb.train()
        self.surrogate_head.train()

        E = self.surrogate_emb(idx_mat)  # (B,K,D)
        pooled = E.mean(dim=1)  # (B,D)

        preds = []
        for _ in range(T):
            preds.append(self.surrogate_head(pooled).squeeze(1))  # (B,)
        P = torch.stack(preds, dim=0)  # (T,B)
        mu = P.mean(dim=0)
        sd = P.std(dim=0, unbiased=False)
        return (mu + ucb_lambda * sd).detach()

    def explore(self, N_explore: int, **reward_kwargs) -> List[float]:
        """
        Exploration step: sample a *population* of panels from the current logits-induced
        distribution using Gumbel-TopK (one-shot without-replacement sampling).

        Changes:
        - Sampling uses Gumbel-TopK in torch on self.device (major speedup vs np.delete loop).
        - Two-stage candidate filtering uses embedding-based surrogate on (B,K) indices (no dense actions_mat).
        - logp_old is an approximate log-prob (unused by KL-CEM optimize(); kept for buffer compatibility).
        - Elitism: always include incumbent/best-so-far for full evaluation (optional).
        """
        base_genes = list(self.current_subset)
        rewards_collected: List[float] = []

        # Encode one "context" state for the epoch (kept for buffer/API compatibility)
        S_ctx = self._encode_state(base_genes, train_encoder=True)

        @torch.no_grad()
        def _sample_panel_gumbel_topk(k: int, tau: float):
            """Sample panel indices + approximate log-prob via log_softmax logits."""
            k = int(min(max(k, 1), self.n_genes))
            tau = float(max(1e-6, tau))

            logits = self.panel_logits.detach()
            U = torch.rand(self.n_genes, device=self.device).clamp_(1e-6, 1.0 - 1e-6)
            gumbel = -torch.log(-torch.log(U))
            scores = logits / tau + gumbel
            chosen = torch.topk(scores, k=k, largest=True).indices  # (k,)

            lsm = torch.log_softmax(logits / tau, dim=0)
            logp_old = float(lsm[chosen].sum().item())
            return chosen, logp_old

        def _proxy_score(genes_list: List[str]) -> float:
            """
            Cheap proxy score to rank candidate panels.
            Uses centroid separation in PCA space computed on the selected genes.
            """
            try:
                from sklearn.decomposition import PCA
                X = self._extract_block(genes_list).astype(np.float32, copy=False)
                if X.shape[0] < 5 or X.shape[1] < 5:
                    return -1e9
                n_comp = int(min(self.proxy_n_pcs, X.shape[1] - 1, X.shape[0] - 1))
                if n_comp < 2:
                    return -1e9
                Xp = PCA(n_components=n_comp, random_state=0).fit_transform(X)

                y = np.asarray(self.adata.obs[self.label_key])
                uniq = np.unique(y)
                if uniq.shape[0] < 2:
                    return -1e9

                # Centroid separation / within-class scatter ratio (bigger is better)
                centroids = []
                within = 0.0
                for c in uniq:
                    idx = (y == c)
                    Xc = Xp[idx]
                    if Xc.shape[0] < 2:
                        continue
                    mu = Xc.mean(axis=0, keepdims=True)
                    centroids.append(mu.squeeze(0))
                    within += float(((Xc - mu) ** 2).mean())

                if len(centroids) < 2:
                    return -1e9
                C = np.stack(centroids, axis=0)
                # average pairwise centroid distance
                diffs = C[:, None, :] - C[None, :, :]
                d2 = (diffs ** 2).sum(axis=-1)
                sep = float(np.sqrt(np.maximum(d2.mean(), 1e-12)))
                return sep / (1e-6 + math.sqrt(max(within, 1e-12)))
            except Exception:
                return -1e9

        # More exploratory when eps is high
        tau = float(self.cem_tau * (1.0 + 1.5 * self.eps))

        # Two-stage racing / screening:
        pool_size = int(max(N_explore, self.cem_population) * max(1, self.race_pool_mult))
        pool_size = int(max(pool_size, N_explore))

        candidates = []
        idx_mat = torch.empty((pool_size, self.K_target), dtype=torch.long, device=self.device)
        logp_list = []

        for j in range(pool_size):
            chosen_t, logp_old = _sample_panel_gumbel_topk(self.K_target, tau=tau)
            idx_mat[j] = chosen_t
            logp_list.append(logp_old)
            chosen_cpu = chosen_t.detach().to("cpu", dtype=torch.long).numpy()
            genes = [self.G_all[int(i)] for i in chosen_cpu]
            candidates.append((chosen_cpu, genes, logp_old))

        # Train surrogate (adaptive schedule)
        self._train_surrogate()

        # Score candidates: surrogate-UCB if available, else proxy.
        ucb = self._surrogate_score_ucb(
            idx_mat,
            T=self.surrogate_dropout_T,
            ucb_lambda=self.surrogate_ucb_lambda,
        )
        if ucb is not None:
            scores = ucb.detach().cpu().numpy().tolist()
        else:
            scores = [_proxy_score(genes) for _, genes, _ in candidates]

        ranked = list(zip(scores, candidates))
        ranked.sort(key=lambda x: x[0], reverse=True)

        n_full = int(max(1, round(self.race_top_frac * len(ranked))))
        n_full = int(min(n_full, max(N_explore, 1)))

        top = ranked[:n_full]

        # Elitism: always evaluate incumbent and best-so-far
        forced = []
        if self.always_eval_best and self.best_G is not None:
            forced.append(list(self.best_G))
        forced.append(base_genes)

        forced_sets = set()
        forced_unique = []
        for g in forced:
            key = tuple(sorted(g))
            if key not in forced_sets:
                forced_sets.add(key)
                forced_unique.append(g)

        # Evaluate top candidates
        for step, (_, (chosen_idx, genes_tp1, logp_old)) in enumerate(top):
            r_t = self._panel_reward(genes_tp1, **reward_kwargs)

            rewards_collected.append(r_t)
            self._update_reward_stats(r_t)
            r_t_norm = self._normalize_reward(r_t)

            S_tp1 = self._encode_state(genes_tp1, train_encoder=(step % 5 == 0))

            actions_vec = torch.zeros(self.n_genes, device=self.device)
            actions_vec[torch.as_tensor(chosen_idx, device=self.device, dtype=torch.long)] = 1.0

            # logp_old retained for buffer schema compatibility; unused by KL-CEM optimize()
            self.buffer.push(S_ctx, actions_vec, r_t_norm, S_tp1, logp_old=logp_old, chosen_idx=chosen_idx)

        # Evaluate forced elites (incumbent/best) if not already in evaluated set
        evaluated_keys = set(tuple(sorted(genes)) for _, (_, genes, _) in top)
        for genes_forced in forced_unique:
            key = tuple(sorted(genes_forced))
            if key in evaluated_keys:
                continue

            r_t = self._panel_reward(genes_forced, **reward_kwargs)
            rewards_collected.append(r_t)
            self._update_reward_stats(r_t)
            r_t_norm = self._normalize_reward(r_t)

            S_tp1 = self._encode_state(genes_forced, train_encoder=False)

            actions_vec = torch.zeros(self.n_genes, device=self.device)
            forced_idx = []
            for g in genes_forced:
                if g in self.gene_to_idx:
                    ii = self.gene_to_idx[g]
                    actions_vec[ii] = 1.0
                    forced_idx.append(ii)

            self.buffer.push(S_ctx, actions_vec, r_t_norm, S_tp1, logp_old=0.0, chosen_idx=forced_idx)

        # Update incumbent to best of evaluated panels (greedy)
        if rewards_collected:
            evaluated_gene_lists = [genes for _, (_, genes, _) in top] + forced_unique
            best_i = int(np.argmax(rewards_collected))
            best_genes = evaluated_gene_lists[best_i]
            self.current_subset = set(best_genes)

        return rewards_collected

    def optimize(self, N_optimize: int):
        """
        KL-regularized CEM (trust-region) update on per-gene inclusion marginals.

        - No PPO ratios / no log-prob mismatch.
        - Compute elite empirical marginals m_i over panels (reward-weighted).
        - Update inclusion propensities p_i = sigmoid(logit_i) toward m_i with step η.
        - Enforce KL(p_old || p_new) <= kl_target via backtracking on η.
        - Map p_new back to logits via logit() and store in self.panel_logits.

        Args:
            N_optimize: Number of CEM update steps
        """
        def _bernoulli_kl(p: torch.Tensor, q: torch.Tensor) -> torch.Tensor:
            eps = 1e-6
            p = torch.clamp(p, eps, 1.0 - eps)
            q = torch.clamp(q, eps, 1.0 - eps)
            return (p * torch.log(p / q) + (1.0 - p) * torch.log((1.0 - p) / (1.0 - q))).mean()

        for _ in range(N_optimize):
            if len(self.buffer) < max(10, self.minibatch):
                return

            S_t, actions, r_t, S_tp1, logp_old, chosen_idx_list = self.buffer.sample(self.minibatch, self.device)

            # Stabilize scale per minibatch
            r = r_t.squeeze(1)
            r = (r - r.mean()) / (r.std(unbiased=False) + 1e-6)

            # Select elites
            n_elite = max(2, int(self.cem_elite_frac * actions.shape[0]))
            elite_idx = torch.topk(r, k=n_elite, largest=True).indices
            A_elite = actions[elite_idx]  # (E, n_genes)
            r_elite = r[elite_idx]        # (E,)

            # Reward-weight elites (softmax weights)
            temp = 0.75
            w = torch.softmax(r_elite / temp, dim=0)  # (E,)
            m = (w.unsqueeze(1) * A_elite).sum(dim=0)  # (n_genes,)

            # Mild anti-collapse prior early: mix toward baseline inclusion p0
            p0 = float(self.K_target) / float(max(1, self.n_genes))
            lam = float(min(0.15, 0.15 * (self.eps / 0.5)))  # decays with eps
            m = (1.0 - lam) * m + lam * p0

            # Old probs from current logits
            p_old = torch.sigmoid(self.panel_logits.detach())

            # Proposed update with trust region (backtracking on eta)
            eta = float(self.cem_lr)
            kl_target = float(self.cem_kl_target)

            # Backtracking to satisfy KL constraint
            for _bt in range(20):
                p_new = (1.0 - eta) * p_old + eta * m
                kl = _bernoulli_kl(p_old, p_new)
                if float(kl.item()) <= kl_target or eta < 1e-6:
                    break
                eta *= float(self.cem_kl_backtrack)

            # Commit update
            eps = 1e-6
            p_new = torch.clamp(p_new, eps, 1.0 - eps)
            self.panel_logits = torch.log(p_new) - torch.log(1.0 - p_new)

            # Anneal temperature slowly toward cem_tau_end
            self.cem_tau = max(self.cem_tau_end, self.cem_tau * 0.98)

    def train(self, epochs: int, N_explore: int = 10, N_optimize: int = 5, verbose: bool = True, **reward_kwargs) -> Dict:
        """
        Main training loop.

        Args:
            epochs: Number of training epochs
            N_explore: Exploration steps per epoch
            N_optimize: Optimization steps per epoch
            verbose: Whether to print progress
            **reward_kwargs: Additional arguments for reward function

        Returns:
            Dictionary with best_panel, best_reward, and training_history
        """
        for epoch in tqdm(range(epochs)):
            genes = list(self.current_subset)
            R = self._panel_reward(genes, **reward_kwargs)

            self.reward_history.append(R)
            self.size_history.append(len(genes))
            self.eps_history.append(self.eps)

            rewards_epoch = self.explore(N_explore, **reward_kwargs)

            if rewards_epoch:
                self.epoch_rewards.append(np.mean(rewards_epoch))
                self.epoch_rewards_min.append(np.min(rewards_epoch))
                self.epoch_rewards_max.append(np.max(rewards_epoch))

            if R > self.best_R:
                self.best_R = R
                self.best_G = list(genes)

            if verbose:
                logger.info(f"Epoch {epoch + 1}/{epochs} | eps={self.eps:.3f} | |G|={len(genes)} | R={R:.4f} | best={self.best_R:.4f}")

            self.optimize(N_optimize)

            self.eps = max(0.10, self.eps * 0.97)

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
