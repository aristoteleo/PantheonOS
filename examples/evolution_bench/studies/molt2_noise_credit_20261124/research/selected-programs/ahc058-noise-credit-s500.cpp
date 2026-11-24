// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

static int N, L, T;
static ll K;
static vector<ll> A;
static vector<vector<ll>> C;

struct State {
    i128 apples;
    vector<vector<ll>> B, P;
};

static State initState() {
    State s;
    s.apples = (i128)K;
    s.B.assign(L, vector<ll>(N, 1));
    s.P.assign(L, vector<ll>(N, 0));
    return s;
}

static inline void produce(State& s) {
    for (int j = 0; j < N; j++) s.apples += (i128)A[j] * s.B[0][j] * s.P[0][j];
    for (int i = 1; i < L; i++) for (int j = 0; j < N; j++) s.B[i-1][j] += s.B[i][j] * s.P[i][j];
}

static inline i128 costOf(const State& s, int i, int j) {
    return (i128)C[i][j] * (i128)(s.P[i][j] + 1);
}

static long double marginalValue(const State& s, int i, int j, int r) {
    if (r <= 0) return 0.0L;
    long double Aj = (long double)A[j];
    if (i == 0) {
        return Aj * (long double)s.B[0][j] * (long double)r;
    }
    long double prod = 1.0L;
    for (int k = 0; k < i; k++) prod *= (long double)(s.P[k][j] + 1);
    long double binom = 1.0L;
    for (int k = 0; k <= i; k++) binom *= (long double)(r - k);
    for (int k = 2; k <= i + 1; k++) binom /= (long double)k;
    if (binom < 0) binom = 0;
    return Aj * (long double)s.B[i][j] * prod * binom;
}

static pair<int,int> greedyBest(const State& s, int r) {
    int bi = -1, bj = -1;
    long double bestRatio = 0.0L;
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
        i128 cost = costOf(s, i, j);
        if (cost > s.apples || cost <= 0) continue;
        long double val = marginalValue(s, i, j, r);
        if (val <= 1e-18L) continue;
        long double ratio = val / (long double)cost;
        if (ratio > bestRatio) { bestRatio = ratio; bi = i; bj = j; }
    }
    return {bi, bj};
}

static i128 simulate(const vector<pair<int,int>>& seq) {
    State s = initState();
    for (int t = 0; t < T; t++) {
        int i = seq[t].first, j = seq[t].second;
        if (i >= 0) {
            if (i >= L || j < 0 || j >= N) return (i128)-1;
            i128 c = costOf(s, i, j);
            if (c > s.apples) return (i128)-1;
            s.apples -= c;
            s.P[i][j]++;
        }
        produce(s);
    }
    return s.apples;
}

