// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;

static const int MAXN=55;

static const int DI4[4]={-1,1,0,0};
static const int DJ4[4]={0,0,-1,1};

int N, M;
int Cg[MAXN][MAXN];
int Df[MAXN*MAXN];
char staticEr[MAXN][MAXN];
char needBorder[130];
bitset<128> origAdjMask[130];

static char visF[MAXN*MAXN];
static char visC[MAXN*MAXN];
static int cntF[130];
static bitset<128> curAdjF[130];
static int qbuf[MAXN*MAXN];
static char hasZero[130];
static int startPos[130];

bool fullCheckFlat(int NN_, int MM_){
    int total = NN_*NN_;
    for(int c=0;c<=MM_;c++){ cntF[c]=0; curAdjF[c].reset(); hasZero[c]=0; }
    for(int k=0;k<total;k++) cntF[Df[k]]++;
    
    memset(visF, 0, total);
    int qh=0, qt=0;
    for(int i=0;i<NN_;i++){
        int j0=0, j1=NN_-1;
        if(i==0||i==NN_-1){
            for(int j=0;j<NN_;j++){
                if(Df[i*NN_+j]==0){int k=i*NN_+j; if(!visF[k]){visF[k]=1;qbuf[qt++]=k;}}
            }
        } else {
            if(Df[i*NN_+j0]==0){int k=i*NN_+j0; if(!visF[k]){visF[k]=1;qbuf[qt++]=k;}}
            if(Df[i*NN_+j1]==0){int k=i*NN_+j1; if(!visF[k]){visF[k]=1;qbuf[qt++]=k;}}
        }
    }
    while(qh<qt){
        int k=qbuf[qh++]; int i=k/NN_, j=k%NN_;
        if(i>0){int k2=k-NN_; if(Df[k2]==0&&!visF[k2]){visF[k2]=1;qbuf[qt++]=k2;}}
        if(i<NN_-1){int k2=k+NN_; if(Df[k2]==0&&!visF[k2]){visF[k2]=1;qbuf[qt++]=k2;}}
        if(j>0){int k2=k-1; if(Df[k2]==0&&!visF[k2]){visF[k2]=1;qbuf[qt++]=k2;}}
        if(j<NN_-1){int k2=k+1; if(Df[k2]==0&&!visF[k2]){visF[k2]=1;qbuf[qt++]=k2;}}
    }
    for(int k=0;k<total;k++) if(Df[k]==0&&!visF[k]) return false;
    
    for(int c=0;c<=MM_;c++) startPos[c]=-1;
    for(int k=0;k<total;k++){ int c=Df[k]; if(c>0 && startPos[c]<0) startPos[c]=k; }
    
    memset(visC, 0, total);
    for(int c=1;c<=MM_;c++){
        if(cntF[c]==0) continue;
        int start=startPos[c];
        qh=qt=0; qbuf[qt++]=start; visC[start]=1;
        int reach=1;
        while(qh<qt){
            int k=qbuf[qh++]; int i=k/NN_, j=k%NN_;
            if(i>0){int k2=k-NN_; if(Df[k2]==c&&!visC[k2]){visC[k2]=1;reach++;qbuf[qt++]=k2;}}
            if(i<NN_-1){int k2=k+NN_; if(Df[k2]==c&&!visC[k2]){visC[k2]=1;reach++;qbuf[qt++]=k2;}}
            if(j>0){int k2=k-1; if(Df[k2]==c&&!visC[k2]){visC[k2]=1;reach++;qbuf[qt++]=k2;}}
            if(j<NN_-1){int k2=k+1; if(Df[k2]==c&&!visC[k2]){visC[k2]=1;reach++;qbuf[qt++]=k2;}}
        }
        if(reach!=cntF[c]) return false;
    }
    
    for(int i=0;i<NN_;i++)for(int j=0;j<NN_;j++){
        int k=i*NN_+j; int c=Df[k]; if(c==0) continue;
        if(i==0||i==NN_-1||j==0||j==NN_-1) hasZero[c]=1;
        if(i>0){int cc=Df[k-NN_]; if(cc>0&&cc!=c){curAdjF[c].set(cc);curAdjF[cc].set(c);} else if(cc==0) hasZero[c]=1;}
        if(i<NN_-1){int cc=Df[k+NN_]; if(cc>0&&cc!=c){curAdjF[c].set(cc);curAdjF[cc].set(c);} else if(cc==0) hasZero[c]=1;}
        if(j>0){int cc=Df[k-1]; if(cc>0&&cc!=c){curAdjF[c].set(cc);curAdjF[cc].set(c);} else if(cc==0) hasZero[c]=1;}
        if(j<NN_-1){int cc=Df[k+1]; if(cc>0&&cc!=c){curAdjF[c].set(cc);curAdjF[cc].set(c);} else if(cc==0) hasZero[c]=1;}
    }
    
    for(int c=1;c<=MM_;c++){
        if(cntF[c]==0){
            if(needBorder[c]) return false;
            if(origAdjMask[c].any()) return false;
            continue;
        }
        if((bool)needBorder[c] != (bool)hasZero[c]) return false;
    }
    
    for(int c=1;c<=MM_;c++){
        for(int d=c+1;d<=MM_;d++){
            bool o = origAdjMask[c].test(d);
            bool nw = curAdjF[c].test(d);
            if(o!=nw) return false;
        }
    }
    return true;
}

