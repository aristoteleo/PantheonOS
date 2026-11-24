// EVOLVE-BLOCK-START
// AHC058: Offline sequence optimizer over an initial greedy+rollout sequence.
// Phase 1: build initial legal sequence with frontier heuristic (greedy marginal).
// Phase 2: local search with simulated annealing over sequence mutations
//          (change/swap/move), simulating from earliest changed turn.
// Phase 3: sanitize output by replaying with fresh state (emit -1 if unaffordable).
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

int N, L, T; ll K;
vector<ll> A;
vector<vector<ll>> C;

struct State {
    i128 apples;
    vector<vector<ll>> B, P;
};

static inline void produce(State &s) {
    for (int j = 0; j < N; j++) s.apples += (i128)A[j] * s.B[0][j] * s.P[0][j];
    for (int i = 1; i < L; i++)
        for (int j = 0; j < N; j++)
            s.B[i-1][j] += s.B[i][j] * s.P[i][j];
}

static inline bool apply(State &s, int i, int j) {
    if (i < 0) return true;
    i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
    if (cost > s.apples) return false;
    s.apples -= cost;
    s.P[i][j] += 1;
    return true;
}

static inline long double score(const State &s, int i, int j, int R) {
    i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
    if (cost <= 0) return -1e30L;
    long double c = (long double)cost;
    long double g;
    if (i == 0) {
        g = (long double)A[j] * (long double)s.B[0][j] * (long double)R;
    } else {
        long double prod = 1.0L;
        int blockCount = 0;
        for (int k = 1; k < i; k++) {
            prod *= (long double)(s.P[k][j] + 1);
            if (s.P[k][j] == 0) blockCount++;
        }
        long double rp = 1.0L;
        for (int k = 0; k <= i; k++) rp *= (long double)R;
        long double fact = 2.0L;
        for (int k = 3; k <= i + 1; k++) fact *= (long double)k;
        long double p0 = (long double)max<ll>(s.P[0][j], 1);
        g = (long double)A[j] * p0 * (long double)s.B[i][j] * prod * rp / fact;
        for (int k = 0; k < blockCount; k++) g *= 0.35L;
    }
    return g / c;
}

static inline pair<int,int> policy(const State &s, int R) {
    int bi = -1, bj = -1;
    long double best = 0.0L;
    for (int i = 0; i < L; i++) {
        for (int j = 0; j < N; j++) {
            i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
            if (cost > s.apples) continue;
            long double v = score(s, i, j, R);
            if (v > best) { best = v; bi = i; bj = j; }
        }
    }
    return {bi, bj};
}

struct Action { int i, j; };

static void simulateAll(const State &s0, const vector<Action> &seq,
                        vector<State> &prefix, i128 &finalApples) {
    int n = (int)seq.size();
    prefix.assign(n + 1, State{});
    prefix[0] = s0;
    State cur = s0;
    for (int t = 0; t < n; t++) {
        int i = seq[t].i, j = seq[t].j;
        if (i >= 0) {
            i128 cost = (i128)C[i][j] * (cur.P[i][j] + 1);
            if (cost <= cur.apples) {
                cur.apples -= cost;
                cur.P[i][j] += 1;
            }
        }
        produce(cur);
        prefix[t + 1] = cur;
    }
    finalApples = cur.apples;
}

