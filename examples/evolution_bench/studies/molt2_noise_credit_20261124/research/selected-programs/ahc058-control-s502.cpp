// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

static int N, L, T;
static ll K;
static ll A[10];
static ll C[4][10];

struct State {
    i128 apples;
    ll B[4][10];
    ll P[4][10];
};

static inline void initState(State &s) {
    s.apples = (i128)K;
    for (int i = 0; i < L; i++)
        for (int j = 0; j < N; j++) { s.B[i][j] = 1; s.P[i][j] = 0; }
}

static inline void produce(State &s) {
    for (int j = 0; j < N; j++) s.apples += (i128)A[j] * s.B[0][j] * s.P[0][j];
    for (int i = 1; i < L; i++)
        for (int j = 0; j < N; j++)
            s.B[i-1][j] += s.B[i][j] * s.P[i][j];
}

static inline bool apply(State &s, int i, int j) {
    if (i < 0) return true;
    i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
    if (cost > s.apples) return false;
    s.apples -= cost; s.P[i][j] += 1;
    return true;
}

static void greedySeq(vector<pair<int,int>>& seq) {
    State s; initState(s);
    for (int t = 0; t < T; t++) {
        int R = T - t;
        int bi = -1, bj = -1;
        long double best = 0.L;
        for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
            i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
            if (cost > s.apples) continue;
            long double val = (long double)A[j] * (long double)s.B[i][j];
            for (int e = 0; e < i; e++) val *= (long double)R;
            val /= (long double)cost;
            if (val > best) { best = val; bi = i; bj = j; }
        }
        seq[t] = {bi, bj};
        if (bi >= 0) apply(s, bi, bj);
        produce(s);
    }
}

static pair<int,int> bestGreedyAction(const State &s, int R) {
    int bi = -1, bj = -1;
    long double best = 0.L;
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
        i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
        if (cost > s.apples) continue;
        long double val = (long double)A[j] * (long double)s.B[i][j];
        for (int e = 0; e < i; e++) val *= (long double)R;
        val /= (long double)cost;
        if (val > best) { best = val; bi = i; bj = j; }
    }
    return {bi, bj};
}

struct IncEval {
    vector<State> states;
    vector<pair<int,int>> seqStored;
    int validLen;
    bool init;

    IncEval() : init(false), validLen(0) {
        states.resize(T);
        seqStored.assign(T, {-2,-2});
    }

    bool run(const vector<pair<int,int>>& newSeq, i128 &finalApples) {
        int start = 0;
        State s;
        if (init) {
            int d = 0;
            while (d < T && d < validLen && seqStored[d] == newSeq[d]) d++;
            if (d == validLen && d == T) {
                finalApples = states[T-1].apples;
                return true;
            }
            if (d == 0) { initState(s); start = 0; }
            else { s = states[d-1]; start = d; }
        } else { initState(s); start = 0; }
        for (int t = start; t < T; t++) {
            int i = newSeq[t].first, j = newSeq[t].second;
            if (i >= 0) {
                if (!apply(s, i, j)) {
                    validLen = t;
                    for (int q = 0; q < t; q++) seqStored[q] = newSeq[q];
                    if (t < T) seqStored[t] = {-2,-2};
                    init = true;
                    return false;
                }
            }
            produce(s);
            states[t] = s;
            seqStored[t] = newSeq[t];
        }
        validLen = T;
        init = true;
        finalApples = s.apples;
        return true;
    }
};

