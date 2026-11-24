// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;

int n, m;
static const int di[4]={-1,1,0,0}, dj[4]={0,0,-1,1};

int main(int argc, char**argv){
    double tl = argc>1? atof(argv[1]): 2.0;
    auto t0 = chrono::steady_clock::now();
    
    cin >> n >> m;
    vector<vector<int>> orig(n, vector<int>(n));
    for(int i=0;i<n;i++) for(int j=0;j<n;j++) cin >> orig[i][j];
    
    // A0
    vector<vector<char>> A0(m+1, vector<char>(m+1, 0));
    for(int i=0;i<n;i++) for(int j=0;j<n;j++) {
        int a = orig[i][j];
        if(j+1<n) {
            int b = orig[i][j+1];
            if(a != b) A0[a][b] = A0[b][a] = 1;
        }
        if(i+1<n) {
            int b = orig[i+1][j];
            if(a != b) A0[a][b] = A0[b][a] = 1;
        }
        if(i==0 || i==n-1 || j==0 || j==n-1) {
            if(a != 0) A0[a][0] = A0[0][a] = 1;
        }
    }
    
    // cnt and colorCount
    vector<vector<int>> cnt(m+1, vector<int>(m+1, 0));
    vector<int> colorCount(m+1, 0);
    for(int i=0;i<n;i++) for(int j=0;j<n;j++) {
        int a = orig[i][j];
        colorCount[a]++;
        if(j+1<n) {
            int b = orig[i][j+1];
            if(a != b) { cnt[a][b]++; cnt[b][a]++; }
        }
        if(i+1<n) {
            int b = orig[i+1][j];
            if(a != b) { cnt[a][b]++; cnt[b][a]++; }
        }
        if(i==0 || i==n-1 || j==0 || j==n-1) {
            if(a != 0) { cnt[a][0]++; cnt[0][a]++; }
        }
    }
    
    vector<vector<int>> cur = orig;
    vector<vector<int>> best = orig;
    int curZeros = colorCount[0];
    int bestZeros = curZeros;
    
    // for BFS
    vector<vector<int>> visToken(n, vector<int>(n, 0));
    int token = 0;
    vector<pair<int,int>> q; q.reserve(n*n);
    
    auto getNeighborColors = [&](int i, int j) {
        vector<int> nbs;
        for(int d=0;d<4;d++) {
            int ni=i+di[d], nj=j+dj[d];
            if(ni>=0 && ni<n && nj>=0 && nj<n) nbs.push_back(cur[ni][nj]);
        }
        if(i==0 || i==n-1 || j==0 || j==n-1) nbs.push_back(0);
        return nbs;
    };
    
    auto applyMove = [&](int i, int j, int oldc, int newc) {
        auto nbs = getNeighborColors(i, j);
        for(int nb : nbs) {
            if(oldc != nb) { cnt[oldc][nb]--; cnt[nb][oldc]--; }
            if(newc != nb) { cnt[newc][nb]++; cnt[nb][newc]++; }
        }
        colorCount[oldc]--;
        colorCount[newc]++;
        if(oldc == 0) curZeros--;
        if(newc == 0) curZeros++;
        cur[i][j] = newc;
    };
    
    auto checkConnectivity = [&](int c) -> bool {
        token++;
        if(c == 0) {
            vector<pair<int,int>> zeros;
            q.clear();
            for(int i=0;i<n;i++) for(int j=0;j<n;j++) {
                if(cur[i][j] == 0) {
                    zeros.push_back({i,j});
                    if(i==0 || i==n-1 || j==0 || j==n-1) {
                        visToken[i][j] = token;
                        q.push_back({i,j});
                    }
                }
            }
            if(zeros.empty()) return true;
            if(q.empty()) return false;
            for(size_t h=0; h<q.size(); h++) {
                auto [i,j] = q[h];
                for(int d=0;d<4;d++) {
                    int ni=i+di[d], nj=j+dj[d];
                    if(ni>=0 && ni<n && nj>=0 && nj<n && cur[ni][nj]==0 && visToken[ni][nj] != token) {
                        visToken[ni][nj] = token;
                        q.push_back({ni,nj});
                    }
                }
            }
            for(auto&p: zeros) {
                if(visToken[p.first][p.second] != token) return false;
            }
            return true;
        } else {
            int total = colorCount[c];
            if(total == 0) return false;
            int si=-1, sj=-1;
            for(int i=0;i<n && si==-1;i++) for(int j=0;j<n;j++) {
                if(cur[i][j] == c) { si=i; sj=j; break; }
            }
            if(si==-1) return false;
            q.clear();
            visToken[si][sj] = token;
            q.push_back({si,sj});
            int cntVis = 1;
            for(size_t h=0; h<q.size(); h++) {
                auto [i,j] = q[h];
                for(int d=0;d<4;d++) {
                    int ni=i+di[d], nj=j+dj[d];
                    if(ni>=0 && ni<n && nj>=0 && nj<n && cur[ni][nj]==c && visToken[ni][nj] != token) {
                        visToken[ni][nj] = token;
                        q.push_back({ni,nj});
                        cntVis++;
                    }
                }
            }
            return cntVis == total;
        }
    };
    
    mt19937 rng(chrono::steady_clock::now().time_since_epoch().count());
    double deadline = tl * 0.95;
    double T0 = 1.0;
    
    int iter = 0;
    while(true) {
        if((iter & 255) == 0) {
            double el = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
            if(el > deadline) break;
        }
        iter++;
        
        int i = rng() % n;
        int j = rng() % n;
        int oldc = cur[i][j];
        
        vector<int> cand;
        bool seen[256] = {false};
        for(int d=0;d<4;d++) {
            int ni=i+di[d], nj=j+dj[d];
            if(ni>=0 && ni<n && nj>=0 && nj<n) {
                int c = cur[ni][nj];
                if(c != oldc && !seen[c]) { seen[c]=true; cand.push_back(c); }
            }
        }
        if(i==0 || i==n-1 || j==0 || j==n-1) {
            if(0 != oldc && !seen[0]) { seen[0]=true; cand.push_back(0); }
        }
        
        if(cand.empty()) continue;
        int newc = cand[rng() % cand.size()];
        
        applyMove(i, j, oldc, newc);
        
        bool ok = true;
        if(oldc != 0) {
            if(colorCount[oldc] == 0 || !checkConnectivity(oldc)) ok = false;
        }
        if(ok && (oldc == 0 || newc == 0) && oldc != newc) {
            if(!checkConnectivity(0)) ok = false;
        }
        if(ok) {
            for(int x=0; x<=m; x++) {
                if((cnt[oldc][x] > 0) != (A0[oldc][x] > 0)) { ok = false; break; }
                if((cnt[newc][x] > 0) != (A0[newc][x] > 0)) { ok = false; break; }
            }
        }
        
        if(ok) {
            int delta = (newc == 0 ? 1 : 0) - (oldc == 0 ? 1 : 0);
            bool accept = false;
            if(delta >= 0) accept = true;
            else {
                double el = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
                double T = T0 * (1.0 - el / deadline) + 0.01;
                double prob = exp(delta / T);
                if((rng() % 1000000) < 1000000 * prob) accept = true;
            }
            if(accept) {
                if(curZeros > bestZeros) {
                    bestZeros = curZeros;
                    best = cur;
                }
            } else {
                applyMove(i, j, newc, oldc);
            }
        } else {
            applyMove(i, j, newc, oldc);
        }
    }
    
    for(int i=0;i<n;i++) {
        for(int j=0;j<n;j++) {
            if(j) cout << ' ';
            cout << best[i][j];
        }
        cout << '\n';
    }
    
    return 0;
}
// EVOLVE-BLOCK-END
