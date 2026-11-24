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

// Simulate a full sequence of T actions, starting from initial state.
// Returns final apple count, or -1 if the sequence is invalid.
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

// Greedy: at each turn, pick the highest analyticValue action (if any feasible).
// We don't do any rollout. Fast heuristic.
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
    auto t0 = chrono::steady_clock::now();
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
        // greedy invalid (shouldn't happen) -> fallback all -1
        seq.assign(T, {-1, -1});
        bestVal = simSeq(seq, init);
    }
    vector<pair<int,int>> bestSeq = seq;

    auto deadline = chrono::steady_clock::now() + chrono::duration<double>(tl * 0.95);
    double elapsedInit = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
    // Phase 2: hill-climb on the full sequence
    mt19937_64 rng(123456789ULL ^ (unsigned long long)chrono::steady_clock::now().time_since_epoch().count());

    int totalMachines = L * N;
    int actionSpace = totalMachines + 1; // including -1

    while (chrono::steady_clock::now() < deadline) {
        // Random turn to mutate
        int turn = (int)(rng() % (unsigned long long)T);
        // Random new action
        int r = (int)(rng() % (unsigned long long)actionSpace);
        pair<int,int> newAct = (r == 0) ? make_pair(-1, -1) : make_pair((r-1)/N, (r-1)%N);
        if (newAct == seq[turn]) continue;
        pair<int,int> old = seq[turn];
        seq[turn] = newAct;
        i128 val = simSeq(seq, init);
        if (val > bestVal) {
            bestVal = val;
            bestSeq = seq;
        } else {
            seq[turn] = old;
        }
        // Occasionally do multi-turn perturbation? Keep simple for now.
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