int main(int argc, char **argv) {
    double tl = argc > 1 ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    ios_base::sync_with_stdio(false); cin.tie(nullptr);
    if (!(cin >> N >> L >> T >> K)) return 0;
    for (int j = 0; j < N; j++) cin >> A[j];
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) cin >> C[i][j];

    double timeLimit = tl * 0.985;

    vector<pair<int,int>> seq(T, {-1,-1});
    greedySeq(seq);

    mt19937_64 rng(chrono::steady_clock::now().time_since_epoch().count());

    IncEval ie;
    vector<pair<int,int>> cur = seq;
    i128 curApples = 0;
    if (!ie.run(cur, curApples)) {
        for (int t = 0; t < T; t++) cur[t] = {-1,-1};
        ie.init = false;
        ie.run(cur, curApples);
    }
    i128 globalBest = curApples;
    vector<pair<int,int>> globalBestSeq = cur;

    const int LA_LEN = 32;
    vector<i128> hist(LA_LEN, curApples);

    int mutWeights[8] = {20, 14, 19, 11, 9, 8, 12, 7}; // sum = 100
    int NW = 8;

    long long iter = 0;
    long long noImprove = 0;

    while (true) {
        if ((iter++ & 15) == 0) {
            double elapsed = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
            if (elapsed > timeLimit) break;
        }

        vector<pair<int,int>> nseq = cur;
        int r = rng() % 100;
        int mutType = 0, acc = 0;
        for (int k = 0; k < NW; k++) {
            acc += mutWeights[k];
            if (r < acc) { mutType = k; break; }
        }
        bool ok = true;
        if (mutType == 0) {
            if (T < 2) ok = false;
            else { int t = rng() % (T-1); swap(nseq[t], nseq[t+1]); }
        } else if (mutType == 1) {
            if (T < 2) ok = false;
            else {
                int t2 = 1 + rng() % (T-1);
                int t1 = rng() % t2;
                auto act = nseq[t2];
                nseq.erase(nseq.begin() + t2);
                nseq.insert(nseq.begin() + t1, act);
            }
        } else if (mutType == 2) {
            int t = rng() % T;
            int choice = rng() % (N*L + 1);
            if (choice == N*L) nseq[t] = {-1,-1};
            else { int i = choice / N, j = choice % N; nseq[t] = {i,j}; }
        } else if (mutType == 3) {
            if (T < 2) ok = false;
            else {
                int t1 = rng() % (T-1);
                int t2 = t1 + 1 + rng() % (T - t1 - 1);
                auto act = nseq[t1];
                nseq.erase(nseq.begin() + t1);
                nseq.insert(nseq.begin() + t2, act);
            }
        } else if (mutType == 4) {
            int a = rng() % T, b = rng() % T;
            if (a == b) ok = false;
            else swap(nseq[a], nseq[b]);
        } else if (mutType == 5) {
            int len = 2 + rng() % 6;
            if (len > T) ok = false;
            else {
                int from = rng() % (T - len + 1);
                reverse(nseq.begin()+from, nseq.begin()+from+len);
            }
        } else if (mutType == 6) {
            int t1 = rng() % T, t2 = rng() % T;
            int c1 = rng() % (N*L + 1), c2 = rng() % (N*L + 1);
            if (c1 == N*L) nseq[t1] = {-1,-1};
            else { int i = c1 / N, j = c1 % N; nseq[t1] = {i,j}; }
            if (c2 == N*L) nseq[t2] = {-1,-1};
            else { int i = c2 / N, j = c2 % N; nseq[t2] = {i,j}; }
        } else {
            // fill a -1 with greedy best action at that state
            int t = rng() % T;
            if (nseq[t].first >= 0) ok = false;
            else {
                State s;
                if (t == 0) initState(s);
                else {
                    if (t-1 >= ie.validLen || !ie.init) ok = false;
                    else s = ie.states[t-1];
                }
                if (ok) {
                    pair<int,int> act = bestGreedyAction(s, T - t);
                    if (act.first < 0) ok = false;
                    else nseq[t] = act;
                }
            }
        }
        if (!ok) { continue; }

        i128 nApples;
        if (!ie.run(nseq, nApples)) continue;

        int idx = iter % LA_LEN;
        i128 threshold = hist[idx];
        bool accept = (nApples >= threshold) || (nApples > curApples);
        if (accept) {
            if (nApples > globalBest) {
                globalBest = nApples;
                globalBestSeq = nseq;
                noImprove = 0;
            } else {
                noImprove++;
            }
            cur = nseq;
            curApples = nApples;
            hist[idx] = curApples;
        } else {
            noImprove++;
            ie.run(cur, curApples);
        }

        if (noImprove > 80000) {
            vector<pair<int,int>> restart = globalBestSeq;
            int reps = 2 + rng() % 8;
            for (int k = 0; k < reps; k++) {
                int t = rng() % T;
                int choice = rng() % (N*L + 1);
                if (choice == N*L) restart[t] = {-1,-1};
                else { int i = choice / N, j = choice % N; restart[t] = {i,j}; }
            }
            i128 na;
            ie.init = false;
            if (ie.run(restart, na)) {
                cur = restart;
                curApples = na;
                if (na > globalBest) { globalBest = na; globalBestSeq = restart; }
            } else {
                cur = globalBestSeq;
                ie.init = false;
                i128 ca;
                ie.run(cur, ca);
                curApples = ca;
            }
            for (auto &h : hist) h = curApples;
            noImprove = 0;
        }
    }

    string out;
    out.reserve(T * 8);
    for (int t = 0; t < T; t++) {
        if (globalBestSeq[t].first < 0) out += "-1\n";
        else {
            out += to_string(globalBestSeq[t].first);
            out += ' ';
            out += to_string(globalBestSeq[t].second);
            out += '\n';
        }
    }
    cout << out;
    return 0;
}
// EVOLVE-BLOCK-END
