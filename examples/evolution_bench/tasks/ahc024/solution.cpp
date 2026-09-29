// AHC024 seed: the identity map, then greedy erasure of cells to color 0 while a full check keeps
// every ward connected and the adjacency graph (including the outside, color 0) identical.
#include <bits/stdc++.h>
using namespace std;

int n, m;
vector<vector<int>> orig, cur;
const int di[4] = {-1, 1, 0, 0}, dj[4] = {0, 0, -1, 1};

// EVOLVE-BLOCK-START
static int at(const vector<vector<int>> &g, int i, int j) { return (i < 0 || j < 0 || i >= n || j >= n) ? 0 : g[i][j]; }

static vector<vector<char>> adjacency(const vector<vector<int>> &g) {
    vector<vector<char>> A(m + 1, vector<char>(m + 1, 0));
    for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) for (int d = 0; d < 4; d++) {
        int a = g[i][j], b = at(g, i + di[d], j + dj[d]);
        if (a != b) A[a][b] = A[b][a] = 1;
    }
    return A;
}
static bool connectedAll(const vector<vector<int>> &g) {
    // every color 1..m connected; color 0 connected when the outside ring is added as one node
    vector<int> cnt(m + 1, 0), seen(m + 1, 0);
    for (auto &r : g) for (int c : r) cnt[c]++;
    vector<vector<char>> vis(n, vector<char>(n, 0));
    for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) if (!vis[i][j] && g[i][j] != 0) {
        int c = g[i][j]; if (seen[c]) return false; seen[c] = 1;
        queue<pair<int,int>> q; q.push({i, j}); vis[i][j] = 1; int k = 0;
        while (!q.empty()) { auto [x, y] = q.front(); q.pop(); k++;
            for (int d = 0; d < 4; d++) { int a = x + di[d], b = y + dj[d];
                if (a >= 0 && b >= 0 && a < n && b < n && !vis[a][b] && g[a][b] == c) { vis[a][b] = 1; q.push({a, b}); } } }
        if (k != cnt[c]) return false;
    }
    // color 0: all zero cells must reach the border
    queue<pair<int,int>> q; int z = 0;
    for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) if (g[i][j] == 0 && (i == 0 || j == 0 || i == n - 1 || j == n - 1)) { vis[i][j] = 1; q.push({i, j}); z++; }
    while (!q.empty()) { auto [x, y] = q.front(); q.pop();
        for (int d = 0; d < 4; d++) { int a = x + di[d], b = y + dj[d];
            if (a >= 0 && b >= 0 && a < n && b < n && !vis[a][b] && g[a][b] == 0) { vis[a][b] = 1; q.push({a, b}); z++; } } }
    return z == cnt[0];
}
static void improve(double tl, chrono::steady_clock::time_point t0) {
    auto A0 = adjacency(orig);
    bool changed = true;
    while (changed) {
        changed = false;
        for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) {
            if (cur[i][j] == 0) continue;
            if (chrono::duration<double>(chrono::steady_clock::now() - t0).count() > tl * 0.8) return;
            int keep = cur[i][j]; cur[i][j] = 0;
            if (adjacency(cur) == A0 && connectedAll(cur)) changed = true; else cur[i][j] = keep;
        }
    }
}
// EVOLVE-BLOCK-END

int main(int argc, char **argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0; auto t0 = chrono::steady_clock::now();
    cin >> n >> m; orig.assign(n, vector<int>(n)); for (auto &r : orig) for (auto &c : r) cin >> c;
    cur = orig;
    improve(tl, t0);
    for (int i = 0; i < n; i++) { for (int j = 0; j < n; j++) cout << cur[i][j] << (j + 1 < n ? " " : "\n"); }
    return 0;
}
