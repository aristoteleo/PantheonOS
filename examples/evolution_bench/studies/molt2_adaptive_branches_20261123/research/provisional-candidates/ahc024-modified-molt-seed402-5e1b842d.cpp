// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
using Clock = chrono::steady_clock;

static int n, m;
static int curG[55][55], saveG[55][55], bestG[55][55];
static bool A0[110][110];
static int adjCnt[110][110];
static int cnt[110], saveCnt[110];
static const int di[4]={-1,1,0,0}, dj[4]={0,0,-1,1};

static int visT[55][55], visTok=0;
static int stk[3000][2];
static int zq[3000][2];

static Clock::time_point T0;
static double TL;
static inline double elapsed(){ return chrono::duration<double>(Clock::now()-T0).count(); }

static void fullRecountAdj(){
    memset(adjCnt,0,sizeof(adjCnt));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        int c=curG[i][j];
        for(int d=0;d<4;d++){
            int a=i+di[d], b=j+dj[d];
            int nc;
            if(a<0||b<0||a>=n||b>=n) nc=0; else nc=curG[a][b];
            if(nc!=c){ adjCnt[c][nc]++; adjCnt[nc][c]++; }
        }
    }
}

static bool colorConnected(int c){
    if(cnt[c]==0) return true;
    int si=-1,sj=-1;
    for(int i=0;i<n&&si<0;i++)for(int j=0;j<n;j++)if(curG[i][j]==c){si=i;sj=j;break;}
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
            if(a>=0&&b>=0&&a<n&&b<n&&visT[a][b]!=visTok&&curG[a][b]==c){
                visT[a][b]=visTok; stk[top][0]=a; stk[top][1]=b; top++;
            }
        }
    }
    return cc==cnt[c];
}

static bool checkZeroConnected(){
    if(cnt[0]==0) return true;
    ++visTok;
    int qh=0, qt=0;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        if(curG[i][j]==0 && (i==0||i==n-1||j==0||j==n-1)){
            if(visT[i][j]!=visTok){
                visT[i][j]=visTok; zq[qt][0]=i; zq[qt][1]=j; qt++;
            }
        }
    }
    int cc=0;
    while(qh<qt){
        int x=zq[qh][0], y=zq[qh][1]; qh++; cc++;
        for(int d=0;d<4;d++){
            int a=x+di[d], b=y+dj[d];
            if(a>=0&&b>=0&&a<n&&b<n&&visT[a][b]!=visTok&&curG[a][b]==0){
                visT[a][b]=visTok; zq[qt][0]=a; zq[qt][1]=b; qt++;
            }
        }
    }
    return cc==cnt[0];
}

static bool tryMoveCommit(int i,int j,int newColor){
    int oldColor = curG[i][j];
    if(oldColor==newColor) return false;
    if(newColor==0){
        if(oldColor==0) return false;
        if(cnt[oldColor]<=1) return false;
    } else {
        if(cnt[newColor]==0) return false;
        bool hasNbr=false;
        for(int d=0;d<4;d++){
            int a=i+di[d], b=j+dj[d];
            if(a<0||b<0||a>=n||b>=n) continue;
            if(curG[a][b]==newColor){hasNbr=true;break;}
        }
        if(!hasNbr) return false;
        if(oldColor!=0 && cnt[oldColor]<=1) return false;
    }
    
    memcpy(saveG, curG, sizeof(curG));
    memcpy(saveCnt, cnt, sizeof(cnt));
    
    curG[i][j]=newColor;
    cnt[oldColor]--;
    cnt[newColor]++;
    
    bool ok = true;
    if(oldColor!=0 && !colorConnected(oldColor)) ok=false;
    if(ok && (oldColor==0 || newColor==0) && !checkZeroConnected()) ok=false;
    if(ok){
        fullRecountAdj();
        int cc[3]={oldColor, newColor, 0};
        for(int xc : cc){
            for(int y=0;y<=m;y++){
                if(y==xc) continue;
                if(A0[xc][y] != (adjCnt[xc][y]>0)){ ok=false; break; }
            }
            if(!ok) break;
        }
    }
    
    if(!ok){
        memcpy(curG, saveG, sizeof(curG));
        memcpy(cnt, saveCnt, sizeof(cnt));
        fullRecountAdj();
        return false;
    }
    return true;
}

