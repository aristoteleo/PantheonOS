// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
using Clock = chrono::steady_clock;

static int n, m;
static vector<vector<int>> orig, cur, bestg;
static bool A0[110][110];
static int adjCnt[110][110];
static int cnt[110];
static const int di[4]={-1,1,0,0}, dj[4]={0,0,-1,1};

static int visT[55][55], visTok=0;
static int stk[3000][2];

static Clock::time_point T0;
static double TL;
static inline double elapsed(){ return chrono::duration<double>(Clock::now()-T0).count(); }

static void recountAdj(int c){
    for (int x=0; x<=m; x++){ adjCnt[c][x]=0; adjCnt[x][c]=0; }
    for (int i=0;i<n;i++) for(int j=0;j<n;j++){
        if (cur[i][j]!=c) continue;
        for (int d=0; d<4; d++){
            int a=i+di[d], b=j+dj[d];
            int nc;
            if (a<0||b<0||a>=n||b>=n) nc=0; else nc=cur[a][b];
            if (nc!=c) adjCnt[c][nc]++;
        }
    }
}

static bool colorConnected(int c){
    if (cnt[c]==0) return true;
    int si=-1, sj=-1;
    for (int i=0;i<n && si<0;i++) for(int j=0;j<n;j++) if(cur[i][j]==c){ si=i; sj=j; break; }
    ++visTok;
    int top=0;
    stk[top][0]=si; stk[top][1]=sj; top++;
    visT[si][sj]=visTok;
    int cc=0;
    while(top){
        --top;
        int x=stk[top][0], y=stk[top][1]; cc++;
        for(int d=0;d<4;d++){
            int a=x+di[d], b=y+dj[d];
            if(a>=0&&b>=0&&a<n&&b<n&&visT[a][b]!=visTok&&cur[a][b]==c){
                visT[a][b]=visTok; stk[top][0]=a; stk[top][1]=b; top++;
            }
        }
    }
    return cc==cnt[c];
}

static bool checkZeroConnected(){
    if (cnt[0]==0) return true;
    ++visTok;
    int top=0;
    for (int i=0;i<n;i++) for (int j=0;j<n;j++){
        if ((i==0||i==n-1||j==0||j==n-1) && cur[i][j]==0 && visT[i][j]!=visTok){
            visT[i][j]=visTok; stk[top][0]=i; stk[top][1]=j; top++;
        }
    }
    if (top==0){
        for (int i=0;i<n && top==0;i++) for(int j=0;j<n;j++){
            if (cur[i][j]==0){ visT[i][j]=visTok; stk[top][0]=i; stk[top][1]=j; top++; break; }
        }
    }
    int cc=0;
    while(top){
        --top;
        int x=stk[top][0], y=stk[top][1]; cc++;
        for(int d=0;d<4;d++){
            int a=x+di[d], b=y+dj[d];
            if(a>=0&&b>=0&&a<n&&b<n&&visT[a][b]!=visTok&&cur[a][b]==0){
                visT[a][b]=visTok; stk[top][0]=a; stk[top][1]=b; top++;
            }
        }
    }
    return cc==cnt[0];
}

// Attempt move. Returns true on success (state changed to newColor). 
static bool tryMove(int i, int j, int newColor){
    int oldColor = cur[i][j];
    if (oldColor==newColor) return false;
    if (newColor==0){
        if (oldColor==0) return false;
        if (cnt[oldColor]<=1) return false;
    } else {
        if (cnt[newColor]==0) return false;
        bool hasNbr=false;
        for (int d=0;d<4;d++){
            int a=i+di[d], b=j+dj[d];
            if (a<0||b<0||a>=n||b>=n) continue;
            if (cur[a][b]==newColor){ hasNbr=true; break; }
        }
        if (!hasNbr) return false;
        if (oldColor!=0 && cnt[oldColor]<=1) return false;
    }
    
    // apply tentatively
    cur[i][j]=newColor;
    cnt[oldColor]--;
    cnt[newColor]++;
    
    bool ok = true;
    if (oldColor != 0 && cnt[oldColor] > 0 && !colorConnected(oldColor)) ok = false;
    if (ok && oldColor == 0 && !checkZeroConnected()) ok = false;
    
    if (ok){
        recountAdj(oldColor);
        if (newColor!=oldColor) recountAdj(newColor);
        for (int c=0;c<=m && ok;c++){
            if (c != oldColor && A0[oldColor][c] != (adjCnt[oldColor][c] > 0)) ok = false;
            if (c != newColor && A0[newColor][c] != (adjCnt[newColor][c] > 0)) ok = false;
        }
    }
    
    if (!ok){
        // revert
        cur[i][j]=oldColor;
        cnt[oldColor]++;
        cnt[newColor]--;
        recountAdj(oldColor);
        if (newColor!=oldColor) recountAdj(newColor);
        return false;
    }
    return true;
}

