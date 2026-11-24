// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
int n, m;
vector<vector<int>> orig, cur, best;
vector<vector<char>> A0;
const int di[4]={-1,1,0,0}, dj[4]={0,0,-1,1};
static int at(const vector<vector<int>>&g, int i, int j){return (i<0||j<0||i>=n||j>=n)?0:g[i][j];}
static vector<vector<char>> adjacency(const vector<vector<int>>&g){
    vector<vector<char>> A(m+1, vector<char>(m+1,0));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++)for(int d=0;d<4;d++){
        int a=g[i][j],b=at(g,i+di[d],j+dj[d]);
        if(a!=b)A[a][b]=A[b][a]=1;
    }
    return A;
}
static bool connectedAll(const vector<vector<int>>&g){
    vector<int> cntv(m+1,0), seen(m+1,0);
    for(auto&r:g)for(int c:r)cntv[c]++;
    vector<vector<char>> vis(n, vector<char>(n,0));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++)if(!vis[i][j]&&g[i][j]!=0){
        int c=g[i][j]; if(seen[c])return false; seen[c]=1;
        queue<pair<int,int>> q; q.push({i,j}); vis[i][j]=1; int k=0;
        while(!q.empty()){auto[x,y]=q.front();q.pop();k++;
            for(int d=0;d<4;d++){int a=x+di[d],b=y+dj[d];
                if(a>=0&&b>=0&&a<n&&b<n&&!vis[a][b]&&g[a][b]==c){vis[a][b]=1;q.push({a,b});}}}
        if(k!=cntv[c])return false;
    }
    queue<pair<int,int>> q; int z=0;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++)if(g[i][j]==0&&(i==0||j==0||i==n-1||j==n-1)){vis[i][j]=1;q.push({i,j});z++;}
    while(!q.empty()){auto[x,y]=q.front();q.pop();
        for(int d=0;d<4;d++){int a=x+di[d],b=y+dj[d];
            if(a>=0&&b>=0&&a<n&&b<n&&!vis[a][b]&&g[a][b]==0){vis[a][b]=1;q.push({a,b});z++;}}}
    return z==cntv[0];
}

