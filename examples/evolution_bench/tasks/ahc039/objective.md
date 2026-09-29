AtCoder Heuristic Contest 039 — Purse Seine Fishing.

5000 mackerels and 5000 sardines sit on a 2D grid. Draw ONE axis-aligned rectilinear polygon and
score `(mackerels strictly inside or on the boundary) - (sardines inside or on the boundary)`.
Constraints: at most 1000 vertices, perimeter at most 400000, coordinates in [0, 100000], the
polygon must be simple (no self-intersection) and its edges must alternate horizontal/vertical.

Edit `solution.cpp`. It is compiled with g++-12 (C++20) and run once per test case as

    ./a.out 2.0 < input.txt > output.txt

with argv[1] the time limit in seconds. Print the vertex count, then one `x y` per line.

**Fitness is the MEAN score over 150 official test cases**, normalised so the seed scores 2.47
(raw 3709 per case). Higher is better. The target is 5000 per case. A case that times out, crashes
or emits an illegal polygon scores zero and the whole evaluation is reported as infeasible — you
do not get to average over the cases that worked.

The seed is a 5th-place contest solution: a time-bounded local search over a rectilinear polygon,
already tuned. Beating it means finding something it does not do, not tidying it up.

Where the headroom is. Because the score is a mean over 150 independent cases, gains are ADDITIVE
— there is no single insight that collects the whole prize, and a change that helps one family of
instances while hurting another nets out to nothing. Directions that differ from each other:
the polygon representation (grid-snapped strips, monotone staircases, unions of rectangles),
the search itself (simulated annealing schedules, large-neighbourhood moves, ruin-and-recreate,
beam search over strip assignments), how the 2-second budget is spent (restarts versus one long
descent, adaptive move mixes, early termination on convergence), and instance-adaptive strategy
(detecting clustered versus dispersed inputs and switching approach).

Two things about the measurement you should know. It is noisy — the same program scored 2.47339,
2.47155 and 2.47244 on three runs, because scoring is wall-clock-limited — so a change smaller than
about 0.002 is not distinguishable from noise. And the machine runs the tester under x86 emulation
at roughly 65% of native speed, so a solution that exactly fills 2.0 seconds here is doing less
work than it would on the contest judge. Both affect every attempt equally.

Write the search yourself. Do not shell out to an external solver.
