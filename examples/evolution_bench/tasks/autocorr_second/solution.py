# EVOLVE-BLOCK-START
"""Gradient-based population search for maximizing the AC2 lower bound."""
import numpy as np
from typing import Tuple


def _simpson_l2sq(conv: np.ndarray) -> Tuple[float, np.ndarray]:
    """
    Compute ||f*f||_2^2 via Simpson-like piecewise-linear rule with endpoint zeros,
    and return its gradient w.r.t conv (same length as conv).
    """
    m = conv.size
    if m == 0:
        return 0.0, np.zeros_like(conv)

    dx = 1.0 / (m + 1)

    y = np.empty(m + 2, dtype=conv.dtype)
    y[0] = 0.0
    y[1:-1] = conv
    y[-1] = 0.0

    lhs = y[:-1]
    rhs = y[1:]
    l2_sq = (dx / 3.0) * np.sum(lhs * lhs + lhs * rhs + rhs * rhs)

    grad_y = (dx / 3.0) * (4.0 * y + np.roll(y, 1) + np.roll(y, -1))
    grad_conv = grad_y[1:-1]

    return float(l2_sq), grad_conv


def _l1(conv: np.ndarray) -> Tuple[float, np.ndarray]:
    """||f*f||_1 = dx * sum(conv); gradient is dx * ones."""
    m = conv.size
    dx = 1.0 / (m + 1) if m > 0 else 1.0
    val = dx * float(np.sum(conv)) if m > 0 else 0.0
    grad = np.full_like(conv, dx)
    return val, grad


def _linf(conv: np.ndarray) -> Tuple[float, np.ndarray]:
    """||f*f||_inf = max(conv); subgradient: uniform over argmax set."""
    if conv.size == 0:
        return 0.0, np.zeros_like(conv)
    m = float(np.max(conv))
    mask = conv == m
    count = int(mask.sum())
    if count == 0 or m <= 0.0:
        return m, np.zeros_like(conv)
    grad = mask.astype(conv.dtype) / count
    return m, grad


def _objective_and_grad_conv(conv: np.ndarray) -> Tuple[float, np.ndarray]:
    """
    Compute C = l2_sq / (l1 * linf) and gradient dC/d(conv) using quotient rule.
    """
    l2_sq, g_l2 = _simpson_l2sq(conv)
    l1, g_l1 = _l1(conv)
    linf, g_linf = _linf(conv)

    if l1 <= 0.0 or linf <= 0.0:
        return 0.0, np.zeros_like(conv)

    denom = l1 * linf
    c_value = l2_sq / denom

    num_grad = g_l2 * denom - l2_sq * (g_l1 * linf + l1 * g_linf)
    g_conv = num_grad / (denom * denom)

    return float(c_value), g_conv


def _grad_h_from_conv_grad(h: np.ndarray, g_conv: np.ndarray) -> np.ndarray:
    """
    Given dC/d(conv) and conv = h * h (full convolution),
    dC/dh = 2 * (g_conv convolved with reverse(h)) in valid mode (length N).
    """
    h_rev = h[::-1]
    g_h = np.convolve(g_conv, h_rev, mode="valid")
    return 2.0 * g_h


class _Adam:
    """Lightweight Adam optimizer for numpy arrays (per-candidate)."""

    def __init__(self, shape, lr=3e-2, beta1=0.9, beta2=0.999, eps=1e-8, dtype=np.float32):
        self.m = np.zeros(shape, dtype=dtype)
        self.v = np.zeros(shape, dtype=dtype)
        self.t = 0
        self.lr = lr
        self.b1 = beta1
        self.b2 = beta2
        self.eps = eps

    def step(self, params, grad):
        self.t += 1
        self.m = self.b1 * self.m + (1 - self.b1) * grad
        self.v = self.b2 * self.v + (1 - self.b2) * (grad * grad)
        m_hat = self.m / (1 - self.b1 ** self.t)
        v_hat = self.v / (1 - self.b2 ** self.t)
        return params + self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


def _batch_objective(h_batch: np.ndarray) -> Tuple[np.ndarray, list[np.ndarray]]:
    """Vectorized objective/conv-grad evaluation over a batch."""
    bsz = h_batch.shape[0]
    c_vals = np.zeros(bsz, dtype=np.float32)
    conv_grads = [None] * bsz
    for b in range(bsz):
        h = np.clip(h_batch[b], 0.0, None)
        conv = np.convolve(h, h, mode="full")
        c_val, g_conv = _objective_and_grad_conv(conv)
        c_vals[b] = c_val
        conv_grads[b] = g_conv
    return c_vals, conv_grads