int main(int argc, char**argv){
    TL = argc>1 ? atof(argv[1]) : 2.0;
    T0 = Clock::now();
    if (scanf("%d %d", &n, &m) != 2) return 0;
    orig.assign(n, vector<int>(n));
    for (int i=0;i<n;i++) for(int j=0;j<n;j++){ int x; scanf("%d",&x); orig[i][j]=x; }
    
    memset(A0, 0, sizeof(A0));
    for (int i=0;i<n;i++) for(int j=0;j<n;j++){
        int c1 = orig[i][j];
        if (j+1<n){ int c2=orig[i][j+1]; if(c1!=c2){ A0[c1][c2]=A0[c2][c1]=true; } }
        if (i+1<n){ int c2=orig[i+1][j]; if(c1!=c2){ A0[c1][c2]=A0[c2][c1]=true; } }
        if (i==0||i==n-1||j==0||j==n-1){ A0[0][c1]=A0[c1][0]=true; }
    }
    
    cur = orig;
    memset(cnt, 0, sizeof(cnt));
    for (int i=0;i<n;i++) for(int j=0;j<n;j++) cnt[orig[i][j]]++;
    memset(adjCnt, 0, sizeof(adjCnt));
    for (int c=0;c<=m;c++) recountAdj(c);
    
    mt19937 rng(987654321);
    
    // Greedy erasure phase (initial)
    {
        vector<pair<int,int>> cells;
        for (int i=0;i<n;i++) for(int j=0;j<n;j++) cells.push_back({i,j});
        shuffle(cells.begin(), cells.end(), rng);
        double tlimit = TL * 0.30;
        bool changed = true;
        while (changed && elapsed() < tlimit){
            changed = false;
            for (auto&pr : cells){
                if (elapsed() >= tlimit) break;
                int i=pr.first, j=pr.second;
                if (cur[i][j]==0) continue;
                if (tryMove(i,j,0)) changed = true;
            }
        }
    }
    
    bestg = cur;
    int bestZeros = cnt[0];
    
    double T_start = 0.9;
    double T_end = 0.01;
    double phase_end = TL * 0.98;
    
    long long iters = 0;
    while (elapsed() < phase_end){
        double prog = elapsed() / phase_end;
        if (prog > 1.0) prog = 1.0;
        double T = T_start * pow(T_end/T_start, prog);
        
        int i = (int)(rng() % n);
        int j = (int)(rng() % n);
        int oldColor = cur[i][j];
        
        int choices[6];
        int k = 0;
        for (int d=0;d<4;d++){
            int a=i+di[d], b=j+dj[d];
            if (a<0||b<0||a>=n||b>=n) continue;
            int nc = cur[a][b];
            if (nc == oldColor) continue;
            bool dup=false;
            for (int x=0;x<k;x++) if (choices[x]==nc){dup=true;break;}
            if (!dup && k<6) choices[k++]=nc;
        }
        // Also consider 0 as candidate if adjacent to existing 0 (or oldColor==0 won't apply)
        if (oldColor != 0){
            bool adjZero = false;
            for (int d=0;d<4;d++){
                int a=i+di[d], b=j+dj[d];
                if (a<0||b<0||a>=n||b>=n){ adjZero=true; break; }
                if (cur[a][b]==0){ adjZero=true; break; }
            }
            if (adjZero){
                bool hasZero=false;
                for (int x=0;x<k;x++) if (choices[x]==0){hasZero=true;break;}
                if (!hasZero && k<6) choices[k++]=0;
            }
        }
        if (k==0) continue;
        int newColor = choices[rng()%k];
        
        int zerosBefore = cnt[0];
        if (tryMove(i,j,newColor)){
            int zerosAfter = cnt[0];
            int delta = zerosAfter - zerosBefore;
            bool accept;
            if (delta >= 0) accept = true;
            else accept = ((double)(rng()%1000000)/1000000.0) < exp((double)delta / T);
            if (accept){
                if (zerosAfter > bestZeros){
                    bestZeros = zerosAfter;
                    bestg = cur;
                }
            } else {
                // revert
                tryMove(i,j,oldColor);
            }
        }
        iters++;
    }
    
    // Output bestg
    {
        string out;
        out.reserve((size_t)n*n*4);
        for (int i=0;i<n;i++){
            for(int j=0;j<n;j++){
                out += to_string(bestg[i][j]);
                out += (j+1<n?' ':'\n');
            }
        }
        fputs(out.c_str(), stdout);
    }
    return 0;
}
// EVOLVE-BLOCK-END
