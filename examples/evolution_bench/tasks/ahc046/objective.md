AtCoder Heuristic Contest 046 — Skating with Blocks.

An N×N rink (N = 20), everything outside it is blocked, nothing inside is blocked at first. Start at
(i_0, j_0) and visit the M−1 = 39 target squares (i_1, j_1) … (i_39, j_39) in order. Each turn pick a
direction (U/D/L/R) and one action: Move (one square; not into a block), Slide (keep going until the
square ahead is a block or the edge), or Alter (toggle a block on the adjacent square inside the
rink; a block may sit on a target but must be removed before visiting it). A target counts as
visited only when you stop on it by a Move or at the end of a Slide, and only when it is the next
one in order. At most 2NM = 1600 actions. Score = M + 2NM − T (= 1640 − T) when all targets are
visited, where T is the number of actions; otherwise m + 1 for m targets visited. Higher is better;
150 test cases averaged. Fewer actions is the whole game: slides are one action regardless of
distance, and placed blocks become stopping points for later slides.

Input: `N M` then M lines `i_k j_k` (line 0 is the start). Output: one line per action, `A D` with
A in {M, S, A} and D in {U, D, L, R}.

Edit `solution.cpp`. It is compiled with g++-12 (C++20) and run once per test case as

    ./a.out 2.0 < input.txt > output.txt

(2 s, 1024 MiB; argv[1] is the time limit). The seed walks Manhattan paths with Move only and
never places a block (T = sum of the leg distances, about 1.0k actions).