int main(int argc, char **argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    cin >> N >> L >> T >> K;
    A.assign(N, 0);
    for (auto &a : A) cin >> a;
    C.assign(L, vector<ll>(N));
    for (auto &row : C) for (auto &c : row) cin >> c;

    State s0{ (i128)K,
             vector<vector<ll>>(L, vector<ll>(N, 1)),
             vector<vector<ll>>(L, vector<ll>(N, 0)) };

    // ---- Phase 1: initial greedy sequence ----
    vector<Action> seq(T, Action{-1, -1});
    {
        State s = s0;
        for (int t = 0; t < T; t++) {
            auto [gi, gj] = policy(s, T - t);
            if (gi >= 0) { apply(s, gi, gj); seq[t] = Action{gi, gj}; }
            else seq[t] = Action{-1, -1};
            produce(s);
        }
    }

    vector<State> prefix;
    i128 bestApples;
    simulateAll(s0, seq, prefix, bestApples);
    vector<Action> bestSeq = seq;

    auto timeLeft = [&]() {
        return tl - chrono::duration<double>(chrono::steady_clock::now() - t0).count();
    };

    auto recomputePrefix = [&](int from) {
        State cur = prefix[from];
        for (int t = from; t < T; t++) {
            int i = bestSeq[t].i, j = bestSeq[t].j;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (cur.P[i][j] + 1);
                if (cost <= cur.apples) {
                    cur.apples -= cost;
                    cur.P[i][j] += 1;
                }
            }
            produce(cur);
            prefix[t + 1] = cur;
        }
    };

    mt19937_64 rng(987654321ULL);

    // Simulate sequence from `from` using prefix[from]; returns final apples.
    auto simulateFrom = [&](const vector<Action> &sq, int from) -> i128 {
        State cur = prefix[from];
        for (int t = from; t < T; t++) {
            int i = sq[t].i, j = sq[t].j;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (cur.P[i][j] + 1);
                if (cost <= cur.apples) {
                    cur.apples -= cost;
                    cur.P[i][j] += 1;
                }
            }
            produce(cur);
        }
        return cur.apples;
    };

    auto randomAction = [&]() -> Action {
        if ((rng() & 7) == 0) return Action{-1, -1};
        return Action{(int)(rng() % L), (int)(rng() % N)};
    };

    // Simulated annealing parameters
    double totalBudget = tl;
    int iter = 0;
    // We use a base absolute apple-scale temperature. Apples can be huge, so scale
    // by sqrt of current best apples to get a reasonable magnitude.
    auto startT = chrono::steady_clock::now();

    while (timeLeft() > 0.02) {
        iter++;
        vector<Action> cand = bestSeq;
        int type = rng() % 3;
        int earliest = T;

        if (type == 0) {
            int t = rng() % T;
            cand[t] = randomAction();
            earliest = t;
        } else if (type == 1) {
            int a = rng() % T, b = rng() % T;
            if (a == b) continue;
            swap(cand[a], cand[b]);
            earliest = min(a, b);
        } else {
            int a = rng() % T, b = rng() % T;
            if (a == b) continue;
            Action tmp = cand[a];
            if (a < b) {
                for (int k = a; k < b; k++) cand[k] = cand[k + 1];
                cand[b] = tmp;
            } else {
                for (int k = a; k > b; k--) cand[k] = cand[k - 1];
                cand[b] = tmp;
            }
            earliest = min(a, b);
        }

        i128 candApples = simulateFrom(cand, earliest);

        bool accept = false;
        if (candApples > bestApples) accept = true;
        else if (candApples == bestApples) {
            accept = (rng() & 3) == 0; // accept equal occasionally
        } else {
            // SA: accept worse with small probability
            // Progress fraction from start
            double elapsed = chrono::duration<double>(
                chrono::steady_clock::now() - startT).count();
            double frac = elapsed / max(0.001, totalBudget);
            if (frac < 0.5) {
                // temperature decreases
                double temp = (1.0 - frac * 2.0);
                // log-scale difference
                long double diff = logl((long double)bestApples + 1.0L)
                                 - logl((long double)candApples + 1.0L);
                double p = exp((double)(-diff / max(1e-9, temp * 0.0005)));
                if ((double)(rng() % 1000000) / 1000000.0 < p) accept = true;
            }
        }

        if (accept && candApples != bestApples) {
            bestSeq = cand;
            bestApples = candApples;
            recomputePrefix(earliest);
        } else if (accept) {
            bestSeq = cand;
            recomputePrefix(earliest);
        }
    }

    // Sanitize and emit
    {
        State cur = s0;
        for (int t = 0; t < T; t++) {
            int i = bestSeq[t].i, j = bestSeq[t].j;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (cur.P[i][j] + 1);
                if (cost <= cur.apples) {
                    cout << i << " " << j << "\n";
                    cur.apples -= cost;
                    cur.P[i][j] += 1;
                } else {
                    cout << "-1\n";
                }
            } else {
                cout << "-1\n";
            }
            produce(cur);
        }
    }
    return 0;
}
// EVOLVE-BLOCK-END
