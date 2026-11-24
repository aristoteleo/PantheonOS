// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;

static int n, m;
static vector<vector<int>> orig, cur, bestMap;
static const int di[4]={-1,1,0,0};
static const int dj[4]={0,0,-1,1};
static int adjcnt[105][105];
static int origpres[105][105];
static int cellcnt[105];
static int sameNbrCache[50][50];

static inline int CUR(int i,int j){ if((unsigned)i>=(unsigned)n||(unsigned)j>=(unsigned)n) return 0; return cur[i][j]; }
static inline bool inGrid(int i,int j){ return (unsigned)i<(unsigned)n && (unsigned)j<(unsigned)n; }

static double elapsed(chrono::steady_clock::time_point t0){
    return chrono::duration<double>(chrono::steady_clock::now()-t0).count();
}

// Fast connectivity check for color c using preallocated buffers
static vector<vector<char>> visBuf;
static vector<pair<int,int>> qBuf;

static bool colorConnected(int c, int si, int sj, int needCount){
    if(needCount<=1) return true;
    for(int i=0;i<n;i++) fill(visBuf[i].begin(), visBuf[i].end(), 0);
    qBuf.clear();
    qBuf.push_back({si,sj}); visBuf[si][sj]=1; int k=1;
    size_t head=0;
    while(head<qBuf.size()){
        int x=qBuf[head].first, y=qBuf[head].second; head++;
        for(int d=0;d<4;d++){
            int a=x+di[d], b=y+dj[d];
            if(inGrid(a,b)&&!visBuf[a][b]&&cur[a][b]==c){
                visBuf[a][b]=1; qBuf.push_back({a,b}); k++;
            }
        }
    }
    return k>=needCount;
}

// Check that 0-cells remain connected to border (outside). Single 0-cell adjacent to a border-connected 0 or on border is OK.
static bool zeroConnected(){
    if(cellcnt[0]==0) return true;
    for(int i=0;i<n;i++) fill(visBuf[i].begin(), visBuf[i].end(), 0);
    qBuf.clear();
    int z=0;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        if(cur[i][j]==0 && (i==0||j==0||i==n-1||j==n-1)){ if(!visBuf[i][j]){visBuf[i][j]=1; qBuf.push_back({i,j}); z++;} }
    }
    size_t head=0;
    while(head<qBuf.size()){
        int x=qBuf[head].first, y=qBuf[head].second; head++;
        for(int d=0;d<4;d++){
            int a=x+di[d], b=y+dj[d];
            if(inGrid(a,b)&&!visBuf[a][b]&&cur[a][b]==0){
                visBuf[a][b]=1; qBuf.push_back({a,b}); z++;
            }
        }
    }
    return z==cellcnt[0];
}

static inline int sameNbrsCount(int i,int j){
    int c=cur[i][j], k=0;
    for(int d=0;d<4;d++) if(CUR(i+di[d],j+dj[d])==c) k++;
    return k;
}

static bool tryErase(int i,int j){
    int c=cur[i][j];
    if(c==0) return false;
    if(cellcnt[c]==1) return false;
    int nb[4];
    for(int d=0;d<4;d++) nb[d]=CUR(i+di[d], j+dj[d]);
    int dcnt[105]; memset(dcnt,0,sizeof(int)*(m+1));
    bool hasZero=false, isBorder=false;
    if(i==0||j==0||i==n-1||j==n-1) isBorder=true;
    int sameNbrs=0;
    for(int d=0;d<4;d++){
        if(nb[d]!=c){ dcnt[nb[d]]++; if(nb[d]==0) hasZero=true; }
        else sameNbrs++;
    }
    for(int d=0; d<=m; d++){
        if(dcnt[d]==0) continue;
        if(origpres[c][d] && adjcnt[c][d] - dcnt[d] <= 0) return false;
    }
    for(int d=0;d<4;d++){
        int nd=nb[d];
        if(nd==0||nd==c) continue;
        if(!origpres[0][nd]) return false;
    }
    // If new 0 cell is not connected to border-side zero and c not orig-adjacent to 0, illegal.
    if(!hasZero && !isBorder && !origpres[0][c]){
        // Unless this cell is fully surrounded by c-only (no remaining c becoming adjacent to 0 is fine if none remain).
        // But sameNbrs>0 means remaining c cells become adjacent to this new 0.
        if(sameNbrs>0 || cellcnt[c]>1) return false;
    }

    // Apply
    for(int d=0;d<4;d++){
        int nd=nb[d];
        if(nd!=c){ adjcnt[c][nd]--; adjcnt[nd][c]--; }
        if(nd!=0){ adjcnt[0][nd]++; adjcnt[nd][0]++; }
    }
    cur[i][j]=0; cellcnt[c]--; cellcnt[0]++;

    bool ok = true;
    if(sameNbrs>1 && cellcnt[c]>0){
        int si=-1,sj=-1;
        for(int d=0;d<4&&si<0;d++){
            int a=i+di[d], b=j+dj[d];
            if(inGrid(a,b) && cur[a][b]==c){ si=a; sj=b; }
        }
        if(si>=0 && !colorConnected(c, si, sj, cellcnt[c])) ok=false;
    }
    if(ok && !hasZero && !isBorder && !origpres[0][c]){
        // New 0-cell would be isolated (no adjacency to existing 0). Illegal.
        ok=false;
    }
    if(ok && hasZero){
        // Need to confirm this erase doesn't disconnect 0. If new cell connects to existing 0, and old 0 was
        // connected, we're fine only if there was already only ONE 0-component. That's guaranteed by invariant.
    }
    if(ok && !zeroConnected()) ok = false;
    if(!ok){
        cur[i][j]=c; cellcnt[c]++; cellcnt[0]--;
        for(int d=0;d<4;d++){
            int nd=nb[d];
            if(nd!=c){ adjcnt[c][nd]++; adjcnt[nd][c]++; }
            if(nd!=0){ adjcnt[0][nd]--; adjcnt[nd][0]++; }
        }
        return false;
    }
    return true;
}

