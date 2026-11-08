AtCoder Heuristic Contest 073 — Circles of Inversion.

A 128×128 board (N = 128). Takahashi starts at s and must visit M = 150 destinations g_0 … g_149 in
that order. Before moving you draw K = 3 magic circles on cells of your choice. Each operation is
either a walk (one step U/D/L/R, staying on the board) or a teleport `J a b` through a cell (a, b)
that currently holds a circle: from (i, j) he lands on (2a − i, 2b − j), the point reflection
through the circle, which must be on the board; that circle is removed and a new one is drawn on the
cell he left, (i, j). A destination counts as visited only when he stands on the next required
destination right after an operation. T (given in the input) is the walking-only cost: the total
Manhattan distance s → g_0 → … → g_149. At most T operations may be used. Teleports through
well-placed circles skip long walks; the circle you jump through moves to where you were, so the
circle layout keeps changing.

Score per case = M + T − A + 1 if all M destinations were visited (A = number of operations),
otherwise V + 1 for V destinations visited. Higher is better; 150 test cases averaged.

Input: `N M K T`, then `s_i s_j`, then M lines `g_i g_j`.
Output: K lines `a_k b_k` (the initial circles), then one operation per line: `U`, `D`, `L`, `R`, or
`J a b`.

Edit `solution.cpp`. It is compiled with g++-12 (C++20) and run once per test case as

    ./a.out 2.0 < input.txt > output.txt

(2 s, 1024 MiB; argv[1] is the time limit). The seed walks the Manhattan paths and never teleports
(A = T, score M + 1 per case).
