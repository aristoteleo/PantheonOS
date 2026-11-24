// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;
static const int di[4]={-1,1,0,0}, dj[4]={0,0,-1,1};
static int n, m;
static vector<vector<int>> orig, cur;
static vector<vector<int>> cnt;
static vector<int> wardCnt;
static vector<vector<char>> A0;
static vector<vector<int>> vis;
static int stamp=0;
static vector<pair<int,int>> qb;

static inline bool inb(int i,int j){ return i>=0&&j>=0&&i<n&&j<n; }

static int reachCount(int si,int sj,int c,int pi,int pj){
    stamp++;
    qb.clear();
    qb.push_back({si,sj});
    vis[si][sj]=stamp;
    for(size_t h=0;h<qb.size();++h){
        int x=qb[h].first, y=qb[h].second;
        for(int d=0;d<4;d++){
            int a=x+di[d], b=y+dj[d];
            if(!inb(a,b)) continue;
            if(a==pi&&b==pj) continue;
            if(cur[a][b]!=c) continue;
            if(vis[a][b]==stamp) continue;
            vis[a][b]=stamp;
            qb.push_back({a,b});
        }
    }
    return (int)qb.size();
}

static void initCounts(){
    cnt.assign(m+1, vector<int>(m+1,0));
    wardCnt.assign(m+1,0);
    for(int i=0;i<n;i++)for(int j=0;j<n;j++) wardCnt[cur[i][j]]++;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        if(j+1<n){ int a=cur[i][j],b=cur[i][j+1]; if(a!=b){cnt[a][b]++;cnt[b][a]++;} }
        if(i+1<n){ int a=cur[i][j],b=cur[i+1][j]; if(a!=b){cnt[a][b]++;cnt[b][a]++;} }
    }
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        if(i==0||j==0||i==n-1||j==n-1){
            int a=cur[i][j]; if(a!=0){ cnt[a][0]++; cnt[0][a]++; }
        }
    }
}

static bool tryRemove(int i,int j){
    int c=cur[i][j];
    if(c==0||wardCnt[c]<=1) return false;
    bool touch0=(i==0||j==0||i==n-1||j==n-1);
    int nb[4];
    int sameCount=0;
    for(int d=0;d<4;d++){
        int a=i+di[d],b=j+dj[d];
        nb[d]= inb(a,b)? cur[a][b] : 0;
        if(nb[d]==c) sameCount++;
        if(nb[d]==0) touch0=true;
    }
    if(!touch0) return false;
    if(sameCount>=2){
        int si=-1,sj=-1;
        for(int d=0;d<4;d++){
            int a=i+di[d], b=j+dj[d];
            if(inb(a,b)&&cur[a][b]==c){ si=a; sj=b; break; }
        }
        if(si<0) return false;
        if(reachCount(si,sj,c,i,j) != wardCnt[c]-1) return false;
    }
    cur[i][j]=0;
    for(int d=0;d<4;d++){
        int b=nb[d];
        if(b!=c){ cnt[c][b]--; cnt[b][c]--; }
        if(b!=0){ cnt[0][b]++; cnt[b][0]++; }
    }
    bool ok=true;
    for(int d=0;d<4&&ok;d++){
        int b=nb[d];
        if(b!=c && b!=0 && A0[c][b] && cnt[c][b]==0) ok=false;
    }
    if(ok && A0[c][0] && cnt[c][0]==0) ok=false;
    if(ok){
        for(int d=0;d<4&&ok;d++){
            int b=nb[d];
            if(b!=0 && !A0[0][b]) ok=false;
        }
    }
    if(ok){ wardCnt[c]--; return true; }
    cur[i][j]=c;
    for(int d=0;d<4;d++){
        int b=nb[d];
        if(b!=c){ cnt[c][b]++; cnt[b][c]++; }
        if(b!=0){ cnt[0][b]--; cnt[b][0]--; }
    }
    return false;
}

static int countZeros(){
    int z=0;
    for(int i=0;i<n;i++)for(int j=0;j<n;j++) if(cur[i][j]==0) z++;
    return z;
}

