// AHC070 seed: all three offsets are (0, 0), so P never moves and every talisman sits on (0, 0).
// Always legal; danger = sum of floor((a_t + b_t) * sqrt(t + 1)).
#include <bits/stdc++.h>
using namespace std;

int N, M;
vector<pair<int,int>> haunt;

// EVOLVE-BLOCK-START
struct Plan { vector<pair<int,int>> offs; vector<int> pick; };
static Plan plan() {
    Plan p;
    p.offs.assign(M, {0, 0});
    p.pick.assign(N * N, 0);
    return p;
}
// EVOLVE-BLOCK-END

int main(int argc, char **argv) {
    cin >> N >> M; haunt.resize(N * N); for (auto &h : haunt) cin >> h.first >> h.second;
    Plan p = plan();
    string out;
    for (auto &o : p.offs) out += to_string(o.first) + " " + to_string(o.second) + "\n";
    for (int m : p.pick) out += to_string(m) + "\n";
    fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
