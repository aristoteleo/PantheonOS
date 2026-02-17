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

import logging
import math
import random
from collections import deque
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

import numpy as np
import scipy.sparse as sp
import torch
import torch.nn as nn
import torch.nn.functional as F
from tqdm import tqdm

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
    """Single buffer storing transitions for all genes together."""

    def __init__(self, capacity=2000):
        self.buf = deque(maxlen=capacity)

    def push(self, S_t, actions_vec, r_t, S_tp1):
        """
        S_t: (state_dim,) tensor - current state
        actions_vec: (n_genes,) tensor - 1 if gene included, 0 otherwise
        r_t: float - reward for this transition
        S_tp1: (state_dim,) tensor - next state
        """
        self.buf.append((
            S_t.detach().cpu().float(),
            actions_vec.detach().cpu().float(),
            float(r_t),
            S_tp1.detach().cpu().float()
        ))

    def sample(self, batch_size=64, device=None):
        batch = random.sample(self.buf, k=min(batch_size, len(self.buf)))
        S_t, actions, r_t, S_tp1 = zip(*batch)
        device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return (
            torch.stack(S_t).to(device),
            torch.stack(actions).to(device),
            torch.tensor(r_t, dtype=torch.float32, device=device).unsqueeze(1),
            torch.stack(S_tp1).to(device),
        )

    def __len__(self):
        return len(self.buf)


# =============================================================================
# REWARD FUNCTION (Primary Evolution Target)
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
    alpha: float = 0.8,
    K_target: int = 500,
    K_max: int = 1000,
    beta: float = 1.5,
) -> Dict:
    """
    Evaluate a candidate gene panel by clustering performance and size compliance.
    Uses GPU-accelerated rapids_singlecell if available, otherwise falls back to scanpy.

    This is a PRIMARY EVOLUTION TARGET. The LLM may explore:
    - Multi-metric rewards (include NMI, SI)
    - Progressive penalties based on training progress
    - Non-linear scaling of ARI
    - Diversity bonuses for pathway coverage

    Args:
        adata: AnnData object with expression data
        genes: List of gene names to evaluate
        label_key: Column in adata.obs with true labels
        n_neighbors: Number of neighbors for graph construction
        resolution: Leiden clustering resolution
        n_pcs: Number of PCA components
        alpha: Weight for ARI vs size term (higher = more ARI weight)
        K_target: Target panel size (no penalty below this)
        K_max: Maximum panel size (zero reward above this)
        beta: Shape parameter for size penalty curve

    Returns:
        Dictionary with reward, ari, size_term, and num_genes
    """
    from sklearn.metrics import adjusted_rand_score

    genes = [g for g in genes if g in adata.var_names]
    K = len(genes)

    if K < 10:
        return dict(reward=0.0, ari=0.0, size_term=0.0, num_genes=K)

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

    ari = adjusted_rand_score(true, clusters)

    if K <= K_target:
        size_term = 1.0
    elif K_target < K <= K_max:
        size_term = (1 - (K - K_target) / (K_max - K_target)) ** beta
    else:
        size_term = 0.0

    reward = alpha * ari + (1 - alpha) * size_term

    return dict(
        reward=reward,
        ari=ari,
        size_term=size_term,
        num_genes=K,
    )


# =============================================================================
# RL TRAINER
# =============================================================================

