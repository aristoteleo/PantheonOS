// AHC046 seed: visit the targets in order by plain Moves along Manhattan paths (no blocks, no
// slides). Always legal on an empty rink; T = total Manhattan distance.
#include <bits/stdc++.h>
using namespace std;

int N, M;
vector<pair<int,int>> tg;

// EVOLVE-BLOCK-START
static void leg(int ci, int cj, int ti, int tj, vector<string> &out) {
    while (ci != ti) { out.push_back(ci < ti ? "M D" : "M U"); ci += (ci < ti ? 1 : -1); }
    while (cj != tj) { out.push_back(cj < tj ? "M R" : "M L"); cj += (cj < tj ? 1 : -1); }
}
static vector<string> plan() {
    vector<string> out;
    for (int k = 1; k < M; k++) leg(tg[k-1].first, tg[k-1].second, tg[k].first, tg[k].second, out);
    return out;
}
// EVOLVE-BLOCK-END

int main(int argc, char **argv) {
    cin >> N >> M; tg.resize(M); for (auto &p : tg) cin >> p.first >> p.second;
    for (auto &l : plan()) cout << l << "\n";
    return 0;
}
