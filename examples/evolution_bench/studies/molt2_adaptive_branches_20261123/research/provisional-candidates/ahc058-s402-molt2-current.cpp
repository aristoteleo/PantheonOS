// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

static int N, L, T; static ll K;
static ll A[16];
static ll C[8][16];

struct State {
    i128 apples;
    ll B[8][16];
    ll P[8][16];
};

static inline void produce(State &s){
    for(int j=0;j<N;j++) s.apples += (i128)A[j]*s.B[0][j]*s.P[0][j];
    for(int i=1;i<L;i++)
        for(int j=0;j<N;j++)
            s.B[i-1][j] += s.B[i][j]*s.P[i][j];
}

static inline bool apply(State &s,int i,int j){
    if(i<0) return true;
    i128 cost=(i128)C[i][j]*((i128)s.P[i][j]+1);
    if(cost>s.apples) return false;
    s.apples-=cost; s.P[i][j]+=1; return true;
}

static long double marginalValue(const State &s, int i, int j, int R) {
    if (i == 0) {
        return (long double)A[j] * (long double)s.B[0][j] * (long double)R;
    }
    long double perTurnCoeff = (long double)s.B[i][j];
    for (int k = i-1; k >= 0; k--) {
        perTurnCoeff *= (long double)s.P[k][j];
    }
    perTurnCoeff *= (long double)A[j];
    long double rpow = 1.0L;
    for (int k = 0; k <= i; k++) rpow *= (long double)R / (long double)(k+1);
    return perTurnCoeff * rpow;
}

static pair<int,int> policy(const State &s, int turnsLeft) {
    int bi = -1, bj = -1;
    long double bestRoi = 0.0L;
    for (int i = 0; i < L; i++) {
        for (int j = 0; j < N; j++) {
            i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
            if (cost > s.apples) continue;
            long double v = marginalValue(s, i, j, turnsLeft);
            long double roi = v / (long double)cost;
            if (roi > bestRoi) { bestRoi = roi; bi = i; bj = j; }
        }
    }
    return {bi, bj};
}

static i128 simulateFrom(const State &sStart, const vector<pair<int,int>>& seq, int start) {
    State s = sStart;
    for (int t = start; t < T; t++) {
        if (!apply(s, seq[t].first, seq[t].second)) return -1;
        produce(s);
    }
    return s.apples;
}

static i128 simulateFull(const vector<pair<int,int>>& seq) {
    State s;
    s.apples = (i128)K;
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) { s.B[i][j] = 1; s.P[i][j] = 0; }
    for (int t = 0; t < T; t++) {
        if (!apply(s, seq[t].first, seq[t].second)) return -1;
        produce(s);
    }
    return s.apples;
}