def _phase_update(h_batch, opt_list, lr, add_noise=False, t=0, eta=1e-3, gamma=0.55):
    """One optimization step for the whole batch."""
    bsz = h_batch.shape[0]
    c_vals, conv_grads = _batch_objective(h_batch)
    grads = np.zeros_like(h_batch, dtype=h_batch.dtype)
    for b in range(bsz):
        clipped = np.clip(h_batch[b], 0.0, None)
        grads[b] = _grad_h_from_conv_grad(clipped, conv_grads[b])

    if add_noise:
        sigma = eta / ((t + 1) ** gamma)
        grads = grads + sigma * np.random.normal(size=grads.shape).astype(grads.dtype)

    for b in range(bsz):
        opt = opt_list[b]
        opt.lr = lr
        h_new = opt.step(h_batch[b], grads[b].astype(h_batch.dtype))
        h_batch[b] = np.clip(h_new, 0.0, None)

    return h_batch, c_vals


def _elitist_respawn(h_batch, c_vals, keep_frac, init_sampler, opt_list):
    """Keep top fraction and respawn the rest."""
    bsz = h_batch.shape[0]
    keep_n = max(1, int(bsz * keep_frac))
    idx = np.argsort(c_vals)[-keep_n:]
    survivors = h_batch[idx].copy()

    fresh = init_sampler(bsz - keep_n)
    new_batch = np.concatenate([survivors, fresh], axis=0)

    new_opts = []
    for i in range(keep_n):
        new_opts.append(opt_list[idx[i]])
    for _ in range(bsz - keep_n):
        new_opts.append(_Adam(shape=h_batch.shape[1:], lr=opt_list[0].lr, dtype=h_batch.dtype))

    return new_batch, new_opts


def _upsample_1d(h: np.ndarray) -> np.ndarray:
    """Linear 2x upsampling on the search grid."""
    n = h.shape[0]
    x_old = np.linspace(-0.5, 0.5, n)
    x_new = np.linspace(-0.5, 0.5, 2 * n)
    return np.interp(x_new, x_old, h)


def _single_candidate_finetune(h0: np.ndarray, lr=3e-3, steps=50_000) -> Tuple[np.ndarray, float]:
    """Pure exploitation (no noise) on a single vector with Adam + projection."""
    h = h0.astype(np.float32).copy()
    opt = _Adam(h.shape, lr=lr, dtype=h.dtype)
    last_c = 0.0
    for _ in range(steps):
        h_clip = np.clip(h, 0.0, None)
        conv = np.convolve(h_clip, h_clip, mode="full")
        c_val, g_conv = _objective_and_grad_conv(conv)
        g_h = _grad_h_from_conv_grad(h_clip, g_conv)
        h = np.clip(opt.step(h, g_h.astype(h.dtype)), 0.0, None)
        last_c = c_val
    return h, float(last_c)


