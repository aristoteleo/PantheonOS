// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
typedef long long ll;
typedef __int128 i128;

static int N, L, T; static ll K;
static vector<ll> A, C;
static long double factL[6] = {1.0L,1.0L,2.0L,6.0L,24.0L,120.0L};

static inline long double estGain(const vector<ll>&B, const vector<ll>&P, int i, int j, int r) {
    if (r <= 0) return 0.0L;
    int idx = i*N+j;
    long double R = (long double)r;
    long double Bi = (long double)B[idx];
    long double pw = 1.0L;
    for (int k = 0; k <= i; k++) pw *= R;
    return (long double)A[j] * Bi * pw / factL[i+1];
}

int main(int argc, char** argv) {
    double tl = (argc > 1) ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    cin >> N >> L >> T >> K;
    A.assign(N, 0);
    for (auto &a : A) cin >> a;
    C.assign(L*N, 0);
    for (int i = 0; i < L; i++)
        for (int j = 0; j < N; j++) cin >> C[i*N+j];

    int SZ = L*N;
    using Act = pair<int,int>;
    vector<Act> acts(T, {-1,-1});

    // ---- Greedy init with lookahead scoring ----
    {
        vector<ll> B(SZ, 1), P(SZ, 0);
        i128 apples = (i128)K;
        for (int t = 0; t < T; t++) {
            int r = T - t;
            int bi = -1, bj = -1;
            long double best = 1.0L;
            for (int i = 0; i < L; i++) {
                int base = i*N;
                for (int j = 0; j < N; j++) {
                    int idx = base+j;
                    i128 cost = (i128)C[idx] * (P[idx] + 1);
                    if (cost > apples) continue;
                    long double g = estGain(B, P, i, j, r);
                    long double val = g / (long double)cost;
                    if (val > best) { best = val; bi = i; bj = j; }
                }
            }
            acts[t] = {bi, bj};
            if (bi >= 0) {
                int idx = bi*N+bj;
                apples -= (i128)C[idx] * (P[idx] + 1);
                P[idx]++;
            }
            for (int jj = 0; jj < N; jj++)
                apples += (i128)A[jj] * B[jj] * P[jj];
            for (int ii = 1; ii < L; ii++) {
                int base = ii*N, prev = (ii-1)*N;
                for (int jj = 0; jj < N; jj++)
                    B[prev+jj] += B[base+jj] * P[base+jj];
            }
        }
    }

    // Snapshot arrays per-turn
    vector<i128> snapA(T+1);
    vector<vector<ll>> snapB(T+1), snapP(T+1);
    for (int t = 0; t <= T; t++) { snapB[t].assign(SZ, 1); snapP[t].assign(SZ, 0); }

    vector<ll> workB(SZ), workP(SZ);

    auto evalFrom = [&](const vector<Act>& a, int p, bool commit) -> i128 {
        i128 apples = snapA[p];
        memcpy(workB.data(), snapB[p].data(), SZ * sizeof(ll));
        memcpy(workP.data(), snapP[p].data(), SZ * sizeof(ll));
        for (int t = p; t < T; t++) {
            int i = a[t].first, j = a[t].second;
            if (i >= 0) {
                int idx = i*N+j;
                i128 cost = (i128)C[idx] * (workP[idx] + 1);
                if (cost > apples) return -1;
                apples -= cost;
                workP[idx] += 1;
            }
            for (int jj = 0; jj < N; jj++)
                apples += (i128)A[jj] * workB[jj] * workP[jj];
            for (int ii = 1; ii < L; ii++) {
                int base = ii*N, prev = (ii-1)*N;
                for (int jj = 0; jj < N; jj++)
                    workB[prev+jj] += workB[base+jj] * workP[base+jj];
            }
            if (commit) {
                snapA[t+1] = apples;
                memcpy(snapB[t+1].data(), workB.data(), SZ * sizeof(ll));
                memcpy(snapP[t+1].data(), workP.data(), SZ * sizeof(ll));
            }
        }
        if (apples < 1) return -1;
        return apples;
    };

    snapA[0] = (i128)K;
    i128 curVal = evalFrom(acts, 0, true);
    if (curVal < 0) curVal = (i128)K;

    vector<Act> bestActs = acts;
    i128 bestVal = curVal;

    uint64_t rngState = 0x9E3779B97F4A7C15ULL ^ (uint64_t)chrono::steady_clock::now().time_since_epoch().count();
    auto xrand = [&]() -> uint64_t {
        rngState ^= rngState << 13;
        rngState ^= rngState >> 7;
        rngState ^= rngState << 17;
        return rngState;
    };
    auto randU = [&](int m) { return (int)(xrand() % (uint64_t)m); };
    auto randD = [&]() { return (double)(xrand() >> 11) / (double)(1ULL << 53); };

    // Precompute list of non-empty positions in greedy, for targeted moves
    vector<int> nonEmpty;
    for (int t = 0; t < T; t++) if (acts[t].first >= 0) nonEmpty.push_back(t);

    vector<Act> cand;
    cand.reserve(T);
    double budget = tl * 0.95;
    int iter = 0;
    int noImprove = 0;

    // Log2 temperature for SA on log2(S) scale
    double T0 = 0.010;
    double Tmin = 0.00002;

    long double curLog = log2l((long double)curVal);

    while (true) {
        if ((iter & 31) == 0) {
            double el = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
            if (el >= budget) break;
        }
        iter++;

        double el = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
        double frac = el / budget;
        double temp;
        if (noImprove > 3000) {
            temp = T0 * 0.25;
            if (noImprove > 8000) noImprove = 0;
        } else {
            temp = T0 * (1.0 - frac) * (1.0 - frac) + Tmin;
        }

        cand = acts;
        int firstChanged = T; // min changed position

        int moveType = (int)(xrand() % 20);

        if (moveType < 5) {
            // single replacement at random position
            int p = randU(T);
            firstChanged = p;
            if (moveType == 0) {
                cand[p] = {-1, -1};
            } else {
                int i, j;
                if (randD() < 0.5) i = randU(L);
                else {
                    int wsum = 0, w[8];
                    for (int k = 0; k < L; k++) { w[k] = (k + 1); wsum += w[k]; }
                    int pick = randU(wsum), acc = 0; i = 0;
                    for (int k = 0; k < L; k++) { acc += w[k]; if (pick < acc) { i = k; break; } }
                }
                j = randU(N);
                cand[p] = {i, j};
            }
        } else if (moveType < 8) {
            // swap two random positions
            int p = randU(T), q = randU(T);
            if (p == q) continue;
            swap(cand[p], cand[q]);
            firstChanged = min(p, q);
        } else if (moveType < 10) {
            // adjacent swap with helper
            int p = randU(T - 1);
            swap(cand[p], cand[p+1]);
            firstChanged = p;
        } else if (moveType < 12) {
            // toggle -1 <-> action
            int p = randU(T);
            firstChanged = p;
            if (cand[p].first < 0) {
                cand[p] = {randU(L), randU(N)};
            } else {
                cand[p] = {-1, -1};
            }
        } else if (moveType < 14) {
            // small block replace
            int p = randU(T);
            int len = 1 + randU(3);
            if (p + len > T) len = T - p;
            firstChanged = p;
            for (int k = 0; k < len; k++) {
                if (randD() < 0.15) cand[p+k] = {-1,-1};
                else cand[p+k] = {randU(L), randU(N)};
            }
        } else if (moveType < 16) {
            // 3-opt-ish block rotation
            int p = randU(T);
            int len = 2 + randU(4);
            if (p + len > T) len = T - p;
            if (len < 2) continue;
            firstChanged = p;
            Act tmp = cand[p];
            for (int k = 0; k < len-1; k++) cand[p+k] = cand[p+k+1];
            cand[p+len-1] = tmp;
        } else if (moveType < 18) {
            // Targeted upgrade-of-a-failed-position: pick a position that found no action
            // and try an action there
            int p = randU(T);
            firstChanged = p;
            if (cand[p].first < 0) {
                int j = randU(N);
                int i;
                // weight toward low levels (usually produce immediate apples)
                int wsum = 0, w[8];
                for (int k = 0; k < L; k++) { w[k] = (L - k); wsum += w[k]; }
                int pick = randU(wsum), acc = 0; i = 0;
                for (int k = 0; k < L; k++) { acc += w[k]; if (pick < acc) { i = k; break; } }
                cand[p] = {i, j};
            } else {
                // try swapping this action's machine type with a random one
                int i2 = randU(L), j2 = randU(N);
                cand[p] = {i2, j2};
            }
        } else {
            // Large-window random reshuffle: shuffle 4-7 positions
            int p = randU(T);
            int len = 4 + randU(4);
            if (p + len > T) len = T - p;
            if (len < 2) continue;
            firstChanged = p;
            for (int k = len-1; k > 0; k--) {
                int r = randU(k+1);
                swap(cand[p+k], cand[p+r]);
            }
        }

        i128 v = evalFrom(cand, firstChanged, false);
        if (v <= 0) continue;

        long double vLog = log2l((long double)v);
        long double delta = vLog - curLog;
        bool accept = false;
        if (delta >= 0.0) {
            accept = true;
        } else if (temp > 0) {
            double prob = exp((double)(delta / temp));
            if (randD() < prob) accept = true;
        }

        if (accept) {
            i128 vc = evalFrom(cand, firstChanged, true);
            if (vc <= 0) continue;
            acts = cand;
            curVal = vc;
            curLog = vLog;
            if (vc > bestVal) {
                bestVal = vc;
                bestActs = acts;
                noImprove = 0;
            } else {
                noImprove++;
            }
        } else {
            noImprove++;
        }
    }

    for (int t = 0; t < T; t++) {
        int i = bestActs[t].first, j = bestActs[t].second;
        if (i < 0) cout << "-1\n";
        else cout << i << " " << j << "\n";
    }
    return 0;
}
// EVOLVE-BLOCK-END
