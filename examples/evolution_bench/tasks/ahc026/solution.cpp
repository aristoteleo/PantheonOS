// AHC026 seed: for v = 1..n, if v is buried, move the boxes above it (in one operation) onto the
// other stack whose top box has the largest number, then carry v out.
#include <bits/stdc++.h>
using namespace std;

int n, m;
vector<vector<int>> st;   // stacks, bottom to top

// EVOLVE-BLOCK-START
static int chooseDest(int from) {
    int best = -1, bestTop = -1;
    for (int i = 0; i < m; i++) {
        if (i == from) continue;
        int top = st[i].empty() ? n + 1 : st[i].back();   // empty stacks are as good as it gets
        if (top > bestTop) { bestTop = top; best = i; }
    }
    return best;
}
static void solve(vector<pair<int,int>> &ops) {
    vector<int> where(n + 1, -1);
    for (int i = 0; i < m; i++) for (int v : st[i]) where[v] = i;
    for (int v = 1; v <= n; v++) {
        int s = where[v];
        int pos = find(st[s].begin(), st[s].end(), v) - st[s].begin();
        if (pos + 1 < (int)st[s].size()) {
            int dest = chooseDest(s); int above = st[s][pos + 1];
            for (int k = pos + 1; k < (int)st[s].size(); k++) { st[dest].push_back(st[s][k]); where[st[s][k]] = dest; }
            st[s].resize(pos + 1);
            ops.push_back({above, dest + 1});
        }
        st[s].pop_back(); ops.push_back({v, 0});
    }
}
// EVOLVE-BLOCK-END

int main(int argc, char **argv) {
    cin >> n >> m; st.assign(m, {});
    for (int i = 0; i < m; i++) for (int k = 0; k < n / m; k++) { int b; cin >> b; st[i].push_back(b); }
    vector<pair<int,int>> ops; solve(ops);
    for (auto &[v, i] : ops) cout << v << " " << i << "\n";
    return 0;
}
