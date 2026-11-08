// AHC073 seed: draw the three circles on (0,0), (0,1), (0,2), never teleport, and walk each leg
// along a Manhattan path (rows first). Always legal; A = T, so the score is M + 1 per case.
#include <bits/stdc++.h>
using namespace std;

int N, M, K;
long long T;
pair<int,int> start;
vector<pair<int,int>> goal;

// EVOLVE-BLOCK-START
static void leg(int ci, int cj, int ti, int tj, vector<string> &out) {
    while (ci != ti) { out.push_back(ci < ti ? "D" : "U"); ci += (ci < ti ? 1 : -1); }
    while (cj != tj) { out.push_back(cj < tj ? "R" : "L"); cj += (cj < tj ? 1 : -1); }
}
static pair<vector<pair<int,int>>, vector<string>> plan() {
    vector<pair<int,int>> circles;
    for (int k = 0; k < K; k++) circles.push_back({0, k});
    vector<string> ops;
    auto cur = start;
    for (auto &g : goal) { leg(cur.first, cur.second, g.first, g.second, ops); cur = g; }
    return {circles, ops};
}
// EVOLVE-BLOCK-END

int main(int argc, char **argv) {
    cin >> N >> M >> K >> T >> start.first >> start.second;
    goal.resize(M); for (auto &g : goal) cin >> g.first >> g.second;
    auto [circles, ops] = plan();
    string out;
    for (auto &c : circles) out += to_string(c.first) + " " + to_string(c.second) + "\n";
    for (auto &o : ops) out += o + "\n";
    fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
