AtCoder Heuristic Contest 024 — Topological Map.

A 50×50 grid map of m = 100 wards: cell (i,j) has color c_{i,j} in 1..m, each color's cells are
connected, and the outside of the grid is color 0. Produce a new 50×50 map d_{i,j} in 0..m such
that (1) every color 0..m is connected (color 0 may connect through the outside), and (2) for every
pair of colors c < d, "some c-cell is edge-adjacent to some d-cell" holds in the new map exactly
when it holds in the original (the outside counts as color 0, so a ward touching the border in the
original must touch a 0-cell or the border in the new map, and vice versa). Score = E + 1 where E
is the number of 0-cells in the new map: shrink the wards as much as the adjacency structure
allows. Higher is better; 150 test cases averaged. An illegal map scores 0.

Input: `n m` then n rows of n integers c_{i,j}. Output: n rows of n integers d_{i,j}. If several
maps are printed only the last is scored.

Edit `solution.cpp`. It is compiled with g++-12 (C++20) and run once per test case as

    ./a.out 2.0 < input.txt > output.txt

(2 s, 1024 MiB; argv[1] is the time limit). The seed prints the input map unchanged (score 1);
the whole gain lies in erasing cells to 0 while keeping every ward connected and the ward-adjacency
graph identical.