void setup(){
    M=0;
    for(int i=0;i<N;i++)for(int j=0;j<N;j++) M=max(M,Cg[i][j]);
    char exp2[130]; for(int c=0;c<=M;c++) exp2[c]=0;
    for(int j=0;j<N;j++){ exp2[Cg[0][j]]=1; exp2[Cg[N-1][j]]=1; }
    for(int i=0;i<N;i++){ exp2[Cg[i][0]]=1; exp2[Cg[i][N-1]]=1; }
    exp2[0]=1;
    
    for(int i=0;i<N;i++)for(int j=0;j<N;j++){
        int c=Cg[i][j];
        staticEr[i][j]=0;
        if(!exp2[c]) continue;
        bool ok=true;
        for(int d=0;d<4;d++){
            int a=i+DI4[d],b=j+DJ4[d];
            if(a<0||a>=N||b<0||b>=N) continue;
            int cc=Cg[a][b];
            if(cc==c) continue;
            if(!exp2[cc]){ok=false;break;}
        }
        if(ok) staticEr[i][j]=1;
    }
    
    for(int c=0;c<=M;c++) needBorder[c]=0;
    for(int j=0;j<N;j++){ needBorder[Cg[0][j]]=1; needBorder[Cg[N-1][j]]=1; }
    for(int i=0;i<N;i++){ needBorder[Cg[i][0]]=1; needBorder[Cg[i][N-1]]=1; }
    
    for(int c=0;c<=M;c++) origAdjMask[c].reset();
    for(int i=0;i<N;i++)for(int j=0;j<N;j++){
        int c=Cg[i][j];
        for(int d=0;d<4;d++){
            int a=i+DI4[d],b=j+DJ4[d];
            if(a<0||a>=N||b<0||b>=N) continue;
            int cc=Cg[a][b];
            if(cc>0&&cc!=c){origAdjMask[c].set(cc);origAdjMask[cc].set(c);}
        }
    }
}

int main(int argc, char** argv){
    double tl = 2.0;
    if(argc>1) tl = atof(argv[1]);
    auto t0 = chrono::steady_clock::now();
    
    if(!(cin>>N>>M)) return 0;
    for(int i=0;i<N;i++)for(int j=0;j<N;j++) cin>>Cg[i][j];
    
    setup();
    
    for(int k=0;k<N*N;k++) Df[k] = Cg[k/N][k%N];
    
    vector<int> order;
    for(int i=0;i<N;i++)for(int j=0;j<N;j++) if(staticEr[i][j]) order.push_back(i*N+j);
    mt19937_64 rng(987654321);
    
    for(int pass=0;pass<3;pass++){
        if(pass>0) shuffle(order.begin(), order.end(), rng);
        for(int idx: order){
            if(Df[idx]==0) continue;
            int sv=Df[idx];
            Df[idx]=0;
            if(!fullCheckFlat(N,M)) Df[idx]=sv;
        }
    }
    
    int bestZeros=0;
    static int bestD[MAXN*MAXN];
    auto countZeros=[&](){int z=0;for(int k=0;k<N*N;k++)if(Df[k]==0)z++;return z;};
    bestZeros=countZeros();
    memcpy(bestD, Df, sizeof(int)*N*N);
    
    vector<int> erCells;
    for(int i=0;i<N;i++)for(int j=0;j<N;j++) if(staticEr[i][j]) erCells.push_back(i*N+j);
    
    if(!erCells.empty()){
        double T0=4.0, Tmin=0.2;
        double endT = tl*0.98;
        static int saveArr[MAXN*MAXN];
        
        while(true){
            double el = chrono::duration<double>(chrono::steady_clock::now()-t0).count();
            if(el > endT) break;
            double T = T0 * pow(Tmin/T0, el/endT);
            
            int idx = erCells[rng()%erCells.size()];
            int i=idx/N, j=idx%N;
            
            memcpy(saveArr, Df, sizeof(int)*N*N);
            int oldZ = countZeros();
            
            if(Df[idx]==0){
                Df[idx] = Cg[i][j];
                if(!fullCheckFlat(N,M)){
                    memcpy(Df, saveArr, sizeof(int)*N*N);
                    continue;
                }
            } else {
                int sv=Df[idx];
                Df[idx]=0;
                if(!fullCheckFlat(N,M)){
                    Df[idx]=sv;
                    continue;
                }
            }
            
            vector<int> allcand;
            allcand.push_back(idx);
            for(int d=0;d<4;d++){
                int a=i+DI4[d], b=j+DJ4[d];
                if(a<0||a>=N||b<0||b>=N) continue;
                int k=a*N+b;
                if(staticEr[a][b]) allcand.push_back(k);
            }
            shuffle(allcand.begin(), allcand.end(), rng);
            for(int k: allcand){
                if(Df[k]==0) continue;
                int sv2 = Df[k];
                Df[k]=0;
                if(!fullCheckFlat(N,M)) Df[k]=sv2;
            }
            
            int newZ = countZeros();
            if(newZ >= oldZ){
                if(newZ > bestZeros){ bestZeros=newZ; memcpy(bestD, Df, sizeof(int)*N*N); }
            } else {
                double prob = exp((double)(newZ-oldZ)/T);
                if((double)rng()/(double)UINT64_MAX < prob){ /* accept */ }
                else memcpy(Df, saveArr, sizeof(int)*N*N);
            }
        }
    }
    
    memcpy(Df, bestD, sizeof(int)*N*N);
    for(int i=0;i<N;i++){
        for(int j=0;j<N;j++){
            if(j) cout << ' ';
            cout << Df[i*N+j];
        }
        cout << '\n';
    }
    return 0;
}
// EVOLVE-BLOCK-END