int main(int argc,char**argv){
    double tl = argc>1?atof(argv[1]):2.0;
    auto t0=chrono::steady_clock::now();
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    if(!(cin>>n>>m)) return 0;
    orig.assign(n, vector<int>(n));
    for(auto&r:orig)for(auto&c:r)cin>>c;

    A0.assign(m+1, vector<char>(m+1,0));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        int a=orig[i][j];
        for(int d=0;d<4;d++){
            int x=i+di[d], y=j+dj[d];
            int b=inb(x,y)?orig[x][y]:0;
            if(a!=b) A0[a][b]=A0[b][a]=1;
        }
    }

    vis.assign(n, vector<int>(n,0));
    qb.reserve(n*n);

    int bestZeros=-1;
    vector<vector<int>> bestMap;

    auto elapsed=[&](){return chrono::duration<double>(chrono::steady_clock::now()-t0).count();};

    vector<int> ord(n*n);

    auto runSweep=[&](mt19937 &rng, int mode)->int{
        bool progress=true;
        int passes=0;
        while(progress && passes<150){
            if(elapsed()>tl*0.9) break;
            progress=false; passes++;
            for(int i=0;i<n*n;i++) ord[i]=i;
            if(mode==0){
                shuffle(ord.begin(), ord.end(), rng);
            } else {
                shuffle(ord.begin(), ord.end(), rng);
                vector<int> deg(n*n,0);
                for(int i=0;i<n;i++)for(int j=0;j<n;j++){
                    int c=cur[i][j]; int s=0;
                    if(c!=0){
                        for(int d=0;d<4;d++){
                            int a=i+di[d],b=j+dj[d];
                            if(inb(a,b)&&cur[a][b]==c) s++;
                        }
                    } else s=100;
                    deg[i*n+j]=s;
                }
                stable_sort(ord.begin(), ord.end(), [&](int a,int b){return deg[a]<deg[b];});
            }
            for(int k=0;k<n*n;k++){
                int ii=ord[k]/n, jj=ord[k]%n;
                if(cur[ii][jj]==0) continue;
                if(tryRemove(ii,jj)) progress=true;
            }
        }
        return countZeros();
    };

    auto runPQ=[&](mt19937 &rng, int mode)->int{
        auto score=[&](int i,int j)->int{
            int c=cur[i][j];
            if(c==0) return 1000000000;
            int s=0, z=0;
            for(int d=0;d<4;d++){
                int a=i+di[d],b=j+dj[d];
                int bb = inb(a,b)? cur[a][b] : 0;
                if(bb==c) s++;
                else if(bb==0) z++;
            }
            int base = s*12 - z*25;
            if(z==0) base += 5000;
            if(mode==1){
                if(s==1) base -= 300;
                if(s==0) base -= 2000;
            }
            base += (int)(rng()%9);
            return base;
        };
        priority_queue<tuple<int,int,int>, vector<tuple<int,int,int>>, greater<>> pq;
        for(int i=0;i<n;i++)for(int j=0;j<n;j++){
            if(cur[i][j]!=0) pq.push({score(i,j), i, j});
        }
        while(!pq.empty()){
            if(elapsed()>tl*0.92) break;
            auto [sc,i,j]=pq.top(); pq.pop();
            if(cur[i][j]==0) continue;
            // Lazy re-push if priority stale
            int nsc=score(i,j);
            // If stale (significantly larger than top), re-push and continue
            if(sc > nsc + 20){ pq.push({nsc,i,j}); continue; }
            if(tryRemove(i,j)){
                for(int d=0;d<4;d++){
                    int a=i+di[d], b=j+dj[d];
                    if(inb(a,b)&&cur[a][b]!=0){
                        pq.push({score(a,b),a,b});
                    }
                }
            }
        }
        return countZeros();
    };

    // Initial
    {
        cur=orig; initCounts();
        mt19937 rng(1u);
        int z=runPQ(rng, 1);
        if(z>bestZeros){bestZeros=z;bestMap=cur;}
    }

    int attempt=1;
    while(elapsed()<tl*0.75){
        mt19937 rng(987654321u + attempt*2654435761u);
        cur=orig; initCounts();
        int mode=attempt%3;
        int z;
        if(mode<2) z=runSweep(rng, mode);
        else z=runPQ(rng, (attempt/3)%2);
        if(z>bestZeros){ bestZeros=z; bestMap=cur; }
        attempt++;
    }

    // Polish
    {
        int noImp=0;
        while(elapsed()<tl*0.95 && noImp<40){
            mt19937 rng(271828u + attempt*1000003u + noImp*17u);
            cur=bestMap; initCounts();
            int z=runSweep(rng, 1);
            if(z>bestZeros){ bestZeros=z; bestMap=cur; noImp=0; }
            else noImp++;
            if(elapsed()>tl*0.95) break;
            cur=bestMap; initCounts();
            z=runPQ(rng, 1);
            if(z>bestZeros){ bestZeros=z; bestMap=cur; noImp=0; }
            else noImp++;
            attempt++;
        }
    }

    if(bestMap.empty()) bestMap=orig;
    for(int i=0;i<n;i++){
        for(int j=0;j<n;j++){
            cout<<bestMap[i][j]<<(j+1<n?' ':'\n');
        }
    }
    return 0;
}
// EVOLVE-BLOCK-END
