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

static inline void produce(State &s) {
    for (int j = 0; j < N; j++) s.apples += (i128)A[j] * s.B[0][j] * s.P[0][j];
    for (int i = 1; i < L; i++)
        for (int j = 0; j < N; j++)
            s.B[i - 1][j] += s.B[i][j] * s.P[i][j];
}

static inline bool apply(State &s, int i, int j) {
    if (i < 0) return true;
    i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
    if (cost > s.apples) return false;
    s.apples -= cost;
    s.P[i][j] += 1;
    return true;
}

static inline long double marginal_benefit(const State &s, int i, int j, int m) {
    long double amp = 1.0L;
    for (int k = 0; k < i; k++) amp *= (long double)s.P[k][j];
    long double mm = (long double)m;
    if (i == 0) {
        return (long double)A[j] * (long double)s.B[0][j] * mm;
    }
    long double chain = (long double)A[j];
    for (int k = 0; k < i - 1; k++) chain *= (long double)s.P[k][j];
    return (long double)s.B[i][j] * amp * chain * mm * mm * 0.5L;
}

static pair<int,int> policy(const State &s, int m) {
    int bi = -1, bj = -1;
    long double bestRatio = -1.0L;
    for (int j = 0; j < N; j++) {
        for (int i = 0; i < L; i++) {
            i128 cost = (i128)C[i][j] * (i128)(s.P[i][j] + 1);
            if (cost <= s.apples) {
                long double ben = marginal_benefit(s, i, j, m);
                if (ben > 0) {
                    long double ratio = ben / (long double)cost;
                    if (ratio > bestRatio) { bestRatio = ratio; bi = i; bj = j; }
                }
            }
        }
    }
    return {bi, bj};
}

static i128 simulateFrom(const State &s0, const vector<pair<int,int>> &actions, int start) {
    State s = s0;
    for (int t = start; t < T; t++) {
        int i = actions[t].first, j = actions[t].second;
        if (i >= 0) {
            if (!apply(s, i, j)) return (i128)-1;
        }
        produce(s);
    }
    return s.apples;
}

static inline State initState() {
    State s;
    s.apples = (i128)K;
    s.B.assign(L, vector<ll>(N, 1));
    s.P.assign(L, vector<ll>(N, 0));
    return s;
}

static i128 simulate(const vector<pair<int,int>> &actions) {
    State s = initState();
    return simulateFrom(s, actions, 0);
}

static void recomputeStates(const vector<pair<int,int>> &actions,
                            vector<State> &stateBefore, int from) {
    if (from == 0) stateBefore[0] = initState();
    State s = stateBefore[from];
    for (int t = from; t < T; t++) {
        int i = actions[t].first, j = actions[t].second;
        if (i >= 0) apply(s, i, j);
        produce(s);
        stateBefore[t + 1] = s;
    }
}

int main(int argc, char **argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    cin >> N >> L >> T >> K;
    A.assign(N, 0);
    for (auto &a : A) cin >> a;
    C.assign(L, vector<ll>(N));
    for (auto &row : C) for (auto &c : row) cin >> c;

    double budget = tl * 0.95;
    auto deadline = t0 + chrono::duration_cast<chrono::steady_clock::duration>(
        chrono::duration<double>(budget));

    // Build incumbent greedily.
    vector<pair<int,int>> actions(T, {-1, -1});
    {
        State s = initState();
        for (int t = 0; t < T; t++) {
            int remain = T - t;
            pair<int,int> gp = policy(s, remain);
            int gi = gp.first, gj = gp.second;
            if (!apply(s, gi, gj)) { gi = -1; gj = -1; }
            actions[t] = {gi, gj};
            produce(s);
        }
    }

    i128 bestScore = simulate(actions);
    vector<pair<int,int>> bestActions = actions;

    vector<State> stateBefore(T + 1);
    recomputeStates(actions, stateBefore, 0);

    mt19937_64 rng(chrono::steady_clock::now().time_since_epoch().count() ^ 0x9e3779b97f4a7c15ULL);

    // Temperature schedule.
    long double bs = (long double)bestScore;
    double T0 = 1.0;
    if (bs > 1.0L) {
        T0 = (double)(bs * 0.05L);
        if (T0 < 1.0) T0 = 1.0;
    }
    double Tend = 1e-3;
    double Tcur = T0;

    auto elapsed = [&]() {
        return chrono::duration<double>(chrono::steady_clock::now() - t0).count();
    };

    vector<pair<int,int>> curActions = actions;
    i128 curScore = bestScore;

    long long iters = 0;
    vector<pair<int,int>> backup;
    int totalActs = L * N + 1;

    while (true) {
        double el = elapsed();
        if (el >= budget) break;
        double frac = el / budget;
        Tcur = T0 * pow(Tend / T0, frac);
        if (Tcur < 1e-9) Tcur = 1e-9;

        iters++;

        int op = (int)(rng() % 4);
        backup = curActions;
        int earliest = T;

        if (op == 0) {
            int t = (int)(rng() % T);
            int na = (int)(rng() % totalActs);
            curActions[t] = (na == L * N) ? make_pair(-1,-1) : make_pair(na / N, na % N);
            earliest = t;
        } else if (op == 1) {
            int u = (int)(rng() % T);
            int v = (int)(rng() % T);
            if (u == v) continue;
            swap(curActions[u], curActions[v]);
            earliest = min(u, v);
        } else if (op == 2) {
            int u = (int)(rng() % T);
            int v = (int)(rng() % T);
            if (u == v) continue;
            pair<int,int> val = curActions[u];
            if (u < v) {
                for (int k = u; k < v; k++) curActions[k] = curActions[k + 1];
                curActions[v] = val;
            } else {
                for (int k = u; k > v; k--) curActions[k] = curActions[k - 1];
                curActions[v] = val;
            }
            earliest = min(u, v);
        } else {
            int u = (int)(rng() % T);
            int v = (int)(rng() % T);
            if (u == v) continue;
            pair<int,int> val = curActions[u];
            curActions[u] = {-1,-1};
            int na = (int)(rng() % totalActs);
            pair<int,int> newAct = (na == L * N) ? make_pair(-1,-1) : make_pair(na / N, na % N);
            curActions[v] = (rng() & 1) ? newAct : val;
            earliest = min(u, v);
        }

        i128 candScore = simulateFrom(stateBefore[earliest], curActions, earliest);

        bool accept = false;
        if (candScore >= 0) {
            if (candScore >= curScore) {
                accept = true;
            } else {
                double delta = (double)(curScore - candScore);
                if (delta <= 0) accept = true;
                else {
                    double prob = exp(-delta / Tcur);
                    double rnd = (double)(rng() % 1000000000ULL) / 1e9;
                    if (rnd < prob) accept = true;
                }
            }
        }

        if (accept) {
            curScore = candScore;
            if (candScore > bestScore) {
                bestScore = candScore;
                bestActions = curActions;
            }
            recomputeStates(curActions, stateBefore, earliest);
        } else {
            curActions.swap(backup);
        }
    }

    for (int t = 0; t < T; t++) {
        if (bestActions[t].first < 0) cout << "-1\n";
        else cout << bestActions[t].first << " " << bestActions[t].second << "\n";
    }
    return 0;
}
// EVOLVE-BLOCK-END
