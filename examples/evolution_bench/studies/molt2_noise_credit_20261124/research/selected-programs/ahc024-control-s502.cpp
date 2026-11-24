// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;

static const int di4[4]={-1,1,0,0}, dj4[4]={0,0,-1,1};

int main(int argc, char**argv){
    double tl = argc>1 ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    int n, m;
    if(!(cin >> n >> m)) return 0;
    vector<vector<int>> orig(n, vector<int>(n));
    for(int i=0;i<n;i++) for(int j=0;j<n;j++) cin >> orig[i][j];

    int NC = m+1;
    vector<vector<char>> A0(NC, vector<char>(NC, 0));
    vector<int> colorCount0(NC, 0);
    for(int i=0;i<n;i++) for(int j=0;j<n;j++){
        int a = orig[i][j];
        colorCount0[a]++;
        if(j+1<n){ int b=orig[i][j+1]; if(a!=b) A0[a][b]=A0[b][a]=1; }
        if(i+1<n){ int b=orig[i+1][j]; if(a!=b) A0[a][b]=A0[b][a]=1; }
        if(i==0||i==n-1||j==0||j==n-1){ if(a!=0) A0[a][0]=A0[0][a]=1; }
    }

    vector<vector<int>> cur = orig;
    vector<vector<int>> best = orig;
    int curZeros = colorCount0[0];
    int bestZeros = curZeros;

    vector<vector<int>> cnt(NC, vector<int>(NC, 0));
    for(int i=0;i<n;i++) for(int j=0;j<n;j++){
        int a = orig[i][j];
        if(j+1<n){ int b=orig[i][j+1]; if(a!=b){ cnt[a][b]++; cnt[b][a]++; } }
        if(i+1<n){ int b=orig[i+1][j]; if(a!=b){ cnt[a][b]++; cnt[b][a]++; } }
        if(i==0||i==n-1||j==0||j==n-1){ if(a!=0){ cnt[a][0]++; cnt[0][a]++; } }
    }
    vector<int> colorCount = colorCount0;

    vector<int> vis(n*n, 0);
    int token = 0;
    vector<int> q; q.reserve(n*n);

    auto connectivityNonZero = [&](int c) -> bool {
        token++;
        int total = colorCount[c];
        if(total==0) return false;
        if(total==1) return true;
        int si=-1,sj=-1;
        for(int i=0;i<n && si==-1;i++)
            for(int j=0;j<n;j++) if(cur[i][j]==c){ si=i; sj=j; break; }
        q.clear();
        int sid=si*n+sj;
        vis[sid]=token;
        q.push_back(sid);
        int visited=1;
        for(size_t h=0;h<q.size();h++){
            int id=q[h]; int i=id/n, j=id%n;
            for(int d=0;d<4;d++){
                int ni=i+di4[d], nj=j+dj4[d];
                if(ni>=0&&ni<n&&nj>=0&&nj<n&&cur[ni][nj]==c){
                    int nid=ni*n+nj;
                    if(vis[nid]!=token){ vis[nid]=token; visited++; q.push_back(nid); }
                }
            }
        }
        return visited==total;
    };

    auto connectivityZero = [&]() -> bool {
        token++;
        q.clear();
        int zeros = colorCount[0];
        if(zeros==0) return true;
        for(int j=0;j<n;j++){
            if(cur[0][j]==0){ int id=j; if(vis[id]!=token){ vis[id]=token; q.push_back(id);} }
            if(cur[n-1][j]==0){ int id=(n-1)*n+j; if(vis[id]!=token){ vis[id]=token; q.push_back(id);} }
        }
        for(int i=1;i<n-1;i++){
            if(cur[i][0]==0){ int id=i*n; if(vis[id]!=token){ vis[id]=token; q.push_back(id);} }
            if(cur[i][n-1]==0){ int id=i*n+n-1; if(vis[id]!=token){ vis[id]=token; q.push_back(id);} }
        }
        if(q.empty()) return false;
        for(size_t h=0;h<q.size();h++){
            int id=q[h]; int i=id/n, j=id%n;
            for(int d=0;d<4;d++){
                int ni=i+di4[d], nj=j+dj4[d];
                if(ni>=0&&ni<n&&nj>=0&&nj<n&&cur[ni][nj]==0){
                    int nid=ni*n+nj;
                    if(vis[nid]!=token){ vis[nid]=token; q.push_back(nid); }
                }
            }
        }
        return (int)q.size()==zeros;
    };

    auto doMove = [&](int i, int j, int oldc, int newc){
        for(int d=0;d<4;d++){
            int ni=i+di4[d], nj=j+dj4[d];
            if(ni>=0&&ni<n&&nj>=0&&nj<n){
                int nb=cur[ni][nj];
                if(oldc!=nb){ cnt[oldc][nb]--; cnt[nb][oldc]--; }
                if(newc!=nb){ cnt[newc][nb]++; cnt[nb][newc]++; }
            }
        }
        if(i==0||i==n-1||j==0||j==n-1){
            if(oldc!=0){ cnt[oldc][0]--; cnt[0][oldc]--; }
            if(newc!=0){ cnt[newc][0]++; cnt[0][newc]++; }
        }
        colorCount[oldc]--;
        colorCount[newc]++;
        if(oldc==0) curZeros--;
        if(newc==0) curZeros++;
        cur[i][j]=newc;
    };

    auto adjacencyOK = [&](int a, int b) -> bool {
        for(int x=0;x<NC;x++){
            if((cnt[a][x]>0) != (A0[a][x]>0)) return false;
        }
        if(a!=b) for(int x=0;x<NC;x++){
            if((cnt[b][x]>0) != (A0[b][x]>0)) return false;
        }
        return true;
    };

    mt19937_64 rng(chrono::steady_clock::now().time_since_epoch().count() ^ (uint64_t)(size_t)&cur);
    double deadline = tl * 0.88;
    double T0 = 1.5, Tmin = 0.02;

    int candBuf[8];
    long long iter=0;
    while(true){
        if((iter & 63)==0){
            double el = chrono::duration<double>(chrono::steady_clock::now()-t0).count();
            if(el > deadline) break;
        }
        iter++;
        int i = rng()%n;
        int j = rng()%n;
        int oldc = cur[i][j];
        int cn=0;
        for(int d=0;d<4;d++){
            int ni=i+di4[d], nj=j+dj4[d];
            if(ni>=0&&ni<n&&nj>=0&&nj<n){
                int c=cur[ni][nj];
                if(c!=oldc){
                    bool dup=false;
                    for(int t=0;t<cn;t++) if(candBuf[t]==c){dup=true;break;}
                    if(!dup) candBuf[cn++]=c;
                }
            }
        }
        if(i==0||i==n-1||j==0||j==n-1){
            if(oldc!=0){
                bool dup=false;
                for(int t=0;t<cn;t++) if(candBuf[t]==0){dup=true;break;}
                if(!dup) candBuf[cn++]=0;
            }
        }
        if(cn==0) continue;
        int zeroIdx=-1;
        for(int t=0;t<cn;t++) if(candBuf[t]==0){zeroIdx=t;break;}
        int newc;
        // Bias toward 0 (erosion), and if the cell is not adjacent to 0 currently,
        // still allow transfer-to-neighbor moves that can unlock later erosion.
        if(zeroIdx>=0 && (rng()%100) < 55) newc=0;
        else newc = candBuf[rng()%cn];
        if(newc==oldc) continue;

        doMove(i,j,oldc,newc);
        bool ok=true;
        if(oldc!=0){
            if(colorCount[oldc]==0) ok=false;
            else if(!connectivityNonZero(oldc)) ok=false;
        }
        if(ok && (oldc==0 || newc==0)){
            if(!connectivityZero()) ok=false;
        }
        if(ok && !adjacencyOK(oldc,newc)) ok=false;

        if(ok){
            int delta = (newc==0?1:0)-(oldc==0?1:0);
            bool accept=false;
            if(delta>=0) accept=true;
            else {
                double el = chrono::duration<double>(chrono::steady_clock::now()-t0).count();
                double frac = el/deadline;
                double T = T0 * pow(Tmin/T0, frac);
                double r = (double)(rng()&0xFFFFFF)/(double)0x1000000;
                if(exp(delta/T) > r) accept=true;
            }
            if(accept){
                if(curZeros > bestZeros){ bestZeros=curZeros; best=cur; }
            } else {
                doMove(i,j,newc,oldc);
            }
        } else {
            doMove(i,j,newc,oldc);
        }
    }

    // Phase 2: multi-move SA focusing on sequences that relocate cells.
    // Then greedy erosion.
    cur = best;
    {
        fill(colorCount.begin(), colorCount.end(), 0);
        for(int i=0;i<n;i++) for(int j=0;j<n;j++) colorCount[cur[i][j]]++;
        for(int a=0;a<NC;a++) for(int b=0;b<NC;b++) cnt[a][b]=0;
        for(int i=0;i<n;i++) for(int j=0;j<n;j++){
            int a=cur[i][j];
            if(j+1<n){ int b=cur[i][j+1]; if(a!=b){cnt[a][b]++;cnt[b][a]++;} }
            if(i+1<n){ int b=cur[i+1][j]; if(a!=b){cnt[a][b]++;cnt[b][a]++;} }
            if(i==0||i==n-1||j==0||j==n-1){ if(a!=0){cnt[a][0]++;cnt[0][a]++;} }
        }
        curZeros = colorCount[0];
        if(curZeros>bestZeros){ bestZeros=curZeros; best=cur; }
    }

    vector<int> order(n*n);
    iota(order.begin(), order.end(), 0);

    // Greedy erosion with multiple passes. Accept any single-cell removal that
    // preserves invariants. Use randomized order per pass.
    for(int pass=0; pass<400; pass++){
        double el = chrono::duration<double>(chrono::steady_clock::now()-t0).count();
        if(el > tl*0.98) break;
        for(int k=n*n-1;k>0;k--){ int r=rng()%(k+1); swap(order[k],order[r]); }
        bool changed=false;
        for(int idx=0; idx<n*n; idx++){
            int id=order[idx];
            int i=id/n, j=id%n;
            int oldc=cur[i][j];
            if(oldc==0) continue;
            // Only attempt if the cell is a "leaf" candidate: either the color has
            // more than one cell, or the cell is adjacent to 0.
            bool neigh0 = (i==0||i==n-1||j==0||j==n-1);
            for(int d=0;d<4 && !neigh0;d++){
                int ni=i+di4[d], nj=j+dj4[d];
                if(ni>=0&&ni<n&&nj>=0&&nj<n && cur[ni][nj]==0) neigh0=true;
            }
            if(!neigh0) continue;
            if(colorCount[oldc]<=1) continue;
            doMove(i,j,oldc,0);
            bool ok=true;
            if(colorCount[oldc]==0) ok=false;
            else if(!connectivityNonZero(oldc)) ok=false;
            if(ok && !connectivityZero()) ok=false;
            if(ok && !adjacencyOK(oldc,0)) ok=false;
            if(ok){
                changed=true;
                if(curZeros>bestZeros){ bestZeros=curZeros; best=cur; }
            } else {
                doMove(i,j,0,oldc);
            }
        }
        if(!changed) break;
    }

    // Final: try a "scan + retry with limited neighbor-color moves" pass.
    // For each non-zero cell with a non-zero neighbor of another color, try
    // reassigning to that neighbor color (if adjacency allows), which may
    // unlock future erosions. Then run another greedy erosion sweep.
    for(int pass=0; pass<60; pass++){
        double el = chrono::duration<double>(chrono::steady_clock::now()-t0).count();
        if(el > tl*0.995) break;
        bool changed=false;
        for(int k=n*n-1;k>0;k--){ int r=rng()%(k+1); swap(order[k],order[r]); }
        for(int idx=0; idx<n*n; idx++){
            int id=order[idx];
            int i=id/n, j=id%n;
            int oldc=cur[i][j];
            if(oldc==0) continue;
            // Try neighbor colors as candidates
            int cand2[4]; int c2n=0;
            for(int d=0;d<4;d++){
                int ni=i+di4[d], nj=j+dj4[d];
                if(ni>=0&&ni<n&&nj>=0&&nj<n){
                    int c=cur[ni][nj];
                    if(c!=oldc && c!=0){
                        bool dup=false;
                        for(int t=0;t<c2n;t++) if(cand2[t]==c){dup=true;break;}
                        if(!dup) cand2[c2n++]=c;
                    }
                }
            }
            if(c2n==0) continue;
            int newc = cand2[rng()%c2n];
            doMove(i,j,oldc,newc);
            bool ok=true;
            if(colorCount[oldc]==0) ok=false;
            else if(!connectivityNonZero(oldc)) ok=false;
            if(ok && !adjacencyOK(oldc,newc)) ok=false;
            if(ok){
                changed=true;
                if(curZeros>bestZeros){ bestZeros=curZeros; best=cur; }
            } else {
                doMove(i,j,newc,oldc);
            }
        }
        // Run a quick erosion sweep
        for(int k=n*n-1;k>0;k--){ int r=rng()%(k+1); swap(order[k],order[r]); }
        for(int idx=0; idx<n*n; idx++){
            int id=order[idx];
            int i=id/n, j=id%n;
            int oldc=cur[i][j];
            if(oldc==0) continue;
            bool neigh0 = (i==0||i==n-1||j==0||j==n-1);
            for(int d=0;d<4 && !neigh0;d++){
                int ni=i+di4[d], nj=j+dj4[d];
                if(ni>=0&&ni<n&&nj>=0&&nj<n && cur[ni][nj]==0) neigh0=true;
            }
            if(!neigh0) continue;
            if(colorCount[oldc]<=1) continue;
            doMove(i,j,oldc,0);
            bool ok=true;
            if(colorCount[oldc]==0) ok=false;
            else if(!connectivityNonZero(oldc)) ok=false;
            if(ok && !connectivityZero()) ok=false;
            if(ok && !adjacencyOK(oldc,0)) ok=false;
            if(ok){
                changed=true;
                if(curZeros>bestZeros){ bestZeros=curZeros; best=cur; }
            } else {
                doMove(i,j,0,oldc);
            }
        }
        if(!changed) break;
    }

    if(curZeros>bestZeros){ bestZeros=curZeros; best=cur; }

    string out;
    out.reserve((size_t)n*(n*4+2));
    for(int i=0;i<n;i++){
        for(int j=0;j<n;j++){
            if(j) out+=' ';
            out+=to_string(best[i][j]);
        }
        out+='\n';
    }
    fwrite(out.data(),1,out.size(),stdout);
    return 0;
}
// EVOLVE-BLOCK-END
