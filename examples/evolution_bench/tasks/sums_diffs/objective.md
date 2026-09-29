For a finite set of integers A, define the sumset A+A = {a+b} and the difference set A-A = {a-b}.
MAXIMISE

    C(A) = log(|A+A| / |A|) / log(|A-A| / |A|)

which is a lower bound on the smallest constant C for which |A+A|/|A| <= (|A-A|/|A|)^C holds for
every finite A.

`solution.py` must expose `run_code()` returning A -- an iterable of integers, or a tuple whose
first element is one. Any claimed C is ignored; the score is recomputed from the set.

Constraints, all enforced: integers only, 2 <= |A| <= 512 after removing duplicates, every element
in [-1000000, 1000000]. Breaking any of them is reported as infeasible rather than scored -- the
set is not repaired, clamped or padded for you.

Fitness is `combined_score = C(A)`, higher is better. The seed, a 17-element set, scores 1.0598.

Where the headroom is: C(A) rewards a set whose sumset grows fast while its difference set does
not, and difference sets are symmetric while sumsets are not -- so the interesting constructions
are deliberately asymmetric. Distinct families behave very differently: arithmetic-progression-like
sets with holes, unions of shifted blocks, B_h/Sidon-style sets, generalised-arithmetic-progression
lattices projected to one dimension, greedy or local search over membership, and MSTD (more sums
than differences) constructions. Which family you pick matters more than how hard you tune inside
it. Write the construction and any search yourself.