int main(int argc,char**argv){
    double tl = argc>1?atof(argv[1]):2.0;
    auto t0 = chrono::steady_clock::now();
    ios::sync_with_stdio(false); cin.tie(nullptr);
    cin >> N >> L >> T >> K;
    for (int j = 0; j < N; j++) cin >> A[j];
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) cin >> C[i][j];

    // ----- Phase 1: build initial sequence using per-turn greedy with rollout -----
    State s;
    s.apples = (i128)K;
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) { s.B[i][j] = 1; s.P[i][j] = 0; }

    vector<pair<int,int>> seq; seq.reserve(T);

    auto rollout = [&](State ss, int i0, int j0) -> i128 {
        if (!apply(ss, i0, j0)) return -1;
        produce(ss);
        // Fast greedy continuation (no rollout) for speed
        // But we do a short rollout inside score later
        return ss.apples;
    };

    // For scoring initial candidate: use greedy policy rollout
    auto rollFull = [&](State ss, int turn, int i0, int j0) -> i128 {
        if (!apply(ss, i0, j0)) return -1;
        produce(ss);
        for (int t = turn+1; t < T; t++) {
            auto pr = policy(ss, T-t);
            apply(ss, pr.first, pr.second);
            produce(ss);
        }
        return ss.apples;
    };

    double phase1Budget = tl * 0.20;
    for (int t = 0; t < T; t++) {
        int bi = -1, bj = -1;
        int R = T - t;

        vector<tuple<long double,int,int>> cand;
        for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
            i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
            if (cost > s.apples) continue;
            long double v = marginalValue(s, i, j, R);
            long double roi = v / (long double)cost;
            cand.emplace_back(roi, i, j);
        }
        sort(cand.begin(), cand.end(), [](const tuple<long double,int,int>&a, const tuple<long double,int,int>&b){
            return get<0>(a) > get<0>(b);
        });

        double el = chrono::duration<double>(chrono::steady_clock::now()-t0).count();
        double remaining = tl - el;
        if (remaining > phase1Budget && !cand.empty() && (t < T/3)) {
            i128 best = rollFull(s, t, -1, -1);
            int lim = min((int)cand.size(), 20);
            for (int k = 0; k < lim; k++) {
                double e2 = chrono::duration<double>(chrono::steady_clock::now()-t0).count();
                if (e2 > phase1Budget) break;
                int i = get<1>(cand[k]), j = get<2>(cand[k]);
                i128 v = rollFull(s, t, i, j);
                if (v > best) { best = v; bi = i; bj = j; }
            }
        } else {
            if (!cand.empty()) { bi = get<1>(cand[0]); bj = get<2>(cand[0]); }
        }

        if (!apply(s, bi, bj)) { bi = -1; bj = -1; }
        produce(s);
        seq.emplace_back(bi, bj);
    }

    // ----- Phase 2: simulated annealing over the full sequence -----
    i128 bestScore = simulateFull(seq);
    if (bestScore < 0) {
        seq.assign(T, {-1,-1});
        bestScore = simulateFull(seq);
    }
    vector<pair<int,int>> bestSeq = seq;

    // Build all actions
    vector<pair<int,int>> allActions;
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) allActions.emplace_back(i,j);
    allActions.emplace_back(-1,-1);

    mt19937_64 rng(987654321ULL);
    auto now = [&](){ return chrono::duration<double>(chrono::steady_clock::now()-t0).count(); };

    double timeLimit = tl * 0.98;

    vector<pair<int,int>> curSeq = seq;
    i128 curScore = bestScore;

    // Snapshots for curSeq
    vector<State> snaps(T+1);
    {
        snaps[0].apples = (i128)K;
        for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) { snaps[0].B[i][j]=1; snaps[0].P[i][j]=0; }
        for (int t = 0; t < T; t++) {
            snaps[t+1] = snaps[t];
            apply(snaps[t+1], curSeq[t].first, curSeq[t].second);
            produce(snaps[t+1]);
        }
    }

    double temp = 0.05;
    double tempEnd = 0.0002;

    auto log2ld = [](i128 x) -> double {
        if (x <= 0) return -1e18;
        return (double)(logl((long double)x) / logl(2.0L));
    };

    int nActions = (int)allActions.size();
    double curLog = log2ld(curScore);
    double bestLog = curLog;

    // Bias turns: prefer earlier and more influential regions. Build sampling weight.
    // Weight turns so that early turns get more mutations (they matter more), but still cover all.
    vector<int> turnSamplePool;
    turnSamplePool.reserve(T*2);
    for (int t = 0; t < T; t++) {
        turnSamplePool.push_back(t);
        if (t < T/2) turnSamplePool.push_back(t); // double weight for first half
    }

    long long iters = 0;
    while (now() < timeLimit) {
        iters++;
        double elapsed = now();
        double frac = elapsed / timeLimit;
        if (frac > 1.0) frac = 1.0;
        double curTemp = temp * pow(tempEnd/temp, frac);

        int mt = rng() % 100;
        int start = T;

        if (mt < 65) {
            // single action change
            int t = turnSamplePool[rng() % turnSamplePool.size()];
            int act = rng() % nActions;
            pair<int,int> na = allActions[act];
            if (na == curSeq[t]) continue;
            pair<int,int> old = curSeq[t];
            curSeq[t] = na;
            start = t;
            i128 newScore = simulateFrom(snaps[start], curSeq, start);
            if (newScore < 0) { curSeq[t] = old; continue; }
            double newLog = log2ld(newScore);
            double delta = newLog - curLog;
            bool accept = false;
            if (delta >= 0) accept = true;
            else {
                double prob = exp(delta / curTemp);
                double r = (double)(rng() % 1000000) / 1000000.0;
                accept = (r < prob);
            }
            if (accept) {
                for (int k = start; k < T; k++) {
                    snaps[k+1] = snaps[k];
                    apply(snaps[k+1], curSeq[k].first, curSeq[k].second);
                    produce(snaps[k+1]);
                }
                curScore = newScore;
                curLog = newLog;
                if (curLog > bestLog) { bestLog = curLog; bestScore = curScore; bestSeq = curSeq; }
            } else {
                curSeq[t] = old;
            }
        } else if (mt < 85) {
            // swap adjacent
            if (T < 2) continue;
            int t = rng() % (T-1);
            if (curSeq[t] == curSeq[t+1]) continue;
            swap(curSeq[t], curSeq[t+1]);
            start = t;
            i128 newScore = simulateFrom(snaps[start], curSeq, start);
            if (newScore < 0) { swap(curSeq[t], curSeq[t+1]); continue; }
            double newLog = log2ld(newScore);
            double delta = newLog - curLog;
            bool accept = false;
            if (delta >= 0) accept = true;
            else {
                double prob = exp(delta / curTemp);
                double r = (double)(rng() % 1000000) / 1000000.0;
                accept = (r < prob);
            }
            if (accept) {
                for (int k = start; k < T; k++) {
                    snaps[k+1] = snaps[k];
                    apply(snaps[k+1], curSeq[k].first, curSeq[k].second);
                    produce(snaps[k+1]);
                }
                curScore = newScore;
                curLog = newLog;
                if (curLog > bestLog) { bestLog = curLog; bestScore = curScore; bestSeq = curSeq; }
            } else {
                swap(curSeq[t], curSeq[t+1]);
            }
        } else {
            // move action
            int t = rng() % T;
            int u = rng() % T;
            if (t == u) continue;
            pair<int,int> a = curSeq[t];
            curSeq.erase(curSeq.begin()+t);
            int insPos = u;
            if (insPos > (int)curSeq.size()) insPos = (int)curSeq.size();
            curSeq.insert(curSeq.begin()+insPos, a);
            start = min(t, u);
            i128 newScore = simulateFrom(snaps[start], curSeq, start);
            if (newScore < 0) {
                // revert
                curSeq.erase(curSeq.begin()+insPos);
                curSeq.insert(curSeq.begin()+t, a);
                continue;
            }
            double newLog = log2ld(newScore);
            double delta = newLog - curLog;
            bool accept = false;
            if (delta >= 0) accept = true;
            else {
                double prob = exp(delta / curTemp);
                double r = (double)(rng() % 1000000) / 1000000.0;
                accept = (r < prob);
            }
            if (accept) {
                for (int k = start; k < T; k++) {
                    snaps[k+1] = snaps[k];
                    apply(snaps[k+1], curSeq[k].first, curSeq[k].second);
                    produce(snaps[k+1]);
                }
                curScore = newScore;
                curLog = newLog;
                if (curLog > bestLog) { bestLog = curLog; bestScore = curScore; bestSeq = curSeq; }
            } else {
                curSeq.erase(curSeq.begin()+insPos);
                curSeq.insert(curSeq.begin()+t, a);
            }
        }
    }

    // ----- Phase 3: final greedy polish on bestSeq (accept only improvements) -----
    {
        vector<State> bs(T+1);
        bs[0].apples = (i128)K;
        for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) { bs[0].B[i][j]=1; bs[0].P[i][j]=0; }
        for (int t = 0; t < T; t++) {
            bs[t+1] = bs[t];
            apply(bs[t+1], bestSeq[t].first, bestSeq[t].second);
            produce(bs[t+1]);
        }
        i128 bsc = simulateFull(bestSeq);
        double bscLog = log2ld(bsc);
        double polishEnd = tl * 0.995;
        while (now() < polishEnd) {
            bool improved = false;
            for (int t = 0; t < T && now() < polishEnd; t++) {
                pair<int,int> old = bestSeq[t];
                pair<int,int> bestA = old;
                for (int a = 0; a < nActions; a++) {
                    pair<int,int> na = allActions[a];
                    if (na == old) continue;
                    bestSeq[t] = na;
                    i128 ns = simulateFrom(bs[t], bestSeq, t);
                    if (ns > bsc) {
                        bsc = ns; bscLog = log2ld(bsc); bestA = na; improved = true;
                    }
                }
                bestSeq[t] = bestA;
                if (bestA != old) {
                    for (int k = t; k < T; k++) {
                        bs[k+1] = bs[k];
                        apply(bs[k+1], bestSeq[k].first, bestSeq[k].second);
                        produce(bs[k+1]);
                    }
                }
            }
            if (!improved) break;
        }
    }

    for (auto &p : bestSeq) {
        if (p.first < 0) cout << -1 << "\n";
        else cout << p.first << " " << p.second << "\n";
    }
    return 0;
}
// EVOLVE-BLOCK-END
