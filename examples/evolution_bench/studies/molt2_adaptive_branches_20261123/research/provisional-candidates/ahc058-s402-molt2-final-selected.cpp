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

static void initState(State &s) {
    s.apples = (i128)K;
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) { s.B[i][j]=1; s.P[i][j]=0; }
}

static i128 simulateFrom(State s, const vector<pair<int,int>>& seq, int start, int &badTurn) {
    badTurn = -1;
    for (int t = start; t < T; t++) {
        if (!apply(s, seq[t].first, seq[t].second)) { badTurn = t; return -1; }
        produce(s);
    }
    return s.apples;
}

static i128 simulateFull(const vector<pair<int,int>>& seq) {
    State s; initState(s);
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

    auto now = [&](){ return chrono::duration<double>(chrono::steady_clock::now()-t0).count(); };

    // ----- Phase 1: build initial sequence using policy -----
    State s; initState(s);
    vector<pair<int,int>> seq; seq.reserve(T);

    // Small rollout for the first few turns to get a good opening.
    auto rollFull = [&](State ss, int turn, int i0, int j0) -> i128 {
        if (!apply(ss, i0, j0)) return -1;
        produce(ss);
        for (int t = turn+1; t < T; t++) {
            auto pr = policy(ss, T-t);
            if (!apply(ss, pr.first, pr.second)) {
                // no-op
            }
            produce(ss);
        }
        return ss.apples;
    };

    double phase1End = tl * 0.10;
    int openingTurns = min(T, 30);
    for (int t = 0; t < T; t++) {
        int R = T - t;
        int bi = -1, bj = -1;
        vector<tuple<long double,int,int>> cand;
        for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) {
            i128 cost = (i128)C[i][j] * ((i128)s.P[i][j] + 1);
            if (cost > s.apples) continue;
            long double v = marginalValue(s, i, j, R);
            cand.emplace_back(v / (long double)cost, i, j);
        }
        sort(cand.begin(), cand.end(),
             [](const tuple<long double,int,int>&a, const tuple<long double,int,int>&b){
                 return get<0>(a) > get<0>(b);
             });

        if (!cand.empty() && t < openingTurns && now() < phase1End) {
            int lim = min((int)cand.size(), 10);
            State nb = s; produce(nb);
            i128 bestSc = nb.apples;
            for (int k = 0; k < lim; k++) {
                if (now() >= phase1End) break;
                int i = get<1>(cand[k]), j = get<2>(cand[k]);
                i128 v = rollFull(s, t, i, j);
                if (v > bestSc) { bestSc = v; bi = i; bj = j; }
            }
        } else if (!cand.empty()) {
            bi = get<1>(cand[0]); bj = get<2>(cand[0]);
        }

        if (!apply(s, bi, bj)) { bi = -1; bj = -1; }
        produce(s);
        seq.emplace_back(bi, bj);
    }

    i128 bestScore = simulateFull(seq);
    if (bestScore < 0) {
        seq.assign(T, {-1,-1});
        bestScore = simulateFull(seq);
    }
    vector<pair<int,int>> bestSeq = seq;
    vector<pair<int,int>> curSeq = seq;
    i128 curScore = bestScore;

    // Snapshots
    vector<State> snaps(T+1);
    initState(snaps[0]);
    for (int t = 0; t < T; t++) {
        snaps[t+1] = snaps[t];
        apply(snaps[t+1], curSeq[t].first, curSeq[t].second);
        produce(snaps[t+1]);
    }

    // Actions
    vector<pair<int,int>> allActions;
    allActions.reserve(L*N + 1);
    for (int i = 0; i < L; i++) for (int j = 0; j < N; j++) allActions.emplace_back(i,j);
    int nActions = (int)allActions.size();

    vector<pair<int,int>> allActionsWithNoop = allActions;
    allActionsWithNoop.emplace_back(-1,-1);

    // Action pool: weights useful levels more heavily.
    vector<pair<int,int>> actionPool;
    actionPool.reserve(nActions*3 + 1);
    for (auto &a : allActions) {
        actionPool.push_back(a);
        if (a.first <= 1) actionPool.push_back(a);
        if (a.first == 0) actionPool.push_back(a);
    }
    // Noop appears only occasionally.
    actionPool.emplace_back(-1,-1);
    int nActionPool = (int)actionPool.size();

    mt19937_64 rng(0xC0FFEEULL ^ ((uint64_t)N<<32) ^ ((uint64_t)L<<24) ^ (uint64_t)T);

    auto log2ld = [](i128 x) -> double {
        if (x <= 0) return -1e18;
        return (double)(logl((long double)x) / logl(2.0L));
    };

    double curLog = log2ld(curScore);
    double bestLog = curLog;

    // Turn pool heavily front-loaded: earlier turns get much higher weight.
    vector<int> turnPool;
    turnPool.reserve(T*3);
    for (int t = 0; t < T; t++) {
        turnPool.push_back(t);
        if (t < T/2) turnPool.push_back(t);
        if (t < T/4) turnPool.push_back(t);
    }
    int nTurn = (int)turnPool.size();

    // Temperature schedule
    double tempHi = 0.06;
    double tempLo = 0.0002;
    double timeLimit = tl * 0.955;

    int resetCounter = 0;
    const int resetInterval = 15000;

    while (now() < timeLimit) {
        double elapsed = now();
        double frac = elapsed / timeLimit;
        if (frac > 1.0) frac = 1.0;
        double curTemp = tempHi * pow(tempLo/tempHi, frac);

        int mt = (int)(rng() % 100);
        int start = T;

        if (mt < 68) {
            // single action change
            int t = turnPool[rng() % nTurn];
            pair<int,int> na = actionPool[rng() % nActionPool];
            if (na == curSeq[t]) continue;
            pair<int,int> old = curSeq[t];
            curSeq[t] = na;
            start = t;
            int badTurn;
            i128 newScore = simulateFrom(snaps[start], curSeq, start, badTurn);
            if (newScore < 0) {
                curSeq[t] = old;
                continue;
            }
            double newLog = log2ld(newScore);
            double delta = newLog - curLog;
            bool accept = (delta >= 0) || ((double)(rng()%1000000)/1000000.0 < exp(delta / curTemp));
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
        } else if (mt < 82) {
            // swap adjacent
            if (T < 2) continue;
            int t = rng() % (T-1);
            if (curSeq[t] == curSeq[t+1]) continue;
            swap(curSeq[t], curSeq[t+1]);
            start = t;
            int badTurn;
            i128 newScore = simulateFrom(snaps[start], curSeq, start, badTurn);
            if (newScore < 0) { swap(curSeq[t], curSeq[t+1]); continue; }
            double newLog = log2ld(newScore);
            double delta = newLog - curLog;
            bool accept = (delta >= 0) || ((double)(rng()%1000000)/1000000.0 < exp(delta / curTemp));
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
        } else if (mt < 94) {
            // move: shift backward (to a later index) more often than forward
            int t = rng() % T;
            int u = rng() % T;
            if (t == u) continue;
            pair<int,int> a = curSeq[t];
            curSeq.erase(curSeq.begin()+t);
            int insPos = u;
            if (insPos > (int)curSeq.size()) insPos = (int)curSeq.size();
            curSeq.insert(curSeq.begin()+insPos, a);
            start = min(t, u);
            int badTurn;
            i128 newScore = simulateFrom(snaps[start], curSeq, start, badTurn);
            if (newScore < 0) {
                curSeq.erase(curSeq.begin()+insPos);
                curSeq.insert(curSeq.begin()+t, a);
                continue;
            }
            double newLog = log2ld(newScore);
            double delta = newLog - curLog;
            bool accept = (delta >= 0) || ((double)(rng()%1000000)/1000000.0 < exp(delta / curTemp));
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
        } else {
            // double mutation: change two turns
            int t1 = turnPool[rng() % nTurn];
            int t2 = turnPool[rng() % nTurn];
            if (t1 == t2) continue;
            if (t1 > t2) swap(t1, t2);
            pair<int,int> a1 = curSeq[t1], a2 = curSeq[t2];
            pair<int,int> n1 = actionPool[rng() % nActionPool];
            pair<int,int> n2 = actionPool[rng() % nActionPool];
            if (n1 == a1 && n2 == a2) continue;
            curSeq[t1] = n1; curSeq[t2] = n2;
            start = t1;
            int badTurn;
            i128 newScore = simulateFrom(snaps[start], curSeq, start, badTurn);
            if (newScore < 0) {
                curSeq[t1] = a1; curSeq[t2] = a2;
                continue;
            }
            double newLog = log2ld(newScore);
            double delta = newLog - curLog;
            bool accept = (delta >= 0) || ((double)(rng()%1000000)/1000000.0 < exp(delta / curTemp));
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
                curSeq[t1] = a1; curSeq[t2] = a2;
            }
        }

        resetCounter++;
        if (resetCounter > resetInterval && curLog < bestLog - 1e-9) {
            curSeq = bestSeq;
            curScore = bestScore;
            curLog = bestLog;
            initState(snaps[0]);
            for (int k = 0; k < T; k++) {
                snaps[k+1] = snaps[k];
                apply(snaps[k+1], curSeq[k].first, curSeq[k].second);
                produce(snaps[k+1]);
            }
            resetCounter = 0;
        }
    }

    // ----- Phase 3: final greedy polish on bestSeq -----
    {
        vector<State> bs(T+1);
        initState(bs[0]);
        for (int t = 0; t < T; t++) {
            bs[t+1] = bs[t];
            apply(bs[t+1], bestSeq[t].first, bestSeq[t].second);
            produce(bs[t+1]);
        }
        i128 bsc = simulateFull(bestSeq);
        if (bsc > 0) bestScore = bsc;
        double polishEnd = tl * 0.998;

        bool anyImprove = true;
        while (anyImprove && now() < polishEnd) {
            anyImprove = false;
            for (int t = 0; t < T && now() < polishEnd; t++) {
                pair<int,int> old = bestSeq[t];
                pair<int,int> bestA = old;
                i128 bestSc = bsc;
                for (int a = 0; a < (int)allActionsWithNoop.size(); a++) {
                    pair<int,int> na = allActionsWithNoop[a];
                    if (na == old) continue;
                    bestSeq[t] = na;
                    int badTurn;
                    i128 ns = simulateFrom(bs[t], bestSeq, t, badTurn);
                    if (ns > bestSc) {
                        bestSc = ns; bestA = na;
                    }
                }
                bestSeq[t] = bestA;
                if (bestA != old) {
                    bsc = bestSc;
                    anyImprove = true;
                    for (int k = t; k < T; k++) {
                        bs[k+1] = bs[k];
                        apply(bs[k+1], bestSeq[k].first, bestSeq[k].second);
                        produce(bs[k+1]);
                    }
                }
            }
        }
        bestScore = bsc;
    }

    for (auto &p : bestSeq) {
        if (p.first < 0) cout << -1 << "\n";
        else cout << p.first << " " << p.second << "\n";
    }
    return 0;
}
// EVOLVE-BLOCK-END
