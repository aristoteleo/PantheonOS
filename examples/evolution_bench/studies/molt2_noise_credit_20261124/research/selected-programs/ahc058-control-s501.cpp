// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

static int N, L, T;
static ll K;
static vector<ll> A;
static vector<vector<ll>> C;
static vector<vector<i128>> combo;

struct St {
    i128 apples;
    vector<vector<ll>> B, P;
};

static i128 binom(int n, int k) {
    if (n < 0 || k < 0 || k > n) return 0;
    if (n <= T + 6 && k <= L + 6) return combo[n][k];
    if (k > n - k) k = n - k;
    i128 r = 1;
    for (int m = 0; m < k; m++) r = r * (n - m) / (m + 1);
    return r;
}

// Extra apples over `rem` future production phases if P[i][j] incremented by 1.
// For level 0: B[0][j] * A[j] * rem (linear growth per turn).
// For level i>0: each turn after injection, B[0][j] gains cascading contributions.
// If d >= 1 turns: contribution to B[0][j] at end of turn `k` is
//   B_extra(k) = prod_{m=1..i-1} P[m][j] * B[i][j] * C(k-1, i)   [choose cascade positions]
// Wait: the injection adds B[i][j]*1 to B[i-1][j] per turn, then that propagates.
// Simpler: The classic formula for injecting into an "i-th order accumulator":
//   after d turns, B0 gains B_i * prod(P_1..P_{i-1}) * C(d, i+1)
// We use the analytic target P[0][j] multiplier and sum production.
static i128 extraValue(const St &s, int i, int j, int rem) {
    if (rem <= 0) return 0;
    if (i == 0) return (i128)A[j] * s.B[0][j] * rem;
    // P[0] multiplier must be > 0 for this to matter
    if (s.P[0][j] == 0) return 0;
    i128 v = (i128)A[j] * s.P[0][j] * s.B[i][j];
    for (int m = 1; m < i; m++) v *= s.P[m][j];
    // Number of (d-independent) counted contributions = sum_{d=1..rem} C(d-1, i) = C(rem, i+1)
    v *= binom(rem, i + 1);
    return v;
}

static void produce(St &s) {
    for (int j = 0; j < N; j++)
        s.apples += (i128)A[j] * s.B[0][j] * s.P[0][j];
    for (int i = 1; i < L; i++)
        for (int j = 0; j < N; j++)
            s.B[i-1][j] += s.B[i][j] * s.P[i][j];
}

static bool applyAct(St &s, int i, int j) {
    if (i < 0) return true;
    i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
    if (cost > s.apples) return false;
    s.apples -= cost;
    s.P[i][j] += 1;
    return true;
}

// Greedy using ratio val/cost (value per apple spent) — akin to knapsack heuristic.
static pair<int,int> greedyChoice(const St &s, int rem) {
    int bi = -1, bj = -1;
    i128 bestVal = 0, bestCost = 1;
    for (int i = 0; i < L; i++) {
        for (int j = 0; j < N; j++) {
            i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
            if (cost > s.apples) continue;
            i128 val = extraValue(s, i, j, rem);
            if (val <= cost) continue;
            if (bi < 0 || val * bestCost > bestVal * cost) {
                bestVal = val; bestCost = cost; bi = i; bj = j;
            }
        }
    }
    return {bi, bj};
}

static i128 rollout(St s, int i0, int j0, int startTurn, int H) {
    if (!applyAct(s, i0, j0)) return (i128)-1;
    produce(s);
    for (int h = 1; h < H; h++) {
        int rem = T - (startTurn + h);
        if (rem <= 0) break;
        auto [i, j] = greedyChoice(s, rem);
        applyAct(s, i, j);
        produce(s);
    }
    // If we stop early (H < remaining), continue with cheap greedy to finish.
    for (int t = startTurn + H; t < T; t++) {
        int rem = T - t;
        auto [i, j] = greedyChoice(s, rem);
        applyAct(s, i, j);
        produce(s);
    }
    return s.apples;
}

int main(int argc, char **argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    scanf("%d %d %d %lld", &N, &L, &T, &K);
    A.assign(N, 0);
    for (auto& a : A) scanf("%lld", &a);
    C.assign(L, vector<ll>(N));
    for (int i = 0; i < L; i++)
        for (int j = 0; j < N; j++)
            scanf("%lld", &C[i][j]);

    combo.assign(T + 8, vector<i128>(L + 8, 0));
    for (int n = 0; n <= T + 7; n++) {
        combo[n][0] = 1;
        int kmax = min(n, L + 7);
        for (int k = 1; k <= kmax; k++)
            combo[n][k] = combo[n-1][k-1] + (k <= n-1 ? combo[n-1][k] : 0);
    }

    St s;
    s.apples = (i128)K;
    s.B.assign(L, vector<ll>(N, 1));
    s.P.assign(L, vector<ll>(N, 0));

    vector<pair<int,int>> out;
    out.reserve(T);

    const double GLOBAL = tl * 0.88;
    auto now = [&](){ return chrono::duration<double>(chrono::steady_clock::now() - t0).count(); };

    for (int t = 0; t < T; t++) {
        int rem = T - t;

        // Collect candidates sorted by value/cost.
        struct Cnd { int i, j; i128 val, cost; };
        vector<Cnd> cands;
        for (int i = 0; i < L; i++)
            for (int j = 0; j < N; j++) {
                i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
                if (cost > s.apples) continue;
                i128 val = extraValue(s, i, j, rem);
                if (val <= 0) continue;
                cands.push_back({i, j, val, cost});
            }
        sort(cands.begin(), cands.end(), [](const Cnd& a, const Cnd& b){
            return a.val * b.cost > b.val * a.cost;
        });

        int bi = -1, bj = -1;
        auto [gi, gj] = greedyChoice(s, rem);
        bi = gi; bj = gj;

        // Only rollout when there's meaningful branching.
        if (now() < GLOBAL && (int)cands.size() > 1) {
            int K2 = min((int)cands.size(), 8);
            // Restricted horizon; shorter horizon for later turns speeds up.
            int H = min(rem, 140);
            i128 best = (gi < 0)
                ? rollout(s, -1, -1, t, H)
                : rollout(s, gi, gj, t, H);
            // Verify baseline is best.
            if (gi < 0) { bi = -1; bj = -1; }
            for (int c = 0; c < K2; c++) {
                if (now() > GLOBAL) break;
                if (cands[c].i == gi && cands[c].j == gj) continue;
                i128 v = rollout(s, cands[c].i, cands[c].j, t, H);
                if (v > best) { best = v; bi = cands[c].i; bj = cands[c].j; }
            }
            // Try doing nothing once.
            if (now() < GLOBAL) {
                i128 vn = rollout(s, -1, -1, t, H);
                if (vn > best) { best = vn; bi = -1; bj = -1; }
            }
        }

        if (bi >= 0 && !applyAct(s, bi, bj)) { bi = -1; bj = -1; }
        produce(s);
        out.push_back({bi, bj});
    }

    string buf;
    buf.reserve(T * 8);
    for (auto& [i, j] : out) {
        if (i < 0) buf += "-1\n";
        else { buf += to_string(i); buf += ' '; buf += to_string(j); buf += '\n'; }
    }
    fwrite(buf.data(), 1, buf.size(), stdout);
    return 0;
}
// EVOLVE-BLOCK-END
