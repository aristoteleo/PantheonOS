AtCoder Heuristic Contest 026 — Stack of Boxes.

n = 200 boxes numbered 1..n sit in m = 10 stacks of 20 (given bottom to top). Carry the boxes out
in increasing number using at most 5000 operations: (1) move box v together with everything above
it onto the top of another stack i, costing k+1 energy where k is the number of boxes moved (moving
onto its own stack is legal but pure waste); (2) carry out box v, allowed only when v is the
smallest remaining box and is on top of its stack, costing nothing. Score = max(1, 10000 − V) where
V is the total energy; higher is better; 150 test cases averaged. Any illegal operation, or not
carrying out every box, scores 0.

Input: `n m` then m rows of n/m integers (stack i, bottom to top). Output: one operation per line,
`v i` for a move and `v 0` for carrying out box v.

Edit `solution.cpp`. It is compiled with g++-12 (C++20) and run once per test case as

    ./a.out 2.0 < input.txt > output.txt

(2 s, 1024 MiB; argv[1] is the time limit). The seed is the plain greedy: for each box in order,
dump the boxes above it onto the stack with the highest top, then carry it out.
