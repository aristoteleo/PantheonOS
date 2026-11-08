AtCoder Heuristic Contest 070 — Ward the Village.

An N×N village (N = 100). Over N² = 10000 turns, turn t (t = 0 … 9999) brings a haunting at cell
(a_t, b_t); every cell is haunted exactly once, in a uniformly random order given in the input. Before
the first turn you choose M = 3 offsets (i_m, j_m), each in [0, N). A pointer P starts at (0, 0). On
each turn you pick one offset m_t, move P by it with wrap-around (P ← ((P_i + i_m) mod N,
(P_j + j_m) mod N)) and place a talisman at P; then the haunting adds floor(d_t · √(t+1)) to the
danger D, where d_t is the ordinary (non-wrapping) Manhattan distance from (a_t, b_t) to the nearest
talisman placed so far (talismans placed this turn count). Minimise D: talismans should end up near
the cells that are haunted, early, because later hauntings weigh more (√(t+1)) and the haunting
order is known in advance.

Score per case = round(10^6 · N³ / (D + 1)) = round(10^12 / (D + 1)). Higher is better; 150 test
cases averaged.

Input: `N M` (100 3), then N² lines `a_t b_t`.
Output: M lines `i_m j_m`, then exactly N² lines `m_t` (0 ≤ m_t < M).

Edit `solution.cpp`. It is compiled with g++-12 (C++20) and run once per test case as

    ./a.out 2.0 < input.txt > output.txt

(2 s, 1024 MiB; argv[1] is the time limit). The seed sets all three offsets to (0, 0), so every
talisman stays on (0, 0) and d_t = a_t + b_t.