static void recomputeAdj(){
    memset(adjcnt,0,sizeof(adjcnt));
    memset(cellcnt,0,sizeof(cellcnt));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++) cellcnt[cur[i][j]]++;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        int u=cur[i][j];
        for(int d=0;d<4;d++){
            int a=i+di[d], b=j+dj[d];
            if(a<0||b<0||a>=n||b>=n){ adjcnt[u][0]++; adjcnt[0][u]++; }
            else { int v=cur[a][b]; if(u<v){ adjcnt[u][v]++; adjcnt[v][u]++; } }
        }
    }
}

static void resetAll(){
    memset(origpres,0,sizeof(origpres));
    cur=orig;
    recomputeAdj();
    for(int x=0;x<=m;x++)for(int y=0;y<=m;y++) origpres[x][y]=(adjcnt[x][y]>0)?1:0;
}

static int countZeros(){
    int z=0;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++) if(cur[i][j]==0) z++;
    return z;
}

// Priority: leaf-first (cells with few same-color neighbors) plus zero-adjacency bonus, border bonus.
static void runPass(double absDeadline, chrono::steady_clock::time_point t0, int mode){
    bool changed=true;
    int guard=0;
    while(changed){
        changed=false;
        if(++guard>4000) break;
        if(elapsed(t0) > absDeadline) break;
        vector<pair<int,int>> cells;
        cells.reserve((size_t)n*n);
        for(int i=0;i<n;i++)for(int j=0;j<n;j++) if(cur[i][j]!=0) cells.push_back({i,j});
        vector<long long> pr(cells.size());
        for(size_t k=0;k<cells.size();k++){
            int i=cells[k].first, j=cells[k].second, c=cur[i][j];
            int sn=0, zn=0;
            for(int d=0;d<4;d++){
                int nd=CUR(i+di[d],j+dj[d]);
                if(nd==c) sn++; else if(nd==0) zn++;
            }
            int isB = (i==0||j==0||i==n-1||j==n-1)?1:0;
            int dist = min(min(i,j), min(n-1-i, n-1-j));
            long long p=0;
            if(mode==0) p = (long long)sn*1000 + dist*10 + i*50 + j;
            else if(mode==1) p = -(long long)(zn*64 + sn*16 + isB*8) + (i*50+j)%7;
            else if(mode==2) p = (long long)sn*1000 - dist*10 + (i*50+j)%3;
            else if(mode==3) p = -(long long)sn*100 - zn*40 + dist;
            else if(mode==4) p = (long long)sn*500 + dist*30 + ((i*131+j*17)%97);
            else p = -(long long)(zn*100 + (4-sn)*30) + ((i*7+j*11)%5);
            pr[k]=p;
        }
        vector<int> idx(cells.size()); iota(idx.begin(),idx.end(),0);
        stable_sort(idx.begin(), idx.end(), [&](int a,int b){ return pr[a]<pr[b]; });
        for(int k:idx){
            auto&p=cells[k];
            if(cur[p.first][p.second]==0) continue;
            if(elapsed(t0) > absDeadline) break;
            if(tryErase(p.first,p.second)) changed=true;
        }
    }
}

int main(int argc,char**argv){
    double tl = argc>1? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    ios::sync_with_stdio(false); cin.tie(nullptr);
    cin>>n>>m;
    orig.assign(n, vector<int>(n));
    for(auto&r:orig) for(auto&x:r) cin>>x;

    visBuf.assign(n, vector<char>(n,0));
    qBuf.reserve(n*n);

    double hardDeadline = tl*0.95;
    int bestZeros=-1;
    unsigned seed = 20240924u;

    // Strategy sweep: 6 modes
    int modes[] = {0,1,2,3,4,5};
    int nM = 6;
    double sweepEnd = min(tl*0.55, hardDeadline);
    for(int s=0;s<nM;s++){
        if(elapsed(t0) >= sweepEnd) break;
        double now = elapsed(t0);
        double slice = (sweepEnd - now) / (double)(nM - s);
        resetAll();
        runPass(now + slice, t0, modes[s]);
        int z=countZeros(); if(z>bestZeros){bestZeros=z; bestMap=cur;}
    }

    // Random restarts
    mt19937 rng(seed);
    while(elapsed(t0) < hardDeadline){
        double now = elapsed(t0);
        double slice = min(0.06, hardDeadline - now);
        if(slice < 0.005) break;
        resetAll();
        int mode = (int)(rng()%6);
        runPass(now + slice, t0, mode);
        int z=countZeros(); if(z>bestZeros){bestZeros=z; bestMap=cur;}
    }

    if(bestMap.empty()) bestMap=orig;

    ostringstream out;
    for(int i=0;i<n;i++){
        for(int j=0;j<n;j++){
            if(j) out<<' ';
            out<<bestMap[i][j];
        }
        out<<'\n';
    }
    cout<<out.str();
    return 0;
}
// EVOLVE-BLOCK-END