int val[105][105];
int cellcnt[105];
static inline void addEdge(int a,int b){if(a==b)return;if(a>b)swap(a,b);val[a][b]++;}
static inline void remEdge(int a,int b){if(a==b)return;if(a>b)swap(a,b);val[a][b]--;}
static inline int getEdge(int a,int b){if(a==b)return 0;if(a>b)swap(a,b);return val[a][b];}
static inline bool pairOk(int a,int b){if(a==b)return true;int v=getEdge(a,b);int w=A0[a][b];return (v>0)==(w>0);}
static void buildCnt(){
    memset(val,0,sizeof(val));
    memset(cellcnt,0,sizeof(cellcnt));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++)cellcnt[cur[i][j]]++;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        int c=cur[i][j];
        if(i+1<n && cur[i+1][j]!=c) addEdge(c, cur[i+1][j]);
        if(j+1<n && cur[i][j+1]!=c) addEdge(c, cur[i][j+1]);
    }
}
static void applyChange(int i,int j,int nc){
    int oc=cur[i][j];
    if(oc==nc)return;
    for(int d=0;d<4;d++){
        int ni=i+di[d], nj=j+dj[d];
        int nb=(ni>=0&&nj>=0&&ni<n&&nj<n)?cur[ni][nj]:0;
        remEdge(oc,nb); addEdge(nc,nb);
    }
    cur[i][j]=nc;
    cellcnt[oc]--; cellcnt[nc]++;
}
static bool connCheck(int c){
    int target=cellcnt[c];
    if(target==0)return false;
    static vector<vector<char>> vis;
    vis.assign(n, vector<char>(n,0));
    queue<pair<int,int>> q; int k=0;
    if(c==0){
        for(int i=0;i<n;i++)for(int j=0;j<n;j++)
            if(cur[i][j]==0&&(i==0||j==0||i==n-1||j==n-1)&&!vis[i][j]){vis[i][j]=1;q.push({i,j});k++;}
    } else {
        bool st=false;
        for(int i=0;i<n&&!st;i++)for(int j=0;j<n&&!st;j++)if(cur[i][j]==c){vis[i][j]=1;q.push({i,j});k++;st=true;}
        if(!st)return false;
    }
    while(!q.empty()){
        auto[x,y]=q.front();q.pop();
        for(int d=0;d<4;d++){
            int a=x+di[d],b=y+dj[d];
            if(a>=0&&b>=0&&a<n&&b<n&&!vis[a][b]&&cur[a][b]==c){vis[a][b]=1;q.push({a,b});k++;}
        }
    }
    return k==target;
}
static bool tryErase(int i,int j){
    if(cur[i][j]==0)return false;
    int oc=cur[i][j];
    int pa[8], pa2[8], affN=0;
    for(int d=0;d<4;d++){
        int ni=i+di[d], nj=j+dj[d];
        int nb=(ni>=0&&nj>=0&&ni<n&&nj<n)?cur[ni][nj]:0;
        pa[affN]=oc; pa2[affN]=nb; affN++;
    }
    applyChange(i,j,0);
    bool ok=true;
    for(int k=0;k<affN;k++) if(!pairOk(pa[k],pa2[k])||!pairOk(0,pa2[k])){ok=false;break;}
    if(ok){
        if(!connCheck(oc)) ok=false;
        if(ok && !connCheck(0)) ok=false;
    }
    if(!ok){applyChange(i,j,oc);return false;}
    return true;
}
static bool tryRecolor(int i,int j){
    if(cur[i][j]==0)return false;
    int oc=cur[i][j];
    int cand[4]; int cn=0;
    for(int d=0;d<4;d++){
        int ni=i+di[d], nj=j+dj[d];
        if(ni>=0&&nj>=0&&ni<n&&nj<n&&cur[ni][nj]!=0&&cur[ni][nj]!=oc) cand[cn++]=cur[ni][nj];
    }
    if(cn==0)return false;
    int nc=cand[rand()%cn];
    int pa[8], pa2[8], affN=0;
    for(int d=0;d<4;d++){
        int ni=i+di[d], nj=j+dj[d];
        int nb=(ni>=0&&nj>=0&&ni<n&&nj<n)?cur[ni][nj]:0;
        if(nb!=oc){pa[affN]=oc; pa2[affN]=nb; affN++;}
        if(nb!=nc){pa[affN]=nc; pa2[affN]=nb; affN++;}
    }
    applyChange(i,j,nc);
    bool ok=true;
    for(int k=0;k<affN;k++) if(!pairOk(pa[k],pa2[k])){ok=false;break;}
    if(ok){
        if(!connCheck(oc)) ok=false;
        if(ok && !connCheck(nc)) ok=false;
    }
    if(!ok){applyChange(i,j,oc);return false;}
    return true;
}
static bool trySwap(int i1,int j1,int i2,int j2){
    int c1=cur[i1][j1], c2=cur[i2][j2];
    if(c1==0||c2==0||c1==c2)return false;
    applyChange(i1,j1,c2);
    applyChange(i2,j2,c1);
    int pa[32], pa2[32], affN=0;
    for(int d=0;d<4;d++){
        int ni=i1+di[d], nj=j1+dj[d];
        int nb=(ni>=0&&nj>=0&&ni<n&&nj<n)?cur[ni][nj]:0;
        if(nb!=c2){pa[affN]=c2; pa2[affN]=nb; affN++;}
        if(nb!=c1){pa[affN]=c1; pa2[affN]=nb; affN++;}
    }
    for(int d=0;d<4;d++){
        int ni=i2+di[d], nj=j2+dj[d];
        int nb=(ni>=0&&nj>=0&&ni<n&&nj<n)?cur[ni][nj]:0;
        if(nb!=c1){pa[affN]=c1; pa2[affN]=nb; affN++;}
        if(nb!=c2){pa[affN]=c2; pa2[affN]=nb; affN++;}
    }
    bool ok=true;
    for(int k=0;k<affN;k++) if(!pairOk(pa[k],pa2[k])){ok=false;break;}
    if(ok){
        if(!connCheck(c1)) ok=false;
        if(ok && !connCheck(c2)) ok=false;
    }
    if(!ok){applyChange(i2,j2,c2); applyChange(i1,j1,c1); return false;}
    return true;
}

