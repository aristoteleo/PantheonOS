AtCoder Heuristic Contest 071 — Brick Cover.

A wall W = 60 cells wide and H = 40 cells high; x grows to the right, y grows upward, the floor is
below y = 0. K = 120 distinct hole cells (a_j, b_j) must each be covered by a brick. Bricks are one
cell high with width ℓ ∈ {1, 3, 5, 7, 9}, unlimited copies, no rotation. A brick at (x, y) of width ℓ
covers cells x … x+ℓ−1 of row y and must satisfy 0 ≤ x, x + ℓ ≤ W, 0 ≤ y < H. Bricks may not overlap
(touching edges is fine). A brick is supported if y = 0 or the cell under its centre,
(x + (ℓ−1)/2, y − 1), is covered by another brick; every brick must be supported. Brick costs:
c_1 = 5 and c_3, c_5, c_7, c_9 are given per case (c_ℓ is drawn from [2ℓ−1, 2ℓ+4], so a wider brick
is not always dearer per cell). Minimise the total cost C of the bricks placed.

Score per case = max(0, W·H·c_1 − C + 1) = max(0, 12001 − C). Higher is better; 150 test cases
averaged.

Input: `W H K` (60 40 120), then `c_1 c_3 c_5 c_7 c_9`, then K lines `a_j b_j` (x then y).
Output: `M` (number of bricks, M ≤ 2400), then M lines `x y ℓ` in any order. Several complete
answers may be printed one after another; only the last one is scored.

Edit `solution.cpp`. It is compiled with g++-12 (C++20) and run once per test case as

    ./a.out 2.0 < input.txt > output.txt

(2 s, 1024 MiB; argv[1] is the time limit). The seed stacks width-1 bricks in every column that has
a hole, from the floor up to that column's highest hole.
