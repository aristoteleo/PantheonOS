// AHC058 seed: greedy with a one-action lookahead.
// Each turn, for every affordable action (strengthen j^i, or do nothing) simulate the rest of the
// game under a fixed cheap policy (best payback level-0 upgrade when it pays off, else nothing)
// and keep the action whose rollout ends with the most apples. The rollout policy is deliberately
// simple; the point of the seed is to be valid and non-trivial.
#include <bits/stdc++.h>
using namespace std;
typedef long long ll; typedef __int128 i128;

int N, L, T; ll K;
vector<ll> A; vector<vector<ll>> C;

struct State {
    i128 apples; vector<vector<ll>> B, P;
};

// EVOLVE-BLOCK-START
static inline void produce(State &s) {
    for (int j = 0; j < N; j++) s.apples += (i128)A[j] * s.B[0][j] * s.P[0][j];
    for (int i = 1; i < L; i++) for (int j = 0; j < N; j++) s.B[i-1][j] += s.B[i][j] * s.P[i][j];
}
static inline bool apply(State &s, int i, int j) {
    if (i < 0) return true;
    i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
    if (cost > s.apples) return false;
    s.apples -= cost; s.P[i][j] += 1; return true;
}
// rollout policy: the level-0 upgrade with the shortest payback, if it pays back before the end
static pair<int,int> policy(const State &s, int turnsLeft) {
    int bi = -1, bj = -1; long double best = 1e30L;
    for (int j = 0; j < N; j++) {
        i128 cost = (i128)C[0][j] * (s.P[0][j] + 1);
        if (cost > s.apples) continue;
        long double gain = (long double)A[j] * (long double)s.B[0][j];
        if (gain <= 0) continue;
        long double payback = (long double)cost / gain;
        if (payback < best && payback < turnsLeft) { best = payback; bi = 0; bj = j; }
    }
    return {bi, bj};
}
static i128 rollout(State s, int turn, int i0, int j0) {
    if (!apply(s, i0, j0)) return -1;
    produce(s);
    for (int t = turn + 1; t < T; t++) {
        auto [i, j] = policy(s, T - t);
        apply(s, i, j); produce(s);
    }
    return s.apples;
}
// EVOLVE-BLOCK-END

int main(int argc, char **argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0; auto t0 = chrono::steady_clock::now();
    cin >> N >> L >> T >> K; A.assign(N, 0); for (auto &a : A) cin >> a;
    C.assign(L, vector<ll>(N)); for (auto &row : C) for (auto &c : row) cin >> c;
    State s{ (i128)K, vector<vector<ll>>(L, vector<ll>(N, 1)), vector<vector<ll>>(L, vector<ll>(N, 0)) };
    vector<string> out;
    for (int t = 0; t < T; t++) {
        int bi = -1, bj = -1; i128 best = rollout(s, t, -1, -1);
        double elapsed = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
        if (elapsed < tl * 0.85) {
            for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
                i128 v = rollout(s, t, i, j);
                if (v > best) { best = v; bi = i; bj = j; }
            }
        } else { auto [i, j] = policy(s, T - t); bi = i; bj = j; }
        if (!apply(s, bi, bj)) { bi = -1; bj = -1; }
        produce(s);
        out.push_back(bi < 0 ? string("-1") : to_string(bi) + " " + to_string(bj));
    }
    for (auto &l : out) cout << l << "\n";
    return 0;
}
