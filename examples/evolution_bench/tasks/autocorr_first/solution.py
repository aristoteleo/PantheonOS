# EVOLVE-BLOCK-START
"""LP-guided local search baseline for C1 autoconvolution minimization."""
import time
import numpy as np
from scipy import optimize

linprog = optimize.linprog


def get_good_direction_to_move_into(sequence):
    """Returns a better direction using LP to find g with larger sum while keeping conv bounded."""
    n = len(sequence)
    if n == 0:
        return None

    sum_sequence = np.sum(sequence)
    if sum_sequence <= 0.0:
        return None

    normalized_sequence = [x * np.sqrt(2 * n) / sum_sequence for x in sequence]
    rhs = np.max(np.convolve(normalized_sequence, normalized_sequence))
    g_fun = solve_convolution_lp(normalized_sequence, rhs)
    if g_fun is None:
        return None

    sum_g = np.sum(g_fun)
    if sum_g <= 0.0:
        return None

    normalized_g_fun = [x * np.sqrt(2 * n) / sum_g for x in g_fun]
    t = 0.01
    new_sequence = [(1 - t) * x + t * y for x, y in zip(sequence, normalized_g_fun)]
    return new_sequence


def solve_convolution_lp(f_sequence, rhs):
    """Solves the LP: maximize sum(b) s.t. conv(f, b) <= rhs, b >= 0."""
    n = len(f_sequence)
    if n == 0:
        return None

    c = -np.ones(n)
    a_ub = []
    b_ub = []
    for k in range(2 * n - 1):
        row = np.zeros(n)
        for i in range(n):
            j = k - i
            if 0 <= j < n:
                row[j] = f_sequence[i]
        a_ub.append(row)
        b_ub.append(rhs)
    a_ub_nonneg = -np.eye(n)
    b_ub_nonneg = np.zeros(n)
    a_ub = np.vstack([a_ub, a_ub_nonneg])
    b_ub = np.hstack([b_ub, b_ub_nonneg])
    result = linprog(
        c,
        A_ub=a_ub,
        b_ub=b_ub,
        options={
            "time_limit": 10.0,
            "disp": False,
        },
    )
    if result.success:
        return result.x
    return None


def propose_candidate(seed=42, budget_s=250, **kwargs):
    np.random.seed(seed)
    deadline = time.time() + budget_s - 10

    prev = globals().get("GLOBAL_BEST_CONSTRUCTION")
    if np.random.rand() < 0.5 and isinstance(prev, (list, tuple, np.ndarray)) and len(prev) > 0:
        best_sequence = list(np.asarray(prev, dtype=float))
    else:
        # Start from random initialization, could help if prev is a local minimum
        best_sequence = [float(np.random.random())] * int(np.random.randint(100, 1000))

    curr_sequence = best_sequence.copy()
    best_score = evaluate_sequence(best_sequence)

    while time.time() < deadline:
        h_function = get_good_direction_to_move_into(curr_sequence)
        if h_function is None:
            # Random perturbation if LP fails
            idx = int(np.random.randint(len(curr_sequence)))
            curr_sequence[idx] = max(0.0, curr_sequence[idx] + float(np.random.randn()) * 0.01)
        else:
            curr_sequence = h_function

        try:
            curr_score = evaluate_sequence(curr_sequence)
            if curr_score < best_score:
                best_score = curr_score
                best_sequence = curr_sequence.copy()
        except Exception:
            pass

    return [float(max(0.0, x)) for x in best_sequence]


# EVOLVE-BLOCK-END


def evaluate_sequence(sequence):
    """
    C1 verifier helper, matching evaluator logic.
    Returns np.inf if the input is invalid.
    """
    if not isinstance(sequence, list):
        return np.inf
    if not sequence:
        return np.inf

    for x in sequence:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return np.inf
        if np.isnan(x) or np.isinf(x):
            return np.inf

    sequence = [float(x) for x in sequence]
    sequence = [max(0.0, x) for x in sequence]
    sequence = [min(1000.0, x) for x in sequence]

    n = len(sequence)
    b_sequence = np.convolve(sequence, sequence)
    max_b = max(b_sequence)
    sum_a = np.sum(sequence)

    if sum_a < 0.01:
        return np.inf

    return float(2 * n * max_b / (sum_a**2))


def run_code():
    """Run the C1 constructor and return (solution, self-reported score)."""
    heights = propose_candidate(seed=42, budget_s=250)
    c1_value = evaluate_sequence(heights)
    return heights, c1_value