// ---- Deterministic priority-queue erosion ----
// Score lower = better to erase.
// Components:
//   artic : if removing disconnects color (large penalty)
//   sameN : number of same-color neighbors (prefer fewer)
//   uniq  : number of unique adjacencies provided (prefer fewer)
static int boundaryScore(int i, int j){
    int c = cur[i][j];
    if(c==0) return INT_MAX;
    // count same-color neighbors and unique (color -> count) adjacencies
    int sameN = 0;
    int nbColors[4]; int nbn = 0;
    for(int d=0;d<4;d++){
        int ni=i+di[d], nj=j+dj[d];
        int nb = (ni>=0&&nj>=0&&ni<n&&nj<n)?cur[ni][nj]:0;
        if(nb==c) sameN++;
        else {
            bool found=false;
            for(int k=0;k<nbn;k++) if(nbColors[k]==nb){found=true;break;}
            if(!found) nbColors[nbn++]=nb;
        }
    }
    // unique adjacency score: how many of these adjacencies are only provided by this cell
    int uniq = 0;
    for(int k=0;k<nbn;k++){
        int nb = nbColors[k];
        // count cells of color c adjacent to nb anywhere
        bool onlyHere = true;
        // quick check: total cells of c vs cells adjacent to nb of color c
        // We'll scan the grid; costly, but limited to boundary cells which is bounded
        for(int ii=0; ii<n && onlyHere; ii++){
            for(int jj=0; jj<n; jj++){
                if(cur[ii][jj]!=c) continue;
                if(ii==i && jj==j) continue;
                for(int d2=0;d2<4;d2++){
                    int xi=ii+di[d2], xj=jj+dj[d2];
                    int nnb = (xi>=0&&xj>=0&&xi<n&&xj<n)?cur[xi][xj]:0;
                    if(nnb==nb){ onlyHere=false; break; }
                }
                if(!onlyHere) break;
            }
        }
        if(onlyHere) uniq++;
    }
    // articulation check: temporarily set to 0, check connectivity of c
    // Only do cheap check: if sameN == 0 and there are other cells of c, it's disconnected already? 
    // Actually if the cell has no same-color neighbors, erasing it splits off c (if c has other cells not connected)
    // Safer: temporarily remove and check
    int tmp = cur[i][j];
    cur[i][j] = 0;
    bool art = false;
    if(cellcnt[c]>1){
        // check if remaining c cells are connected
        // Use cellcnt-like count
        int cnt=0; for(int ii=0;ii<n;ii++)for(int jj=0;jj<n;jj++) if(cur[ii][jj]==c) cnt++;
        if(cnt>0){
            // BFS
            static vector<vector<char>> vis2;
            vis2.assign(n, vector<char>(n,0));
            queue<pair<int,int>> q;
            bool started=false;
            for(int ii=0;ii<n&&!started;ii++)for(int jj=0;jj<n&&!started;jj++) if(cur[ii][jj]==c){vis2[ii][jj]=1;q.push({ii,jj});started=true;}
            int k=0;
            while(!q.empty()){auto[x,y]=q.front();q.pop();k++;
                for(int d=0;d<4;d++){int a=x+di[d],b=y+dj[d];
                    if(a>=0&&b>=0&&a<n&&b<n&&!vis2[a][b]&&cur[a][b]==c){vis2[a][b]=1;q.push({a,b});}}}
            if(k!=cnt) art=true;
        }
    }
    cur[i][j]=tmp;
    int score = 0;
    if(art) score += 1000000;
    score += uniq * 10000;
    // prefer fewer same-color neighbors (less interlocked)
    score += (4 - sameN) * 10;
    return score;
}

struct PQItem { int score; int i; int j; };
struct PQCompare { bool operator()(const PQItem&a, const PQItem&b) const { return a.score > b.score; } };