class SmartCurationTrainer:
    """
    RL trainer for gene panel selection with:
    - Fixed-size state representation
    - Structured exploration with size constraints
    - Knowledge injection from prior methods
    - Batched actor-critic optimization
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

        self.actor = BatchedActor(self.n_genes, self.state_dim, hidden=(128, 32), device=self.device)
        self.critic = BatchedCritic(self.n_genes, self.state_dim, hidden=(128, 32), device=self.device)

        self.opt_actor = torch.optim.Adam(self.actor.parameters(), lr=actor_lr)
        self.opt_critic = torch.optim.Adam(self.critic.parameters(), lr=critic_lr)

        self.buffer = BatchedReplayBuffer(capacity=memory_size)

        self.eps = epsilon

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
        out = self.reward_fn(self.adata, genes, label_key=self.label_key, **reward_kwargs)
        return float(out["reward"])

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

            self.buffer.push(S0, actions_vec, r_norm, Sf)

    def explore(self, N_explore: int, **reward_kwargs) -> List[float]:
        """
        Exploration step using epsilon-greedy with Gaussian noise.

        This is a SECONDARY EVOLUTION TARGET. The LLM may explore:
        - Boltzmann/temperature-based sampling
        - UCB-style exploration bonuses
        - Elite gene preservation strategies
        - Adaptive noise schedules

        Args:
            N_explore: Number of exploration steps
            **reward_kwargs: Additional arguments for reward function

        Returns:
            List of rewards collected during exploration
        """
        genes_t = list(self.current_subset)
        rewards_collected = []

        S_t = self._encode_state(genes_t, train_encoder=True)

        for step in range(N_explore):
            with torch.no_grad():
                all_logits = self.actor(S_t.unsqueeze(0)).squeeze(0)
                all_probs = torch.sigmoid(all_logits).cpu().numpy()
            probs = {g: all_probs[i] for i, g in enumerate(self.G_all)}

            if self.rng.random() < self.eps:
                target_size = int(np.clip(self.rng.normal(self.K_target, 100), 200, self.K_max))
                noisy_probs = {g: p + self.rng.normal(0, 0.2) for g, p in probs.items()}
            else:
                target_size = self.K_target
                noisy_probs = probs

            genes_tp1 = sorted(noisy_probs.keys(), key=lambda g: noisy_probs[g], reverse=True)[:target_size]
            if len(genes_tp1) < 100:
                genes_tp1 = list(self.rng.choice(self.G_all, 100, replace=False))

            r_t = self._panel_reward(genes_tp1, **reward_kwargs)

            rewards_collected.append(r_t)
            self._update_reward_stats(r_t)
            r_t_norm = self._normalize_reward(r_t)

            S_tp1 = self._encode_state(genes_tp1, train_encoder=(step % 5 == 0))

            genes_set = set(genes_tp1)
            actions_vec = torch.zeros(self.n_genes, device=self.device)
            for g in genes_set:
                actions_vec[self.gene_to_idx[g]] = 1.0

            self.buffer.push(S_t, actions_vec, r_t_norm, S_tp1)

            S_t = S_tp1
            genes_t = genes_tp1
            self.current_subset = set(genes_tp1)

        return rewards_collected

    def optimize(self, N_optimize: int):
        """
        Optimization step using policy gradient with entropy regularization.

        This is a TERTIARY EVOLUTION TARGET. The LLM may explore:
        - GAE (Generalized Advantage Estimation)
        - Entropy coefficient scheduling
        - Advantage normalization
        - PPO-style clipping

        Args:
            N_optimize: Number of optimization steps
        """
        for _ in range(N_optimize):
            if len(self.buffer) < self.minibatch:
                return

            S_t, actions, r_t, S_tp1 = self.buffer.sample(self.minibatch, self.device)

            # Critic update
            self.opt_critic.zero_grad()
            V_t = self.critic(S_t)
            with torch.no_grad():
                V_tp1 = self.critic(S_tp1)
                target = r_t + self.gamma * V_tp1

            critic_loss = F.mse_loss(V_t, target)
            critic_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.critic.parameters(), 1.0)
            self.opt_critic.step()

            # Actor update
            self.opt_actor.zero_grad()
            logits = self.actor(S_t)

            logp = actions * F.logsigmoid(logits) + (1 - actions) * F.logsigmoid(-logits)

            with torch.no_grad():
                advantage = target - V_t

            policy_loss = -(logp * advantage).mean()

            p = torch.sigmoid(logits)
            entropy = -(p * torch.log(p + 1e-8) + (1 - p) * torch.log(1 - p + 1e-8))
            entropy_loss = -self.entropy_beta * entropy.mean()

            actor_loss = policy_loss + entropy_loss
            actor_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.actor.parameters(), 1.0)
            self.opt_actor.step()

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
