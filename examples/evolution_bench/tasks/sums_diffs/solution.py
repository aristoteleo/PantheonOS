# EVOLVE-BLOCK-START
import math

def construct_set():
    """Return a candidate integer set A (as an iterable)."""
    return [0, 1, 2, 4, 5, 9, 12, 13, 14, 16, 17, 21, 24, 25, 26, 28, 29]


# EVOLVE-BLOCK-END

MIN_SET_SIZE = 2
MAX_SET_SIZE = 512
MIN_INT = -1_000_000
MAX_INT = 1_000_000


def _sanitize_output(values):
    """Convert arbitrary iterable output into a valid sorted integer list."""
    try:
        raw = list(values)
    except TypeError as e:
        raise ValueError(f"Output is not iterable: {e}")

    ints = []
    for x in raw:
        try:
            xf = float(x)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(xf):
            continue
        xi = int(round(xf))
        xi = max(MIN_INT, min(MAX_INT, xi))
        ints.append(xi)

    unique_vals = sorted(set(ints))
    if len(unique_vals) > MAX_SET_SIZE:
        unique_vals = unique_vals[:MAX_SET_SIZE]

    if len(unique_vals) < MIN_SET_SIZE:
        unique_vals = [0, 1]

    return unique_vals


def _compute_c(values):
    n = len(values)
    sumset = {a + b for a in values for b in values}
    diffset = {a - b for a in values for b in values}

    sum_ratio = len(sumset) / n
    diff_ratio = len(diffset) / n

    if sum_ratio <= 1.0 or diff_ratio <= 1.0:
        return 0.0

    return float(math.log(sum_ratio) / math.log(diff_ratio))


def run_code():
    """Return (A_values, claimed_c)."""
    values = construct_set()
    values = _sanitize_output(values)
    c_value = _compute_c(values)
    return values, c_value


if __name__ == "__main__":
    candidate_values, candidate_c = run_code()
    print(f"|A|={len(candidate_values)}, C(A)={candidate_c:.10f}")