int main(int argc, char** argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    cin >> N >> L >> T >> K;
    A.assign(N, 0);
    for (auto& a : A) cin >> a;
    C.assign(L, vector<ll>(N, 0));
    for (auto& row : C) for (auto& c : row) cin >> c;

    // ---- Phase 1: greedy policy with marginal ratio (no expensive rollout) ----
    State s = initState();
    vector<pair<int,int>> actions;
    actions.reserve(T);

    for (int t = 0; t < T; t++) {
        int r = T - t;
        int bi = -1, bj = -1;
        long double bestRatio = 0.0L;
        for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
            i128 c = costOf(s, i, j);
            if (c > s.apples || c <= 0) continue;
            long double v = marginalValue(s, i, j, r);
            if (v <= 1e-18L) continue;
            long double ratio = v / (long double)c;
            if (ratio > bestRatio) { bestRatio = ratio; bi = i; bj = j; }
        }
        if (bi >= 0) {
            i128 c = costOf(s, bi, bj);
            if (c <= s.apples) { s.apples -= c; s.P[bi][bj]++; }
            else { bi = -1; bj = -1; }
        }
        produce(s);
        actions.push_back({bi, bj});
    }

    i128 initApples = s.apples;
    vector<pair<int,int>> bestSeq = actions;
    i128 bestApples = initApples;

    // Prefix states (prefixState[t] = state BEFORE turn t)
    vector<State> prefixState(T + 1);
    prefixState[0] = initState();
    for (int t = 0; t < T; t++) {
        State st = prefixState[t];
        int i = bestSeq[t].first, j = bestSeq[t].second;
        if (i >= 0) {
            i128 c = costOf(st, i, j);
            if (c <= st.apples) { st.apples -= c; st.P[i][j]++; }
        }
        produce(st);
        prefixState[t+1] = st;
    }

    vector<pair<int,int>> curSeq = bestSeq;
    i128 curApples = bestApples;

    mt19937_64 rng(0x9E3779B97F4A7C15ULL ^ (uint64_t)chrono::steady_clock::now().time_since_epoch().count());

    // Precompute cumulative prefix: simulate forward from a prefix and update prefixState[t+1..T]
    // to avoid re-simulating from scratch on every accepted move.
    auto rebuildPrefix = [&](int start) {
        State s2 = prefixState[start];
        for (int tt = start; tt < T; tt++) {
            int ii = curSeq[tt].first, jj = curSeq[tt].second;
            if (ii >= 0) {
                i128 c = costOf(s2, ii, jj);
                if (c <= s2.apples) { s2.apples -= c; s2.P[ii][jj]++; }
            }
            produce(s2);
            prefixState[tt+1] = s2;
        }
    };

    // Simulate forward from prefixState[from] applying curSeq[from..T), returning apples or -1.
    // Does NOT modify global prefixState.
    auto simForward = [&](int from) -> i128 {
        State s2 = prefixState[from];
        for (int tt = from; tt < T; tt++) {
            int ii = curSeq[tt].first, jj = curSeq[tt].second;
            if (ii >= 0) {
                if (ii >= L || jj < 0 || jj >= N) return (i128)-1;
                i128 c = costOf(s2, ii, jj);
                if (c > s2.apples) return (i128)-1;
                s2.apples -= c;
                s2.P[ii][jj]++;
            }
            produce(s2);
        }
        return s2.apples;
    };

    // Precompute list of "useful" actions weighted by cheapness: level 0 actions are usually cheap.
    // Store (i,j) candidates biased toward lower levels and cheap cost.
    long long iters = 0;
    int noImprove = 0;
    const int NOIMPROVE_LIMIT = 6000;
    const int RESTART_LIMIT = 6;
    int restarts = 0;

    // Probability of accepting a worsening move (SA-lite). Decays over time.
    double baseWorsen = 0.02;

    while (true) {
        if ((iters & 31) == 0) {
            double elapsed = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
            if (elapsed > tl * 0.97) break;
        }
        iters++;

        // Choose move type:
        //  - 70%: replace a turn's action
        //  - 15%: swap two turns
        //  - 15%: replace with -1 (skip) or shift action
        int op = (int)(rng() % 100);
        int t = (int)(rng() % T);
        pair<int,int> oldAct = curSeq[t];
        pair<int,int> newAct;
        int t2 = -1;
        pair<int,int> oldAct2;

        if (op < 70) {
            // Replace action. Bias toward lower levels (0 and 1), and toward j indices
            // that haven't been used much — but we don't track that, so just random.
            int lvlRoll = (int)(rng() % 100);
            int i;
            if (lvlRoll < 55) i = 0;
            else if (lvlRoll < 80) i = 1;
            else if (lvlRoll < 93) i = 2;
            else i = 3;
            if ((rng() % 100) < 5) i = -1; // small chance to do nothing
            if (i < 0) newAct = {-1, -1};
            else newAct = {i, (int)(rng() % N)};
        } else if (op < 85) {
            t2 = (int)(rng() % T);
            if (t2 == t) continue;
            oldAct2 = curSeq[t2];
            newAct = oldAct2;
        } else {
            // Replace with a fresh random action from full space
            int i = (int)(rng() % (L + 1));
            if (i == L) newAct = {-1, -1};
            else newAct = {i, (int)(rng() % N)};
        }

        if (t2 < 0 && newAct == oldAct) continue;
        if (t2 >= 0 && oldAct2 == oldAct) continue;

        curSeq[t] = newAct;
        if (t2 >= 0) curSeq[t2] = oldAct;

        int start = (t2 < 0) ? t : min(t, t2);
        i128 newApples = simForward(start);

        if (newApples < 0) {
            curSeq[t] = oldAct;
            if (t2 >= 0) curSeq[t2] = oldAct2;
            noImprove++;
        } else {
            bool accept = (newApples > curApples);
            if (!accept) {
                // SA-lite: accept worsening with small probability
                double elapsed = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
                double progress = elapsed / (tl * 0.97);
                double Tnow = baseWorsen * (1.0 - progress) + 0.001;
                if (Tnow > 0) {
                    // Accept if relative loss is small.
                    // Use difference; only accept small absolute losses.
                    i128 diff = curApples - newApples;
                    // Accept if diff small enough relative to apples scale.
                    // Use log-scale: accept if diff / curApples < Tnow * something.
                    // Simpler: probabilistic acceptance based on relative loss.
                    if (curApples > 0) {
                        long double rel = (long double)diff / (long double)curApples;
                        if (rel < (long double)Tnow) accept = true;
                    }
                }
            }
            if (accept) {
                curApples = newApples;
                rebuildPrefix(start);
                if (newApples > bestApples) {
                    bestApples = newApples;
                    bestSeq = curSeq;
                    noImprove = 0;
                } else {
                    noImprove++;
                }
            } else {
                curSeq[t] = oldAct;
                if (t2 >= 0) curSeq[t2] = oldAct2;
                noImprove++;
            }
        }

        if (noImprove > NOIMPROVE_LIMIT) {
            restarts++;
            // If we've already had many restarts, tighten the SA and go back to best.
            curSeq = bestSeq;
            curApples = bestApples;
            if (restarts <= RESTART_LIMIT) {
                int nShakes = 15 + (int)(rng() % 30);
                for (int k = 0; k < nShakes; k++) {
                    int tt = (int)(rng() % T);
                    int i = (int)(rng() % (L + 1));
                    if (i == L) curSeq[tt] = {-1, -1};
                    else curSeq[tt] = {i, (int)(rng() % N)};
                }
                i128 ca = simulate(curSeq);
                if (ca < 0) {
                    curSeq = bestSeq;
                    curApples = bestApples;
                } else {
                    curApples = ca;
                }
            }
            // Rebuild prefix from scratch.
            prefixState[0] = initState();
            for (int tt = 0; tt < T; tt++) {
                State s2 = prefixState[tt];
                int ii = curSeq[tt].first, jj = curSeq[tt].second;
                if (ii >= 0) {
                    i128 c = costOf(s2, ii, jj);
                    if (c <= s2.apples) { s2.apples -= c; s2.P[ii][jj]++; }
                }
                produce(s2);
                prefixState[tt+1] = s2;
            }
            noImprove = 0;
        }
    }

    // Final validity check.
    if (simulate(bestSeq) < 0) bestSeq.assign(T, {-1, -1});

    string out;
    out.reserve(T * 8);
    for (int t = 0; t < T; t++) {
        if (bestSeq[t].first < 0) out += "-1\n";
        else {
            out += to_string(bestSeq[t].first);
            out += ' ';
            out += to_string(bestSeq[t].second);
            out += '\n';
        }
    }
    fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
// EVOLVE-BLOCK-END
