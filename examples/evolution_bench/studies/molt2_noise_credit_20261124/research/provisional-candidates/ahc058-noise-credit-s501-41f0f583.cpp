// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;
typedef long double ld;

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
            s.B[i-1][j] += (i128)s.B[i][j] * (i128)s.P[i][j];
}

static inline bool applyAct(State &s, int i, int j) {
    if (i < 0) return true;
    i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
    if (cost > s.apples) return false;
    s.apples -= cost; s.P[i][j] += 1;
    return true;
}

static i128 simSeq(const vector<pair<int,int>> &seq, State init) {
    State s = init;
    for (int t = 0; t < T; t++) {
        int i = seq[t].first, j = seq[t].second;
        if (i >= 0) {
            i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
            if (cost > s.apples) return -1;
            s.apples -= cost; s.P[i][j] += 1;
        }
        produce(s);
    }
    return s.apples;
}

static ld analyticValue(const State &s, int i, int j, ll H) {
    ld cost = (ld)C[i][j] * (ld)(s.P[i][j] + 1);
    if (cost <= 0) return 0;
    ld h = (ld)H;
    if (i == 0) {
        return ((ld)A[j] * (ld)s.B[0][j] * h) / cost;
    }
    ld amp = (ld)A[j] * (ld)s.P[0][j];
    for (int k = 1; k < i; k++) amp *= (ld)(s.P[k][j] + 1);
    ld tf = 1.0L;
    for (int k = 0; k <= i; k++) tf *= h / (ld)(k + 1);
    return amp * (ld)s.B[i][j] * tf / cost;
}

static vector<pair<int,int>> greedySeq(State s) {
    vector<pair<int,int>> seq;
    seq.reserve(T);
    for (int t = 0; t < T; t++) {
        ld best = 0;
        int bi = -1, bj = -1;
        ll H = (ll)(T - t);
        for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
            i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
            if (cost > s.apples) continue;
            ld v = analyticValue(s, i, j, H);
            if (v > best) { best = v; bi = i; bj = j; }
        }
        applyAct(s, bi, bj);
        produce(s);
        seq.emplace_back(bi, bj);
    }
    return seq;
}

