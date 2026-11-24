// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

static int N, L, T;
static ll K;
static ll A[16];
static ll C[8][16];

struct State {
    i128 apples;
    ll B[8][16], P[8][16];
};

static inline void produce(State &s) {
    for (int j = 0; j < N; j++) s.apples += (i128)A[j] * s.B[0][j] * s.P[0][j];
    for (int i = 1; i < L; i++) for (int j = 0; j < N; j++)
        s.B[i-1][j] += s.B[i][j] * s.P[i][j];
}

static inline bool afford(const State& s, int i, int j) {
    if (i < 0) return true;
    return (i128)C[i][j] * (s.P[i][j] + 1) <= s.apples;
}

static inline void applyU(State& s, int i, int j) {
    if (i < 0) return;
    i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
    s.apples -= cost;
    s.P[i][j]++;
}

static inline long double value_of(const State &s, int i, int j, int t) {
    if (t <= 0) return 0.0L;
    for (int k = 0; k < i; k++) if (s.P[k][j] <= 0) return 0.0L;
    long double tj = (long double)t;
    long double mult = (long double)A[j];
    for (int k = 0; k < i; k++) mult *= (long double)s.P[k][j];
    long double Bsrc = (long double)s.B[i][j];
    long double poly = 1.0L;
    for (int k = 1; k <= i + 1; k++) poly *= tj / (long double)k;
    return mult * Bsrc * poly;
}

static inline void policy(const State& s, int t, int& bi, int& bj) {
    bi = -1; bj = -1;
    long double best = 0.0L;
    for (int j = 0; j < N; j++) for (int i = 0; i < L; i++) {
        if (!afford(s, i, j)) continue;
        i128 cost = (i128)C[i][j] * (s.P[i][j] + 1);
        long double v = value_of(s, i, j, t) / (long double)cost;
        if (v > best) { best = v; bi = i; bj = j; }
    }
}

static State sim_state;
static void init_state(State& s) {
    s.apples = (i128)K;
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
        s.B[i][j] = 1;
        s.P[i][j] = 0;
    }
}

static i128 simulate_seq(const vector<pair<int,int>>& seq, bool& valid) {
    init_state(sim_state);
    for (int t = 0; t < T; t++) {
        int i = seq[t].first, j = seq[t].second;
        if (i >= 0) {
            if (!afford(sim_state, i, j)) { valid = false; return 0; }
            applyU(sim_state, i, j);
        }
        produce(sim_state);
    }
    valid = true;
    return sim_state.apples;
}

static long double log2_i128(i128 x) {
    if (x <= 0) return -1e18L;
    return log2l((long double)x);
}

int main(int argc, char** argv){
    double tl = 2.0;
    if (argc > 1) tl = atof(argv[1]);
    auto t0 = chrono::steady_clock::now();
    if (scanf("%d %d %d %lld", &N, &L, &T, &K) != 4) return 0;
    for (int j = 0; j < N; j++) scanf("%lld", &A[j]);
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) scanf("%lld", &C[i][j]);

    // Build initial sequence using fast greedy policy
    State build_state;
    init_state(build_state);
    vector<pair<int,int>> seq(T);
    for (int t = 0; t < T; t++) {
        int i, j;
        policy(build_state, T - t - 1, i, j);
        seq[t] = {i, j};
        applyU(build_state, i, j);
        produce(build_state);
    }
    i128 currentS = build_state.apples;
    vector<pair<int,int>> bestSeq = seq;
    i128 bestS = currentS;

    // Local search with restart from best
    mt19937 rng(12345);
    double budget = tl * 0.95;
    double T0 = 0.5;
    double Tmin = 1e-4;
    int iter = 0;
    auto start_time = chrono::steady_clock::now();

    // Precompute upgrade positions list per iteration cheaply via scanning
    while (true) {
        if ((iter & 127) == 0) {
            double elapsed = chrono::duration<double>(chrono::steady_clock::now() - start_time).count();
            if (elapsed > budget) break;
        }
        double elapsed = chrono::duration<double>(chrono::steady_clock::now() - start_time).count();
        double frac = elapsed / budget;
        if (frac > 1.0) frac = 1.0;
        double temp = T0 * pow(Tmin / T0, frac);

        vector<pair<int,int>> newSeq = seq;
        int mut = rng() % 3;
        if (mut == 0) {
            // Move one upgrade earlier/later
            vector<int> upgrade_turns;
            for (int t = 0; t < T; t++) if (newSeq[t].first >= 0) upgrade_turns.push_back(t);
            if (upgrade_turns.size() >= 2) {
                int idx1 = rng() % upgrade_turns.size();
                int t1 = upgrade_turns[idx1];
                int t2 = rng() % T;
                if (t1 != t2) {
                    pair<int,int> val = newSeq[t1];
                    if (t1 < t2) {
                        for (int k = t1; k < t2; k++) newSeq[k] = newSeq[k+1];
                        newSeq[t2] = val;
                    } else {
                        for (int k = t1; k > t2; k--) newSeq[k] = newSeq[k-1];
                        newSeq[t2] = val;
                    }
                }
            }
        } else if (mut == 1) {
            // Swap two upgrades
            vector<int> upgrade_turns;
            for (int t = 0; t < T; t++) if (newSeq[t].first >= 0) upgrade_turns.push_back(t);
            if (upgrade_turns.size() >= 2) {
                int idx1 = rng() % upgrade_turns.size();
                int idx2 = rng() % upgrade_turns.size();
                if (idx1 != idx2) {
                    int t1 = upgrade_turns[idx1], t2 = upgrade_turns[idx2];
                    swap(newSeq[t1], newSeq[t2]);
                }
            }
        } else {
            // Change one upgrade's target (i,j) - keep position, change machine/level
            vector<int> upgrade_turns;
            for (int t = 0; t < T; t++) if (newSeq[t].first >= 0) upgrade_turns.push_back(t);
            if (!upgrade_turns.empty()) {
                int t = upgrade_turns[rng() % upgrade_turns.size()];
                int i = rng() % L;
                int j = rng() % N;
                newSeq[t] = {i, j};
            }
        }

        bool valid;
        i128 newS = simulate_seq(newSeq, valid);
        if (!valid) { 
            iter++; 
            // Occasionally restart from best
            if ((iter & 1023) == 0) { seq = bestSeq; currentS = bestS; }
            continue; 
        }

        long double delta = log2_i128(newS) - log2_i128(currentS);
        bool accept = false;
        if (newS > currentS) accept = true;
        else {
            double prob = exp((double)delta / temp);
            if (prob > (double)rng() / rng.max()) accept = true;
        }
        if (accept) {
            seq = move(newSeq);
            currentS = newS;
            if (newS > bestS) {
                bestS = newS;
                bestSeq = seq;
            }
        }
        iter++;
    }

    // Output best sequence
    string out;
    out.reserve(T * 8);
    for (int t = 0; t < T; t++) {
        int i = bestSeq[t].first, j = bestSeq[t].second;
        if (i < 0) out += "-1\n";
        else {
            out += to_string(i);
            out += ' ';
            out += to_string(j);
            out += '\n';
        }
    }
    fputs(out.c_str(), stdout);
    return 0;
}
// EVOLVE-BLOCK-END
