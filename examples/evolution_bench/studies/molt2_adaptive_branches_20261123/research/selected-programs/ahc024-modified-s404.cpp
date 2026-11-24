// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;

static int N, M;
static const int di[4]={-1,1,0,0}, dj[4]={0,0,-1,1};

int main(int argc,char**argv){
    double tl = argc>1?atof(argv[1]):2.0;
    auto t_start=chrono::steady_clock::now();
    ios::sync_with_stdio(false); cin.tie(nullptr);
    cin>>N>>M;
    int SZ=M+1;
    vector<vector<int>> orig(N, vector<int>(N));
    for(auto&r:orig)for(auto&c:r)cin>>c;

    int PN=N+2;
    vector<vector<int>> P(PN, vector<int>(PN,0));

    vector<vector<char>> origAdj(SZ, vector<char>(SZ,0));
    {
        vector<vector<int>> P0(PN, vector<int>(PN,0));
        for(int i=0;i<N;i++)for(int j=0;j<N;j++) P0[i+1][j+1]=orig[i][j];
        for(int i=0;i<PN;i++)for(int j=0;j<PN;j++){
            int a=P0[i][j];
            if(j+1<PN){int b=P0[i][j+1]; if(a!=b){int x=min(a,b),y=max(a,b);origAdj[x][y]=1;}}
            if(i+1<PN){int b=P0[i+1][j]; if(a!=b){int x=min(a,b),y=max(a,b);origAdj[x][y]=1;}}
        }
    }

    vector<vector<int>> cnt(SZ, vector<int>(SZ,0));
    auto edgeAdd=[&](int a,int b){ if(a==b)return; int x=min(a,b),y=max(a,b); cnt[x][y]++; };
    auto edgeSub=[&](int a,int b){ if(a==b)return; int x=min(a,b),y=max(a,b); cnt[x][y]--; };

    vector<vector<pair<int,int>>> cl(SZ);
    auto rebuildCL=[&](){
        for(auto&v:cl) v.clear();
        for(int i=0;i<N;i++)for(int j=0;j<N;j++) cl[P[i+1][j+1]].push_back({i,j});
    };

    vector<int> mark(N*N,0);
    int markId=0;
    auto cConnected=[&](int c,int ei,int ej)->bool{
        int total=0, si=-1,sj=-1;
        for(auto&p:cl[c]){
            int x=p.first,y=p.second;
            if(P[x+1][y+1]!=c) continue;
            if(x==ei&&y==ej) continue;
            total++;
            if(si<0){si=x;sj=y;}
        }
        if(total<=1) return true;
        markId++;
        vector<pair<int,int>> stk;
        stk.push_back({si,sj});
        mark[si*N+sj]=markId;
        int seen=0;
        while(!stk.empty()){
            auto [x,y]=stk.back(); stk.pop_back(); seen++;
            for(int d=0;d<4;d++){
                int a=x+di[d],b=y+dj[d];
                if(a>=0&&b>=0&&a<N&&b<N && P[a+1][b+1]==c && !(a==ei&&b==ej) && mark[a*N+b]!=markId){
                    mark[a*N+b]=markId; stk.push_back({a,b});
                }
            }
        }
        return seen==total;
    };

    auto resetToOriginal=[&](){
        for(int i=0;i<PN;i++)for(int j=0;j<PN;j++) P[i][j]=0;
        for(int i=0;i<N;i++)for(int j=0;j<N;j++) P[i+1][j+1]=orig[i][j];
        for(int a=0;a<SZ;a++)for(int b=0;b<SZ;b++) cnt[a][b]=0;
        for(int i=0;i<PN;i++)for(int j=0;j<PN;j++){
            int a=P[i][j];
            if(j+1<PN){int b=P[i][j+1]; if(a!=b) edgeAdd(a,b);}
            if(i+1<PN){int b=P[i+1][j]; if(a!=b) edgeAdd(a,b);}
        }
        rebuildCL();
    };

    // exact global validity check for current P
    auto globallyValid=[&]()->bool{
        vector<vector<char>> curAdj(SZ, vector<char>(SZ,0));
        for(int i=0;i<PN;i++)for(int j=0;j<PN;j++){
            int a=P[i][j];
            if(j+1<PN){int b=P[i][j+1]; if(a!=b){int x=min(a,b),y=max(a,b);curAdj[x][y]=1;}}
            if(i+1<PN){int b=P[i+1][j]; if(a!=b){int x=min(a,b),y=max(a,b);curAdj[x][y]=1;}}
        }
        for(int a=0;a<SZ;a++)for(int b=a+1;b<SZ;b++){
            if(curAdj[a][b]!=origAdj[a][b]) return false;
        }
        // connectivity of each color (0 handled implicitly through border; still check nonzero)
        for(int c=1;c<SZ;c++){
            int si=-1,sj=-1,total=0;
            for(int i=0;i<N;i++)for(int j=0;j<N;j++) if(P[i+1][j+1]==c){ if(si<0){si=i;sj=j;} total++; }
            if(total==0) return false;
            vector<vector<char>> vis(N, vector<char>(N,0));
            vector<pair<int,int>> stk; stk.push_back({si,sj}); vis[si][sj]=1;
            int seen=0;
            while(!stk.empty()){
                auto [x,y]=stk.back(); stk.pop_back(); seen++;
                for(int d=0;d<4;d++){
                    int a=x+di[d],b=y+dj[d];
                    if(a>=0&&b>=0&&a<N&&b<N&&!vis[a][b]&&P[a+1][b+1]==c){vis[a][b]=1;stk.push_back({a,b});}
                }
            }
            if(seen!=total) return false;
        }
        return true;
    };

    auto countZeros=[&]()->int{
        int z=0;
        for(int i=1;i<=N;i++)for(int j=1;j<=N;j++) if(P[i][j]==0) z++;
        return z;
    };

    // We'll build a good starting map using erase-only sweep (like target), then improve by SA.
    vector<vector<int>> bestMap(N, vector<int>(N));
    int bestZeros=-1;

    resetToOriginal();

    // quick greedy erase-only sweep for a solid starting point
    {
        bool changed=true;
        while(changed){
            double el=chrono::duration<double>(chrono::steady_clock::now()-t_start).count();
            if(el>tl*0.35) break;
            changed=false;
            rebuildCL();
            vector<array<int,3>> cands;
            for(int i=0;i<N;i++)for(int j=0;j<N;j++){
                if(P[i+1][j+1]==0) continue;
                int pi=i+1,pj=j+1;
                bool touch0=false;
                for(int d=0;d<4;d++){ if(P[pi+di[d]][pj+dj[d]]==0){touch0=true;break;} }
                if(!touch0) continue;
                cands.push_back({0,i,j});
            }
            for(auto&ca:cands){
                int i=ca[1],j=ca[2];
                int pi=i+1,pj=j+1;
                int c=P[pi][pj];
                set<int> nc;
                for(int d=0;d<4;d++){ int x=P[pi+di[d]][pj+dj[d]]; if(x!=c) nc.insert(x); }
                ca[0]=-(int)nc.size();
            }
            sort(cands.begin(),cands.end());
            for(auto&ca:cands){
                int i=ca[1],j=ca[2];
                if(P[i+1][j+1]==0) continue;
                int c=P[i+1][j+1];
                int pi=i+1,pj=j+1;
                int nb[4][2]={{pi-1,pj},{pi+1,pj},{pi,pj-1},{pi,pj+1}};
                bool touch0=false;
                for(auto&x:nb){ if(P[x[0]][x[1]]==0){touch0=true;break;} }
                if(!touch0) continue;
                if(!cConnected(c,i,j)) continue;
                for(auto&x:nb){ int d=P[x[0]][x[1]]; if(c!=d) edgeSub(c,d); }
                P[pi][pj]=0;
                for(auto&x:nb){ int d=P[x[0]][x[1]]; if(d!=0) edgeAdd(0,d); }
                bool ok=true;
                for(int d=0;d<SZ&&ok;d++){
                    if(d==c) continue;
                    int a=min(c,d),b=max(c,d);
                    bool has=cnt[a][b]>0;
                    if(has!=(bool)origAdj[a][b]){ ok=false; break; }
                }
                if(ok){
                    for(int d=1;d<SZ&&ok;d++){
                        int a=0,b=d;
                        bool has=cnt[a][b]>0;
                        if(has!=(bool)origAdj[a][b]){ ok=false; break; }
                    }
                }
                if(ok){
                    bool any=false;
                    for(auto&p:cl[c]){
                        int x=p.first,y=p.second;
                        if(P[x+1][y+1]==c && !(x==i&&y==j)){ any=true; break; }
                    }
                    if(!any) ok=false;
                }
                if(ok){ changed=true; }
                else{
                    P[pi][pj]=c;
                    for(auto&x:nb){ int d=P[x[0]][x[1]]; if(d!=0) edgeSub(0,d); }
                    for(auto&x:nb){ int d=P[x[0]][x[1]]; if(c!=d) edgeAdd(c,d); }
                }
            }
        }
    }

    // record the starting map from greedy
    {
        rebuildCL();
        int z=countZeros();
        if(globallyValid()){
            bestZeros=z;
            for(int i=0;i<N;i++)for(int j=0;j<N;j++) bestMap[i][j]=P[i+1][j+1];
        } else {
            // fall back to original
            resetToOriginal();
            bestZeros=countZeros();
            for(int i=0;i<N;i++)for(int j=0;j<N;j++) bestMap[i][j]=P[i+1][j+1];
        }
    }

    // Simulated annealing over local moves.
    mt19937_64 rng(1234567891ULL);
    auto urand=[&](double lo,double hi)->double{
        return lo + (hi-lo) * (double)(rng()>>11) / (double)(1ULL<<53);
    };

    // precompute current list of nonzero cells for sampling: we resample frequently.
    auto now=[&](){ return chrono::duration<double>(chrono::steady_clock::now()-t_start).count(); };

    double T0 = 1.5;
    double Tmin = 0.02;

    long long iter=0;
    while(true){
        double el=now();
        if(el>tl*0.97) break;

        double frac = el/tl;
        // temperature schedule: geometric-ish
        double T = T0 * pow(Tmin/T0, frac);

        // choose random cell
        int i = (int)(rng()%N);
        int j = (int)(rng()%N);
        int pi=i+1,pj=j+1;
        int c = P[pi][pj];

        // gather neighbor colors (padded, includes 0)
        int nb[4];
        for(int d=0;d<4;d++) nb[d]=P[pi+di[d]][pj+dj[d]];

        // propose move: with some prob erase if c!=0; else recolor
        bool doErase = (c!=0) && ((rng()%100) < 60);
        if(doErase){
            bool touch0=false;
            for(int d=0;d<4;d++) if(nb[d]==0){touch0=true;break;}
            if(!touch0) continue;
            if(!cConnected(c,i,j)) continue;

            // tentatively apply
            for(int d=0;d<4;d++){ int x=nb[d]; if(c!=x) edgeSub(c,x); }
            P[pi][pj]=0;
            for(int d=0;d<4;d++){ int x=nb[d]; if(x!=0) edgeAdd(0,x); }
            rebuildCL();

            bool ok=true;
            // check adjacency preservation (all pairs involving c or 0)
            for(int d=0;d<SZ&&ok;d++){
                if(d==c) continue;
                int a=min(c,d),b=max(c,d);
                bool has=cnt[a][b]>0;
                if(has!=(bool)origAdj[a][b]){ ok=false; break; }
            }
            if(ok){
                for(int d=1;d<SZ&&ok;d++){
                    int a=0,b=d;
                    bool has=cnt[a][b]>0;
                    if(has!=(bool)origAdj[a][b]){ ok=false; break; }
                }
            }
            if(ok){
                // c must remain connected and nonempty (connectivity after removal)
                bool any=false;
                int si=-1,sj=-1,total=0;
                for(auto&p:cl[c]){
                    int x=p.first,y=p.second;
                    if(P[x+1][y+1]==c){ if(si<0){si=x;sj=y;} total++; any=true; }
                }
                if(!any) ok=false;
                else{
                    markId++;
                    vector<pair<int,int>> stk; stk.push_back({si,sj}); mark[si*N+sj]=markId;
                    int seen=0;
                    while(!stk.empty()){
                        auto [x,y]=stk.back(); stk.pop_back(); seen++;
                        for(int d=0;d<4;d++){
                            int a=x+di[d],b=y+dj[d];
                            if(a>=0&&b>=0&&a<N&&b<N && P[a+1][b+1]==c && mark[a*N+b]!=markId){
                                mark[a*N+b]=markId; stk.push_back({a,b});
                            }
                        }
                    }
                    if(seen!=total) ok=false;
                }
            }

            if(ok){
                // always accept improving (more zeros); non-improving with exp(1/T)? Since erase always increases zeros -> accept
                iter++;
                int z=countZeros();
                if(z>bestZeros){
                    bestZeros=z;
                    for(int a=0;a<N;a++)for(int b=0;b<N;b++) bestMap[a][b]=P[a+1][b+1];
                }
            } else {
                // revert
                P[pi][pj]=c;
                for(int d=0;d<4;d++){ int x=nb[d]; if(x!=0) edgeSub(0,x); }
                for(int d=0;d<4;d++){ int x=nb[d]; if(c!=x) edgeAdd(c,x); }
                rebuildCL();
            }
        } else {
            // recolor move: change to a neighboring color != c, including 0? Only nonzero recolor per spec.
            // Choose a neighbor color != c, preferring nonzero to be a genuine recolor.
            int choices[5]; int nc=0;
            for(int d=0;d<4;d++) if(nb[d]!=c){ choices[nc++]=nb[d]; }
            if(nc==0) continue;
            int target = choices[rng()%nc];
            if(target==0) continue; // let erase move handle 0
            // tentatively change
            for(int d=0;d<4;d++){ int x=nb[d]; if(c!=x) edgeSub(c,x); }
            for(int d=0;d<4;d++){ int x=nb[d]; if(target!=x) edgeSub(target,x); }
            P[pi][pj]=target;
            for(int d=0;d<4;d++){ int x=P[pi+di[d]][pj+dj[d]]; if(target!=x) edgeAdd(target,x); }
            for(int d=0;d<4;d++){ int x=P[pi+di[d]][pj+dj[d]]; if(c!=x) edgeAdd(c,x); }
            // careful: recompute neighbors now
            int nb2[4];
            for(int d=0;d<4;d++) nb2[d]=P[pi+di[d]][pj+dj[d]];
            // Above edge updates were wrong: simplest is to revert cnt to reflect actual new map.
            // Reset cnt fully (small map; but expensive). Instead, we can recompute affected pairs.
            // We'll just recompute cnt from scratch to ensure correctness (N=50, SZ<=101 => 10k cells, fine).
            for(int a=0;a<SZ;a++)for(int b=0;b<SZ;b++) cnt[a][b]=0;
            for(int a=0;a<PN;a++)for(int b=0;b<PN;b++){
                int x=P[a][b];
                if(b+1<PN){int y=P[a][b+1]; if(x!=y) edgeAdd(x,y);}
                if(a+1<PN){int y=P[a+1][b]; if(x!=y) edgeAdd(x,y);}
            }
            rebuildCL();

            bool ok=true;
            for(int d=0;d<SZ&&ok;d++){
                if(d==c) continue;
                int a=min(c,d),b=max(c,d);
                bool has=cnt[a][b]>0;
                if(has!=(bool)origAdj[a][b]){ ok=false; break; }
            }
            if(ok){
                for(int d=0;d<SZ&&ok;d++){
                    if(d==target) continue;
                    int a=min(target,d),b=max(target,d);
                    bool has=cnt[a][b]>0;
                    if(has!=(bool)origAdj[a][b]){ ok=false; break; }
                }
            }
            if(ok){
                for(int d=1;d<SZ&&ok;d++){
                    int a=0,b=d;
                    bool has=cnt[a][b]>0;
                    if(has!=(bool)origAdj[a][b]){ ok=false; break; }
                }
            }
            // connectivity of c and target
            if(ok){
                int cs[2]={c,target};
                for(int idx=0;idx<2&&ok;idx++){
                    int cc=cs[idx];
                    int si=-1,sj=-1,total=0;
                    for(auto&p:cl[cc]){
                        int x=p.first,y=p.second;
                        if(P[x+1][y+1]==cc){ if(si<0){si=x;sj=y;} total++; }
                    }
                    if(total==0){ ok=false; break; }
                    markId++;
                    vector<pair<int,int>> stk; stk.push_back({si,sj}); mark[si*N+sj]=markId;
                    int seen=0;
                    while(!stk.empty()){
                        auto [x,y]=stk.back(); stk.pop_back(); seen++;
                        for(int d=0;d<4;d++){
                            int a=x+di[d],b=y+dj[d];
                            if(a>=0&&b>=0&&a<N&&b<N && P[a+1][b+1]==cc && mark[a*N+b]!=markId){
                                mark[a*N+b]=markId; stk.push_back({a,b});
                            }
                        }
                    }
                    if(seen!=total){ ok=false; break; }
                }
            }

            if(ok){
                iter++;
                int z=countZeros();
                if(z>bestZeros){
                    bestZeros=z;
                    for(int a=0;a<N;a++)for(int b=0;b<N;b++) bestMap[a][b]=P[a+1][b+1];
                }
            } else {
                // revert
                P[pi][pj]=c;
                for(int a=0;a<SZ;a++)for(int b=0;b<SZ;b++) cnt[a][b]=0;
                for(int a=0;a<PN;a++)for(int b=0;b<PN;b++){
                    int x=P[a][b];
                    if(b+1<PN){int y=P[a][b+1]; if(x!=y) edgeAdd(x,y);}
                    if(a+1<PN){int y=P[a+1][b]; if(x!=y) edgeAdd(x,y);}
                }
                rebuildCL();
            }
        }

        // occasionally re-seed from best if stagnating? keep simple: no.
    }

    // final safety: if bestMap somehow invalid, fall back to original
    {
        vector<vector<int>> tmp=P; // not used
        // reconstruct and validate bestMap
        for(int i=0;i<PN;i++)for(int j=0;j<PN;j++) P[i][j]=0;
        for(int i=0;i<N;i++)for(int j=0;j<N;j++) P[i+1][j+1]=bestMap[i][j];
        if(!globallyValid()){
            for(int i=0;i<N;i++)for(int j=0;j<N;j++) bestMap[i][j]=orig[i][j];
        }
    }

    for(int i=0;i<N;i++){
        for(int j=0;j<N;j++) cout<<bestMap[i][j]<<(j+1<N?" ":"\n");
    }
    return 0;
}
// EVOLVE-BLOCK-END