static void deterministicErosion(double deadline, chrono::steady_clock::time_point t0){
    auto timeNow=[&](){return chrono::duration<double>(chrono::steady_clock::now()-t0).count();};
    // Initialize PQ with all non-zero boundary cells
    priority_queue<PQItem, vector<PQItem>, PQCompare> pq;
    vector<vector<char>> inQueue(n, vector<char>(n,0));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        if(cur[i][j]==0) continue;
        bool isBoundary=false;
        for(int d=0;d<4;d++){
            int ni=i+di[d], nj=j+dj[d];
            int nb = (ni>=0&&nj>=0&&ni<n&&nj<n)?cur[ni][nj]:0;
            if(nb==0){isBoundary=true;break;}
        }
        if(isBoundary){
            int s = boundaryScore(i,j);
            pq.push({s,i,j});
            inQueue[i][j]=1;
        }
    }
    int steps=0;
    while(!pq.empty()){
        steps++;
        if((steps&15)==0 && timeNow() > deadline) break;
        auto [s,i,j] = pq.top(); pq.pop();
        inQueue[i][j]=0;
        if(cur[i][j]==0) continue;
        // Recompute score (state may have changed)
        int ns = boundaryScore(i,j);
        // If score is stale, push back with new score
        if(ns != s){
            // check still boundary
            bool isBoundary=false;
            for(int d=0;d<4;d++){
                int ni=i+di[d], nj=j+dj[d];
                int nb = (ni>=0&&nj>=0&&ni<n&&nj<n)?cur[ni][nj]:0;
                if(nb==0){isBoundary=true;break;}
            }
            if(isBoundary){ pq.push({ns,i,j}); inQueue[i][j]=1; }
            continue;
        }
        // Skip if not worth trying: articulation always fails
        if(s >= 1000000) continue;
        // Try to erase
        if(tryErase(i,j)){
            // Push newly adjacent non-zero cells
            for(int d=0;d<4;d++){
                int ni=i+di[d], nj=j+dj[d];
                if(ni<0||nj<0||ni>=n||nj>=n) continue;
                if(cur[ni][nj]==0) continue;
                if(inQueue[ni][nj]) continue;
                // check boundary
                bool isBoundary=false;
                for(int d2=0;d2<4;d2++){
                    int xi=ni+di[d2], xj=nj+dj[d2];
                    int nb = (xi>=0&&xj>=0&&xi<n&&xj<n)?cur[xi][xj]:0;
                    if(nb==0){isBoundary=true;break;}
                }
                if(isBoundary){
                    int sc = boundaryScore(ni,nj);
                    pq.push({sc,ni,nj});
                    inQueue[ni][nj]=1;
                }
            }
        }
    }
}

int main(int argc, char** argv){
    double tl = argc>1 ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    cin >> n >> m;
    orig.assign(n, vector<int>(n));
    for(auto&r:orig)for(auto&c:r)cin>>c;
    cur = orig;
    A0 = adjacency(orig);
    buildCnt();
    int curScore=0;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++) if(cur[i][j]!=0) curScore++;
    best = cur;
    int bestScore = curScore;
    mt19937 rng(987654321u);
    auto timeNow=[&](){return chrono::duration<double>(chrono::steady_clock::now()-t0).count();};
    double deadline = tl*0.92;
    
    // Phase 1: deterministic erosion
    double erodeDeadline = tl*0.60;
    while(timeNow() < erodeDeadline){
        deterministicErosion(erodeDeadline, t0);
        // update score
        curScore = 0;
        for(int i=0;i<n;i++)for(int j=0;j<n;j++) if(cur[i][j]!=0) curScore++;
        if(curScore < bestScore){ bestScore = curScore; best = cur; }
        if(timeNow() >= erodeDeadline) break;
        // Phase 2: bounded local search to unblock new candidates
        double lsEnd = min(erodeDeadline, timeNow() + 0.05*tl);
        int lsIter=0;
        while(timeNow() < lsEnd){
            lsIter++;
            int i = rng()%n, j = rng()%n;
            int r = rng()%100;
            if(r<50){
                if(tryRecolor(i,j)){}
            } else {
                if(cur[i][j]==0) continue;
                int d = rng()%4;
                int ni=i+di[d], nj=j+dj[d];
                if(ni<0||nj<0||ni>=n||nj>=n) continue;
                if(cur[ni][nj]==0) continue;
                trySwap(i,j,ni,nj);
            }
        }
        curScore = 0;
        for(int i=0;i<n;i++)for(int j=0;j<n;j++) if(cur[i][j]!=0) curScore++;
        if(curScore < bestScore){ bestScore = curScore; best = cur; }
        // Restore best for further erosion
        if(curScore > bestScore){
            cur = best;
            buildCnt();
            curScore = bestScore;
        }
    }
    
    // Final local search phase
    cur = best;
    buildCnt();
    curScore = bestScore;
    int iter=0;
    while(true){
        if((iter&127)==0 && timeNow()>deadline) break;
        iter++;
        int i = rng()%n, j = rng()%n;
        int r = rng()%100;
        if(r<40){
            if(tryErase(i,j)) curScore--;
        } else if(r<80){
            tryRecolor(i,j);
        } else {
            if(cur[i][j]==0) continue;
            int d = rng()%4;
            int ni=i+di[d], nj=j+dj[d];
            if(ni<0||nj<0||ni>=n||nj>=n) continue;
            if(cur[ni][nj]==0) continue;
            trySwap(i,j,ni,nj);
        }
        if(curScore < bestScore){ bestScore = curScore; best = cur; }
    }
    
    for(int i=0;i<n;i++){
        for(int j=0;j<n;j++) cout << best[i][j] << (j+1<n?" ":"\n");
    }
    return 0;
}
// EVOLVE-BLOCK-END
