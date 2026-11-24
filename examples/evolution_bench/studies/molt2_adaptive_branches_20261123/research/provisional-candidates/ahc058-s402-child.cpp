// EVOLVE-BLOCK-START
// AHC058: Offline sequence optimizer over an initial greedy+rollout sequence.
// Phase 1: build initial legal sequence with frontier heuristic.
// Phase 2: local search with simulated annealing over sequence mutations
//          (change/swap/move), simulating from earliest changed turn.
// Phase 3: sanitize output by replaying with fresh state (emit -1 if unaffordable).
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

static int N, L, T; static ll K;
static vector<ll> A;
static vector<vector<ll>> C;

struct State {
    i128 apples;
    vector<vector<ll>> B, P;
};

static inline void produce(State &s) {
    for (int j = 0; j < N; j++) s.apples += (i128)A[j] * (i128)s.B[0][j] * (i128)s.P[0][j];
    for (int i = 1; i < L; i++)
        for (int j = 0; j < N; j++)
            s.B[i-1][j] += s.B[i][j] * s.P[i][j];
}

static inline bool apply(State &s, int i, int j) {
    if (i < 0) return true;
    i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
    if (cost > s.apples) return false;
    s.apples -= cost;
    s.P[i][j] += 1;
    return true;
}

static inline long double hscore(const State &s, int i, int j, int R) {
    i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
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
            i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
            if (cost > s.apples) continue;
            long double v = hscore(s, i, j, R);
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
            i128 cost = (i128)C[i][j] * (i128)(cur.P[i][j] + 1);
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
    if (!(cin >> N >> L >> T >> K)) return 0;
    A.assign(N, 0);
    for (auto &a : A) cin >> a;
    C.assign(L, vector<ll>(N));
    for (auto &row : C) for (auto &c : row) cin >> c;

    State s0;
    s0.apples = (i128)K;
    s0.B.assign(L, vector<ll>(N, 1));
    s0.P.assign(L, vector<ll>(N, 0));

    // ---- Phase 1: greedy initial sequence with frontier heuristic ----
    vector<Action> seq(T, Action{-1, -1});
    {
        State s = s0;
        for (int t = 0; t < T; t++) {
            auto pr = policy(s, T - t);
            int gi = pr.first, gj = pr.second;
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

    auto rebuildPrefixFrom = [&](int from) {
        State cur = prefix[from];
        for (int t = from; t < T; t++) {
            int i = bestSeq[t].i, j = bestSeq[t].j;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (i128)(cur.P[i][j] + 1);
                if (cost <= cur.apples) {
                    cur.apples -= cost;
                    cur.P[i][j] += 1;
                }
            }
            produce(cur);
            prefix[t + 1] = cur;
        }
    };

    auto simulateFrom = [&](const vector<Action> &sq, int from) -> i128 {
        State cur = prefix[from];
        for (int t = from; t < T; t++) {
            int i = sq[t].i, j = sq[t].j;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (i128)(cur.P[i][j] + 1);
                if (cost <= cur.apples) {
                    cur.apples -= cost;
                    cur.P[i][j] += 1;
                }
            }
            produce(cur);
        }
        return cur.apples;
    };

    mt19937_64 rng(0x9E3779B97F4A7C15ULL);

    auto randomAction = [&]() -> Action {
        // Mostly pick a plausible action, occasionally do-nothing.
        if ((rng() & 15) == 0) return Action{-1, -1};
        return Action{(int)(rng() % L), (int)(rng() % N)};
    };

    long double bestLog = logl((long double)bestApples + 1.0L);
    double startTime = 0.0;
    double totalBudget = max(0.05, tl - 0.05);

    vector<Action> cand;
    cand.resize(T);

    int iters = 0;
    while (timeLeft() > 0.02) {
        iters++;
        // Handle T==1 edge case in mutation choice.
        cand = bestSeq;
        int type = (int)(rng() % 3);
        int earliest = T;

        if (type == 0) {
            int t = (int)(rng() % T);
            cand[t] = randomAction();
            earliest = t;
        } else if (type == 1) {
            if (T < 2) continue;
            int a = (int)(rng() % T), b = (int)(rng() % T);
            if (a == b) continue;
            swap(cand[a], cand[b]);
            earliest = min(a, b);
        } else {
            if (T < 2) continue;
            int a = (int)(rng() % T), b = (int)(rng() % T);
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
        if (candApples > bestApples) {
            accept = true;
        } else if (candApples == bestApples) {
            accept = ((rng() & 7) == 0);
        } else {
            double elapsed = chrono::duration<double>(
                chrono::steady_clock::now() - t0).count();
            double frac = elapsed / totalBudget;
            if (frac < 0.6 && candApples > 0) {
                long double diff = bestLog - logl((long double)candApples + 1.0L);
                // Temperature scheme: start ~ 0.01 log-units, cool down
                double temp = 0.01 * (1.0 - frac / 0.6);
                if (temp > 1e-9) {
                    long double p = expl(-diff / (long double)temp);
                    if (p > 0.0L) {
                        double u = (double)(rng() % 1000000ULL) / 1000000.0;
                        if ((long double)u < p) accept = true;
                    }
                }
            }
        }

        if (accept) {
            bool improved = (candApples > bestApples);
            bestSeq = cand;
            bestApples = candApples;
            bestLog = logl((long double)bestApples + 1.0L);
            rebuildPrefixFrom(earliest);
            (void)improved;
        }
    }

    // ---- Phase 3: sanitize output by replaying with fresh state ----
    {
        State cur = s0;
        for (int t = 0; t < T; t++) {
            int i = bestSeq[t].i, j = bestSeq[t].j;
            bool ok = false;
            if (i >= 0 && i < L && j >= 0 && j < N) {
                i128 cost = (i128)C[i][j] * (i128)(cur.P[i][j] + 1);
                if (cost <= cur.apples) {
                    cur.apples -= cost;
                    cur.P[i][j] += 1;
                    ok = true;
                }
            }
            if (ok) cout << i << " " << j << "\n";
            else cout << "-1\n";
            produce(cur);
        }
    }
    cout.flush();
    return 0;
}
// EVOLVE-BLOCK-END
