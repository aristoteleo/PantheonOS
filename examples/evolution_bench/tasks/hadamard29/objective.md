Find a 29x29 matrix H with every entry equal to +1 or -1 that MAXIMISES |det(H)|.

`solution.py` must expose `run_code()` returning the matrix (a 29x29 array of +1/-1), either
directly or as the first element of a tuple. The determinant is recomputed exactly, in integers --
a claimed value is ignored, and `numpy.linalg.det` is not accurate enough at this magnitude to
rank two near-equal candidates, so compute yours exactly if you compare them.

Fitness is `combined_score = |det(H)| / 1270698346568170340352`, higher is better. The denominator
is Hadamard's bound for n=29; it is NOT attainable, because a Hadamard matrix requires n divisible
by 4. The seed scores 0.1433.

An entry that is not exactly +1 or -1, or a matrix of the wrong shape, scores nothing and is
reported as infeasible -- it is not a low score, it is a failed attempt.

Where the headroom is: two quite different families of approach exist, and they do not reach the
same place. One is algebraic -- construct from quadratic residues, Paley-type or conference-matrix
constructions, block designs, circulant or negacirculant cores -- which lands on a structured
matrix immediately. The other is search -- hill climbing, simulated annealing, tabu search over
sign flips, row/column negation and permutation, restarts from many starting points. The strongest
known results for n = 29 come from combining them: a structured start, then local search that
preserves as much structure as it can. Write the search logic yourself; do not hand the problem to
a general-purpose solver.