int main(int argc, char **argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0;
    cin >> N >> L >> T >> K;
    A.assign(N, 0);
    for (auto &a : A) cin >> a;
    C.assign(L, vector<ll>(N));
    for (auto &row : C) for (auto &c : row) cin >> c;

    State init{(i128)K, vector<vector<ll>>(L, vector<ll>(N, 1)), vector<vector<ll>>(L, vector<ll>(N, 0))};

    // Phase 1: greedy sequence
    vector<pair<int,int>> seq = greedySeq(init);
    i128 bestVal = simSeq(seq, init);
    if (bestVal < 0) {
        seq.assign(T, {-1, -1});
        bestVal = simSeq(seq, init);
    }
    vector<pair<int,int>> bestSeq = seq;
    vector<pair<int,int>> curSeq = seq;
    i128 curVal = bestVal;

    // Maintain prefix states (state BEFORE turn t)
    vector<State> pre(T+1);
    {
        State s = init;
        pre[0] = s;
        for (int t = 0; t < T; t++) {
            int i = curSeq[t].first, j = curSeq[t].second;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
                if (cost <= s.apples) { s.apples -= cost; s.P[i][j] += 1; }
            }
            produce(s);
            pre[t+1] = s;
        }
    }

    auto t0 = chrono::steady_clock::now();
    auto deadline = t0 + chrono::duration<double>(tl * 0.94);
    auto timeLeft = [&]() -> double {
        return chrono::duration<double>(deadline - chrono::steady_clock::now()).count();
    };

    mt19937_64 rng(0xC0FFEEULL ^ (unsigned long long)t0.time_since_epoch().count());

    int actionSpace = L * N + 1;

    // Rebuild prefix states from index `from` using curSeq (pre[0..from] must be valid)
    auto rebuildPrefix = [&](int from) {
        State s = pre[from];
        for (int k = from; k < T; k++) {
            int i = curSeq[k].first, j = curSeq[k].second;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
                if (cost <= s.apples) { s.apples -= cost; s.P[i][j] += 1; }
            }
            produce(s);
            pre[k+1] = s;
        }
    };

    // Evaluate mutation at turn t: use pre[t] as start, then apply curSeq[t..T-1]
    // Assumes curSeq has already been updated at position t if needed.
    auto evalFrom = [&](int t) -> i128 {
        State s = pre[t];
        for (int k = t; k < T; k++) {
            int i = curSeq[k].first, j = curSeq[k].second;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
                if (cost > s.apples) return -1;
                s.apples -= cost; s.P[i][j] += 1;
            }
            produce(s);
        }
        return s.apples;
    };

    long long iter = 0;

    // Promising candidate actions using pre[t] and current P
    vector<pair<int,int>> promising;
    promising.reserve(actionSpace);

    auto sampleAction = [&](int t, int mode, int idx) -> pair<int,int> {
        // mode 0: pure random
        // mode 1: biased to promising (top analytic)
        // mode 2: neighbor of current
        if (mode == 0) {
            int r = (int)(rng() % (unsigned long long)actionSpace);
            return (r == 0) ? make_pair(-1,-1) : make_pair((r-1)/N, (r-1)%N);
        } else if (mode == 1) {
            // 50%: pick from existing seq at nearby turn
            // 50%: pick random
            int sub = (int)(rng() % 100);
            if (sub < 50) {
                int t2 = t + (int)(rng() % 7) - 3;
                if (t2 < 0) t2 = 0; if (t2 >= T) t2 = T-1;
                return curSeq[t2];
            }
            int r = (int)(rng() % (unsigned long long)actionSpace);
            return (r == 0) ? make_pair(-1,-1) : make_pair((r-1)/N, (r-1)%N);
        } else {
            // promising: sampled from top-k by analyticValue at t
            State &s = pre[t];
            ll H = (ll)(T - t);
            // sample a few and pick the best by analytic
            int bestI = -1, bestJ = -1;
            ld bestV = -1;
            int samples = 6;
            for (int k = 0; k < samples; k++) {
                int i = (int)(rng() % L);
                int j = (int)(rng() % N);
                i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
                if (cost > s.apples) continue;
                ld v = analyticValue(s, i, j, H);
                if (v > bestV) { bestV = v; bestI = i; bestJ = j; }
            }
            if (bestI < 0) {
                int r = (int)(rng() % (unsigned long long)actionSpace);
                return (r == 0) ? make_pair(-1,-1) : make_pair((r-1)/N, (r-1)%N);
            }
            return {bestI, bestJ};
        }
    };

    while (timeLeft() > 0) {
        iter++;
        int roll = (int)(rng() % 100);
        int turn = (int)(rng() % (unsigned long long)T);

        if (roll < 80) {
            // single-turn mutation
            int mode;
            int m = (int)(rng() % 100);
            if (m < 40) mode = 0;
            else if (m < 70) mode = 1;
            else mode = 2;
            pair<int,int> newAct = sampleAction(turn, mode, 0);
            if (newAct == curSeq[turn]) continue;
            pair<int,int> old = curSeq[turn];
            curSeq[turn] = newAct;
            i128 v = evalFrom(turn);
            if (v >= curVal) {
                curVal = v;
                rebuildPrefix(turn);
                if (v > bestVal) { bestVal = v; bestSeq = curSeq; }
            } else {
                curSeq[turn] = old;
            }
        } else if (roll < 95) {
            // 2-turn block move: swap/mutate consecutive turns together
            if (turn + 1 >= T) continue;
            pair<int,int> a = curSeq[turn], b = curSeq[turn+1];
            // Try swapping
            pair<int,int> na, nb;
            int m = (int)(rng() % 100);
            if (m < 50) {
                // swap
                na = b; nb = a;
            } else {
                // mutate both independently
                na = sampleAction(turn, 0, 0);
                nb = sampleAction(turn+1, 0, 0);
            }
            if (na == a && nb == b) continue;
            curSeq[turn] = na; curSeq[turn+1] = nb;
            i128 v = evalFrom(turn);
            if (v >= curVal) {
                curVal = v;
                rebuildPrefix(turn);
                if (v > bestVal) { bestVal = v; bestSeq = curSeq; }
            } else {
                curSeq[turn] = a; curSeq[turn+1] = b;
            }
        } else {
            // 3-turn perturbation swap
            if (turn + 2 >= T) continue;
            pair<int,int> a = curSeq[turn], b = curSeq[turn+1], c = curSeq[turn+2];
            // rotate
            int m = (int)(rng() % 2);
            if (m == 0) {
                curSeq[turn] = b; curSeq[turn+1] = c; curSeq[turn+2] = a;
            } else {
                curSeq[turn] = c; curSeq[turn+1] = a; curSeq[turn+2] = b;
            }
            i128 v = evalFrom(turn);
            if (v >= curVal) {
                curVal = v;
                rebuildPrefix(turn);
                if (v > bestVal) { bestVal = v; bestSeq = curSeq; }
            } else {
                curSeq[turn] = a; curSeq[turn+1] = b; curSeq[turn+2] = c;
            }
        }

        if ((iter & 16383) == 0) {
            // periodic restart from best
            if (curVal < bestVal) {
                curSeq = bestSeq;
                curVal = bestVal;
                rebuildPrefix(0);
            }
        }
    }

    // Output
    {
        string buf;
        buf.reserve(T * 8);
        for (int t = 0; t < T; t++) {
            if (bestSeq[t].first < 0) buf += "-1\n";
            else { buf += to_string(bestSeq[t].first); buf += ' '; buf += to_string(bestSeq[t].second); buf += '\n'; }
        }
        cout << buf;
    }
    return 0;
}
// EVOLVE-BLOCK-END
