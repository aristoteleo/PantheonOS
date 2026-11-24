// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

static int N, L, T; static ll K;
static vector<ll> A;
static vector<vector<ll>> C;

struct State {
    i128 apples;
    ll B[4][10];
    ll P[4][10];
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
    s.apples -= cost; s.P[i][j] += 1; return true;
}

static inline void initState(State &s) {
    s.apples = (i128)K;
    for (int i = 0; i < L; i++)
        for (int j = 0; j < N; j++) { s.B[i][j] = 1; s.P[i][j] = 0; }
}

static inline long double comb_gain(int R, int i) {
    if (R <= 0) return 0.0L;
    long double r = 1.0L;
    int n = R + i, k = i + 1;
    for (int t = 0; t < k; t++) r = r * (long double)(n - t) / (long double)(t + 1);
    return r;
}

// Absolute-gain heuristic (from inspiration 2 style), used to bias greedy initial.
static inline long double absoluteBenefit(const State &s, int i, int j, int R) {
    if (R <= 0) return -1e30L;
    i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
    if (cost > s.apples) return -1e30L;
    long double gain;
    if (i == 0) {
        long double B0 = (long double)s.B[0][j];
        long double dgrow = 0.0L;
        for (int k = 1; k < L; k++) {
            long double chain = 1.0L;
            for (int m = 1; m < k; m++) chain *= (long double)s.P[m][j];
            dgrow += (long double)s.B[k][j] * (long double)s.P[k][j] * chain;
        }
        gain = (long double)A[j] * (B0 * (long double)R + dgrow * (long double)R * (long double)R * 0.5L);
    } else {
        long double chainBelow = 1.0L;
        for (int k = 1; k < i; k++) chainBelow *= (long double)s.P[k][j];
        long double rate = (long double)s.B[i][j] * chainBelow;
        long double p0 = (long double)s.P[0][j];
        gain = (long double)A[j] * p0 * rate * comb_gain(R, i);
    }
    return gain - (long double)cost;
}

static inline void greedyStep(const State &s, int R, int &bi, int &bj) {
    bi = -1; bj = -1;
    long double best = 0.0L;
    for (int i = 0; i < L; i++)
        for (int j = 0; j < N; j++) {
            long double v = absoluteBenefit(s, i, j, R);
            if (v > best) { best = v; bi = i; bj = j; }
        }
}

int main(int argc, char **argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0;
    auto t_start = chrono::steady_clock::now();
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    cin >> N >> L >> T >> K;
    A.resize(N);
    for (auto &a : A) cin >> a;
    C.assign(L, vector<ll>(N));
    for (auto &row : C) for (auto &c : row) cin >> c;

    // ---- Phase 1: analytic greedy initial plan ----
    vector<pair<int,int>> bestPlan((size_t)T, {-1,-1});
    {
        State s; initState(s);
        for (int t = 0; t < T; t++) {
            int bi, bj;
            greedyStep(s, T - t, bi, bj);
            if (bi >= 0 && apply(s, bi, bj)) bestPlan[t] = {bi, bj};
            produce(s);
        }
    }

    vector<State> states((size_t)T + 1);
    auto evalFull = [&](const vector<pair<int,int>>& pl) -> i128 {
        initState(states[0]);
        for (int t = 0; t < T; t++) {
            State s = states[t];
            int i = pl[t].first, j = pl[t].second;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
                if (cost <= s.apples) { s.apples -= cost; s.P[i][j]++; }
            }
            produce(s);
            states[t + 1] = s;
        }
        return states[T].apples;
    };
    i128 bestS = evalFull(bestPlan);

    // ---- Phase 2: local search with multiple mutation modes ----
    mt19937_64 rng(0xDEADBEEFCAFEULL);
    vector<pair<int,int>> candPlan((size_t)T);
    vector<State> tmpStates((size_t)T + 1);

    double budget = tl * 0.90;
    long long iter = 0;
    auto t_end = chrono::steady_clock::now();

    while (true) {
        if ((iter & 63) == 0) {
            t_end = chrono::steady_clock::now();
            double el = chrono::duration<double>(t_end - t_start).count();
            if (el > budget) break;
        }
        iter++;

        int mode = (int)(rng() % 100);
        int t0 = 0, t1 = -1;
        if (mode < 55) {
            t0 = (int)(rng() % T);                     // single action change
        } else if (mode < 80) {
            t0 = (int)(rng() % T);
            t1 = (int)(rng() % T);
            if (t1 == t0) continue;                    // swap two actions
        } else if (mode < 92) {
            // block shift: move a random action from t0 to t1 (shift in between)
            t0 = (int)(rng() % T);
            t1 = (int)(rng() % T);
            if (t0 == t1) continue;
        } else {
            // copy from neighbor position (shift right by 1)
            t0 = (int)(rng() % (T - 1));
            t1 = t0 + 1;
        }

        memcpy(candPlan.data(), bestPlan.data(), (size_t)T * sizeof(pair<int,int>));

        int lo;
        if (t1 < 0) {
            // single change
            lo = t0;
            int old_i = bestPlan[t0].first, old_j = bestPlan[t0].second;
            int ni, nj;
            int m2 = (int)(rng() % 100);
            if (m2 < 15) { ni = -1; nj = -1; }
            else { ni = (int)(rng() % L); nj = (int)(rng() % N); }
            if (ni == old_i && nj == old_j) continue;
            candPlan[t0] = {ni, nj};
        } else if (mode < 80) {
            lo = min(t0, t1);
            if (candPlan[t0] == candPlan[t1]) continue;
            swap(candPlan[t0], candPlan[t1]);
        } else if (mode < 92) {
            // shift: move element from t0 to t1
            lo = min(t0, t1);
            pair<int,int> v = candPlan[t0];
            if (t0 < t1) {
                for (int t = t0; t < t1; t++) candPlan[t] = candPlan[t + 1];
                candPlan[t1] = v;
            } else {
                for (int t = t0; t > t1; t--) candPlan[t] = candPlan[t - 1];
                candPlan[t1] = v;
            }
        } else {
            // copy neighbor: swap existing t0,t1 plan entries; may be no-op if same
            lo = t0;
            if (candPlan[t0] == candPlan[t1]) {
                int ni = (int)(rng() % L), nj = (int)(rng() % N);
                candPlan[t1] = {ni, nj};
            } else {
                swap(candPlan[t0], candPlan[t1]);
            }
        }

        State s = states[lo];
        for (int t = lo; t < T; t++) {
            int i = candPlan[t].first, j = candPlan[t].second;
            if (i >= 0) {
                i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
                if (cost > s.apples) {
                    candPlan[t] = {-1, -1};
                } else {
                    s.apples -= cost; s.P[i][j]++;
                }
            }
            produce(s);
            tmpStates[t + 1] = s;
        }
        i128 newS = s.apples;

        if (newS > bestS) {
            bestPlan.swap(candPlan);
            for (int t = lo + 1; t <= T; t++) states[t] = tmpStates[t];
            bestS = newS;
        }
    }

    for (int t = 0; t < T; t++) {
        if (bestPlan[t].first < 0) cout << "-1\n";
        else cout << bestPlan[t].first << " " << bestPlan[t].second << "\n";
    }
    return 0;
}
// EVOLVE-BLOCK-END