def construct_function():
    """
    Four-phase gradient-based search to maximize
    R(f) = ||f*f||_2^2 / (||f*f||_1 * ||f*f||_inf).
    """
    n = 256
    bsz = 64
    total_iter = 10_000
    explore_steps = 30_000
    drop_every = 10_000
    keep_frac = 0.5
    lr_explore = 3e-2
    lr_exploit = 5e-3
    eta, gamma = 1e-3, 0.55
    dtype = np.float32

    prev = globals().get("GLOBAL_BEST_CONSTRUCTION")
    if isinstance(prev, (list, tuple, np.ndarray)) and len(prev) > 0:
        h_prev_best = np.array(prev, dtype=dtype)
    else:
        h_prev_best = np.ones(n, dtype=dtype)
    h_prev_best = np.clip(h_prev_best, 0.0, None)

    if h_prev_best.shape[0] != n:
        x_old = np.linspace(-0.5, 0.5, h_prev_best.shape[0])
        x_new = np.linspace(-0.5, 0.5, n)
        h_prev_best = np.interp(x_new, x_old, h_prev_best).astype(dtype)

    rng = np.random.default_rng()

    def init_sampler(m):
        out = rng.uniform(0.0, 1.0, size=(m, n)).astype(dtype)
        if m > 0:
            out[0] = h_prev_best
        return out

    h_batch = init_sampler(bsz)
    opt_list = [_Adam(shape=(n,), lr=lr_explore, dtype=dtype) for _ in range(bsz)]
    best_h = h_batch.copy()
    best_c = np.full(bsz, -np.inf, dtype=dtype)

    for t in range(total_iter):
        if t < explore_steps:
            h_batch, c_vals = _phase_update(
                h_batch, opt_list, lr=lr_explore, add_noise=True, t=t, eta=eta, gamma=gamma
            )
        else:
            h_batch, c_vals = _phase_update(
                h_batch, opt_list, lr=lr_exploit, add_noise=False, t=t, eta=eta, gamma=gamma
            )

        improved = c_vals > best_c
        best_c = np.where(improved, c_vals, best_c)
        best_h[improved] = h_batch[improved]

        if (t + 1) % drop_every == 0:
            h_batch, opt_list = _elitist_respawn(
                h_batch, c_vals, keep_frac=keep_frac, init_sampler=init_sampler, opt_list=opt_list
            )

    idx = int(np.argmax(best_c))
    h_star = np.clip(best_h[idx].astype(np.float32), 0.0, None)

    h_up1 = _upsample_1d(h_star)
    h_up1, _ = _single_candidate_finetune(h_up1, lr=3e-3, steps=40_000)

    h_up2 = _upsample_1d(h_up1)
    h_up2, _ = _single_candidate_finetune(h_up2, lr=3e-3, steps=40_000)

    heights = np.clip(h_up2, 0.0, None)
    r_value = evaluate_sequence(heights.tolist())
    print("This gets a C2 lower bound of", r_value)
    return heights.tolist()


# EVOLVE-BLOCK-END



def evaluate_sequence(sequence):
    """
    AC2 verifier logic from the pasted code.

    Raises:
        ValueError: for invalid sequence types/values.
    """
    # Verify that the input is a list
    if not isinstance(sequence, list):
        raise ValueError("Invalid sequence type")

    # Reject empty lists
    if not sequence:
        raise ValueError("Empty sequence")

    # Check each element in the list for validity
    for x in sequence:
        # Reject boolean types (as they are a subclass of int) and
        # any other non-integer/non-float types (like strings or complex numbers).
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise ValueError("Invalid sequence element type")

        # Reject Not-a-Number (NaN) and infinity values.
        if np.isnan(x) or np.isinf(x):
            raise ValueError("Invalid sequence element value")

    # Convert all elements to float for consistency
    sequence = [float(x) for x in sequence]

    # Protect against negative numbers
    sequence = [max(0, x) for x in sequence]

    # Check if sum of sequence will be too close to zero
    if np.sum(sequence) < 0.01:
        raise ValueError("Sum of sequence is too close to zero.")
    
    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    convolution_2 = np.convolve(sequence, sequence)
    # --- Security Checks ---

    # Calculate the 2-norm squared: ||f*f||_2^2
    num_points = len(convolution_2)
    x_points = np.linspace(-0.5, 0.5, num_points + 2)
    x_intervals = np.diff(x_points)  # Width of each interval
    y_points = np.concatenate(([0], convolution_2, [0]))
    l2_norm_squared = 0.0
    for i in range(len(convolution_2) + 1):  # Iterate through intervals
        y1 = y_points[i]
        y2 = y_points[i + 1]
        h = x_intervals[i]
        # Integral of (mx + c)^2 = h/3 * (y1^2 + y1*y2 + y2^2) where m = (y2-y1)/h, c = y1 - m*x1, interval is [x1, x2], y1 = mx1+c, y2=mx2+c
        interval_l2_squared = (h / 3) * (y1**2 + y1 * y2 + y2**2)
        l2_norm_squared += interval_l2_squared

    # Calculate the 1-norm: ||f*f||_1
    norm_1 = np.sum(np.abs(convolution_2)) / (len(convolution_2) + 1)

    # Calculate the infinity-norm: ||f*f||_inf
    norm_inf = np.max(np.abs(convolution_2))
    C_lower_bound = l2_norm_squared / (norm_1 * norm_inf)
    return C_lower_bound


def run_code():
    """Run the C2 optimization constructor and return (solution, self-reported score)."""
    heights = construct_function()
    c2_value = evaluate_sequence(heights)
    return heights, c2_value
