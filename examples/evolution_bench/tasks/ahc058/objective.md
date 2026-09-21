AtCoder Heuristic Contest 058 — Apple Incremental Game.

N = 10 machine types, L = 4 levels, T = 500 turns, K = 1 starting apple. Machine j at level i is
"j^i"; every machine starts with quantity B_{i,j} = 1 and power P_{i,j} = 0. Each turn you either
strengthen one machine j^i, paying C_{i,j} × (P_{i,j} + 1) apples and raising P_{i,j} by 1, or do
nothing. Then production runs level by level: level 0 adds A_j × B_{0,j} × P_{0,j} apples; for
levels i = 1..3, B_{i-1,j} increases by B_{i,j} × P_{i,j} (higher levels multiply the count of the
level below). Score = round(10^5 × log2(S)) with S the final apple count; higher is better, and the
150 public cases are averaged. Constraints: 1 ≤ A_j ≤ 100, 1 ≤ C_{i,j} ≤ 1.25 × 10^12.

Input: line 1 `N L T K`; line 2 the N values A_j; then L lines of N integers, row i giving
C_{i,j} for level i. Output exactly T lines: `i j` to strengthen machine j at level i (0-based),
or `-1` to do nothing. Strengthening into negative apples, a non-existent machine, or fewer than
T lines is invalid (score 0).

Edit `solution.cpp`. It is compiled with g++-12 (C++20) and run once per test case as

    ./a.out 2.0 < input.txt > output.txt

with argv[1] the time limit in seconds (2 s, 1024 MiB). Use 64-bit (or wider) integer arithmetic:
apple counts reach 10^12 and beyond.