int main(int argc, char**argv){
    TL = argc>1 ? atof(argv[1]) : 2.0;
    T0 = Clock::now();
    if(scanf("%d %d",&n,&m)!=2) return 0;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){ scanf("%d",&curG[i][j]); }
    
    memset(A0,0,sizeof(A0));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        int c1=curG[i][j];
        if(j+1<n){ int c2=curG[i][j+1]; if(c1!=c2){A0[c1][c2]=A0[c2][c1]=true;} }
        if(i+1<n){ int c2=curG[i+1][j]; if(c1!=c2){A0[c1][c2]=A0[c2][c1]=true;} }
        if(c1!=0 && (i==0||i==n-1||j==0||j==n-1)){ A0[0][c1]=A0[c1][0]=true; }
    }
    
    memset(cnt,0,sizeof(cnt));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++) cnt[curG[i][j]]++;
    fullRecountAdj();
    
    mt19937 rng(987654321);
    
    // Greedy erasure
    {
        vector<pair<int,int>> cells;
        for(int i=0;i<n;i++)for(int j=0;j<n;j++) cells.push_back({i,j});
        shuffle(cells.begin(),cells.end(),rng);
        double tlimit = TL*0.15;
        bool changed=true;
        while(changed && elapsed()<tlimit){
            changed=false;
            for(auto&pr:cells){
                if(elapsed()>=tlimit) break;
                int i=pr.first, j=pr.second;
                if(curG[i][j]==0) continue;
                if(tryMoveCommit(i,j,0)) changed=true;
            }
        }
    }
    
    memcpy(bestG, curG, sizeof(curG));
    int bestZeros = cnt[0];
    
    double T_start = 0.9;
    double T_end = 0.005;
    double phase_end = TL*0.95;
    
    while(elapsed()<phase_end){
        double prog = elapsed()/phase_end;
        if(prog>1.0) prog=1.0;
        double T = T_start*pow(T_end/T_start,prog);
        
        int i = (int)(rng()%n);
        int j = (int)(rng()%n);
        int oldColor = curG[i][j];
        
        int choices[8];
        int k=0;
        for(int d=0;d<4;d++){
            int a=i+di[d], b=j+dj[d];
            int nc;
            if(a<0||b<0||a>=n||b>=n) nc=0;
            else nc=curG[a][b];
            if(nc==oldColor) continue;
            bool dup=false;
            for(int x=0;x<k;x++) if(choices[x]==nc){dup=true;break;}
            if(!dup && k<8) choices[k++]=nc;
        }
        if(k==0) continue;
        int newColor = choices[rng()%k];
        
        int zerosBefore = cnt[0];
        if(tryMoveCommit(i,j,newColor)){
            int zerosAfter = cnt[0];
            int delta = zerosAfter-zerosBefore;
            bool accept;
            if(delta>=0) accept=true;
            else accept = ((double)(rng()%1000000)/1000000.0) < exp((double)delta/T);
            if(!accept){
                memcpy(curG, saveG, sizeof(curG));
                memcpy(cnt, saveCnt, sizeof(cnt));
                fullRecountAdj();
            } else {
                if(zerosAfter > bestZeros){
                    bestZeros = zerosAfter;
                    memcpy(bestG, curG, sizeof(curG));
                }
            }
        }
    }
    
    {
        string out;
        out.reserve((size_t)n*n*4);
        for(int i=0;i<n;i++){
            for(int j=0;j<n;j++){
                out += to_string(bestG[i][j]);
                out += (j+1<n?' ':'\n');
            }
        }
        fputs(out.c_str(), stdout);
    }
    return 0;
}
// EVOLVE-BLOCK-END
