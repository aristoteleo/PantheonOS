// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;

static int n, m, W;
static vector<vector<int>> cur;
static const int di[4]={-1,1,0,0}, dj[4]={0,0,-1,1};

static vector<vector<char>> target;
static vector<vector<int>> ec;

static vector<char> vis;
static vector<int> stk;

static int bfsColor(int c, int si, int sj){
    vis.assign(n*n,0);
    stk.clear();
    if(si<0) return 0;
    int p=si*n+sj; vis[p]=1; stk.push_back(p);
    int k=0; size_t idx=0;
    while(idx<stk.size()){
        int q=stk[idx++]; k++;
        int x=q/n, y=q%n;
        for(int d=0;d<4;d++){
            int a=x+di[d], b=y+dj[d];
            if(a<0||b<0||a>=n||b>=n) continue;
            if(cur[a][b]!=c) continue;
            int pp=a*n+b;
            if(vis[pp]) continue;
            vis[pp]=1; stk.push_back(pp);
        }
    }
    return k;
}

static bool allConnected(){
    vector<int> cntc(W,0);
    for(int i=0;i<n;i++)for(int j=0;j<n;j++) cntc[cur[i][j]]++;
    for(int c=1;c<W;c++){
        if(cntc[c]==0) continue;
        int si=-1,sj=-1;
        for(int i=0;i<n&&si<0;i++)for(int j=0;j<n;j++) if(cur[i][j]==c){si=i;sj=j;break;}
        if(bfsColor(c,si,sj)!=cntc[c]) return false;
    }
    if(cntc[0]==0) return true;
    vis.assign(n*n,0);
    stk.clear();
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        if(cur[i][j]==0 && (i==0||j==0||i==n-1||j==n-1)){
            int p=i*n+j; if(!vis[p]){vis[p]=1; stk.push_back(p);}
        }
    }
    int k=0; size_t idx=0;
    while(idx<stk.size()){
        int p=stk[idx++]; k++;
        int x=p/n,y=p%n;
        for(int d=0;d<4;d++){
            int a=x+di[d],b=y+dj[d];
            if(a<0||b<0||a>=n||b>=n) continue;
            if(cur[a][b]!=0) continue;
            int pp=a*n+b;
            if(vis[pp]) continue;
            vis[pp]=1; stk.push_back(pp);
        }
    }
    return k==cntc[0];
}

int main(int argc,char**argv){
    ios::sync_with_stdio(false); cin.tie(nullptr);
    double tl = argc>1?atof(argv[1]):2.0;
    auto t0 = chrono::steady_clock::now();
    auto elapsed=[&](){return chrono::duration<double>(chrono::steady_clock::now()-t0).count();};

    if(!(cin>>n>>m)) return 0;
    cur.assign(n, vector<int>(n));
    for(auto&r:cur)for(int&c:r)cin>>c;
    W=m+1;

    target.assign(W, vector<char>(W,0));
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        int a=cur[i][j];
        for(int d=0;d<4;d++){
            int x=i+di[d],y=j+dj[d];
            int b=(x<0||y<0||x>=n||y>=n)?0:cur[x][y];
            if(a!=b){ target[a][b]=1; target[b][a]=1; }
        }
    }

    ec.assign(W, vector<int>(W,0));
    auto addE=[&](int a,int b){ if(a!=b) ec[a][b]++; };
    auto remE=[&](int a,int b){ if(a!=b) ec[a][b]--; };
    for(int i=0;i<n;i++)for(int j=0;j<n;j++){
        int a=cur[i][j];
        if(j+1<n) addE(a,cur[i][j+1]);
        if(i+1<n) addE(a,cur[i+1][j]);
        if(i==0) addE(a,0);
        if(i==n-1) addE(a,0);
        if(j==0) addE(a,0);
        if(j==n-1) addE(a,0);
    }
    for(int a=0;a<W;a++)for(int b=a+1;b<W;b++){
        int v = ec[a][b] + ec[b][a];
        ec[a][b]=v; ec[b][a]=v;
    }

    auto adjOk=[&](int c)->bool{
        for(int b=0;b<W;b++){
            if(b==c) continue;
            bool now = ec[c][b]>0;
            bool tgt = target[c][b]!=0;
            if(now!=tgt) return false;
        }
        return true;
    };

    vector<int> cnt(W,0);
    for(int i=0;i<n;i++)for(int j=0;j<n;j++)cnt[cur[i][j]]++;

    auto tryErase=[&](int i,int j)->bool{
        int c=cur[i][j];
        if(c==0) return false;
        if(cnt[c]<=1) return false;

        bool atBorder=(i==0||j==0||i==n-1||j==n-1);
        bool hasZero=false;
        for(int d=0;d<4;d++){
            int x=i+di[d],y=j+dj[d];
            int col=(x<0||y<0||x>=n||y>=n)?0:cur[x][y];
            if(col==0){hasZero=true;break;}
        }
        if(!atBorder&&!hasZero) return false;

        for(int d=0;d<4;d++){
            int x=i+di[d],y=j+dj[d];
            int b=(x<0||y<0||x>=n||y>=n)?0:cur[x][y];
            if(b!=c){ remE(c,b); remE(b,c); }
        }
        cur[i][j]=0; cnt[c]--; cnt[0]++;
        for(int d=0;d<4;d++){
            int x=i+di[d],y=j+dj[d];
            int b=(x<0||y<0||x>=n||y>=n)?0:cur[x][y];
            if(b!=0){ addE(0,b); addE(b,0); }
        }

        bool ok = adjOk(c) && adjOk(0);

        if(ok){
            int si=-1,sj=-1;
            for(int d=0;d<4;d++){
                int x=i+di[d],y=j+dj[d];
                if(x<0||y<0||x>=n||y>=n) continue;
                if(cur[x][y]==c){ si=x; sj=y; break; }
            }
            if(si>=0){
                if(bfsColor(c,si,sj)!=cnt[c]) ok=false;
            } else {
                int fi=-1,fj=-1;
                for(int x2=0;x2<n&&fi<0;x2++)for(int y2=0;y2<n;y2++) if(cur[x2][y2]==c){fi=x2;fj=y2;break;}
                if(fi>=0){ if(bfsColor(c,fi,fj)!=cnt[c]) ok=false; }
            }
        }
        if(ok){
            vis.assign(n*n,0);
            stk.clear();
            for(int ii=0;ii<n;ii++)for(int jj=0;jj<n;jj++){
                if(cur[ii][jj]==0 && (ii==0||jj==0||ii==n-1||jj==n-1)){
                    int p=ii*n+jj; if(!vis[p]){vis[p]=1; stk.push_back(p);}
                }
            }
            int kc=0; size_t idx=0;
            while(idx<stk.size()){
                int p=stk[idx++]; kc++;
                int x=p/n,y=p%n;
                for(int d=0;d<4;d++){
                    int a=x+di[d],b=y+dj[d];
                    if(a<0||b<0||a>=n||b>=n) continue;
                    if(cur[a][b]!=0) continue;
                    int pp=a*n+b;
                    if(vis[pp]) continue;
                    vis[pp]=1; stk.push_back(pp);
                }
            }
            if(kc!=cnt[0]) ok=false;
        }

        if(ok) return true;

        cnt[0]--; cnt[c]++; cur[i][j]=c;
        for(int d=0;d<4;d++){
            int x=i+di[d],y=j+dj[d];
            int b=(x<0||y<0||x>=n||y>=n)?0:cur[x][y];
            if(b!=0){ remE(0,b); remE(b,0); }
        }
        for(int d=0;d<4;d++){
            int x=i+di[d],y=j+dj[d];
            int b=(x<0||y<0||x>=n||y>=n)?0:cur[x][y];
            if(b!=c){ addE(c,b); addE(b,c); }
        }
        return false;
    };

    auto colorConnected=[&](int c)->bool{
        if(cnt[c]<=1) return true;
        int si=-1,sj=-1;
        for(int i=0;i<n&&si<0;i++)for(int j=0;j<n;j++) if(cur[i][j]==c){si=i;sj=j;break;}
        if(si<0) return true;
        return bfsColor(c,si,sj)==cnt[c];
    };

    auto trySwap=[&](int i1, int j1, int i2, int j2)->bool{
        if(abs(i1-i2)+abs(j1-j2)!=1) return false;
        int c1 = cur[i1][j1], c2 = cur[i2][j2];
        if(c1==0 || c2==0 || c1==c2) return false;
        
        // Apply edge updates: p1 goes c1->c2, p2 goes c2->c1
        for(int d=0;d<4;d++){
            int x=i1+di[d], y=j1+dj[d];
            if(x==i2 && y==j2) continue;
            int nb = (x<0||y<0||x>=n||y>=n) ? 0 : cur[x][y];
            if(nb != c1){ remE(c1, nb); remE(nb, c1); }
            if(nb != c2){ addE(c2, nb); addE(nb, c2); }
        }
        for(int d=0;d<4;d++){
            int x=i2+di[d], y=j2+dj[d];
            if(x==i1 && y==j1) continue;
            int nb = (x<0||y<0||x>=n||y>=n) ? 0 : cur[x][y];
            if(nb != c2){ remE(c2, nb); remE(nb, c2); }
            if(nb != c1){ addE(c1, nb); addE(nb, c1); }
        }
        cur[i1][j1] = c2;
        cur[i2][j2] = c1;
        
        bool ok = adjOk(c1) && adjOk(c2) && colorConnected(c1) && colorConnected(c2);
        
        if(!ok){
            // revert
            cur[i1][j1] = c1;
            cur[i2][j2] = c2;
            // now undo edge changes: p1 goes c2->c1, p2 goes c1->c2
            for(int d=0;d<4;d++){
                int x=i1+di[d], y=j1+dj[d];
                if(x==i2 && y==j2) continue;
                int nb = (x<0||y<0||x>=n||y>=n) ? 0 : cur[x][y];
                if(nb != c2){ remE(c2, nb); remE(nb, c2); }
                if(nb != c1){ addE(c1, nb); addE(nb, c1); }
            }
            for(int d=0;d<4;d++){
                int x=i2+di[d], y=j2+dj[d];
                if(x==i1 && y==j1) continue;
                int nb = (x<0||y<0||x>=n||y>=n) ? 0 : cur[x][y];
                if(nb != c1){ remE(c1, nb); remE(nb, c1); }
                if(nb != c2){ addE(c2, nb); addE(nb, c2); }
            }
            return false;
        }
        return true;
    };

    mt19937 rng(20240924u);

    // Initial greedy erasure
    {
        bool improved=true;
        while(improved && elapsed()<tl*0.35){
            improved=false;
            vector<tuple<int,int,int>> cand;
            cand.reserve(n*n);
            for(int i=0;i<n;i++)for(int j=0;j<n;j++){
                int c=cur[i][j];
                if(c==0) continue;
                int same=0, zero=0;
                for(int d=0;d<4;d++){
                    int x=i+di[d],y=j+dj[d];
                    int b=(x<0||y<0||x>=n||y>=n)?0:cur[x][y];
                    if(b==0) zero++;
                    else if(b==c) same++;
                }
                int border=(i==0||j==0||i==n-1||j==n-1)?1:0;
                int key = same*30 - zero*15 - border*5 + (int)(rng()%7);
                cand.push_back({key,i,j});
            }
            sort(cand.begin(), cand.end());
            for(auto&t:cand){
                if(elapsed()>tl*0.38) break;
                int i=get<1>(t), j=get<2>(t);
                if(cur[i][j]==0) continue;
                if(tryErase(i,j)) improved=true;
            }
        }

        while(elapsed()<tl*0.5){
            vector<pair<int,int>> cand;
            for(int i=0;i<n;i++)for(int j=0;j<n;j++) if(cur[i][j]!=0) cand.push_back({i,j});
            if(cand.empty()) break;
            shuffle(cand.begin(),cand.end(),rng);
            bool any=false;
            for(auto&pr:cand){
                if(elapsed()>tl*0.5) break;
                if(cur[pr.first][pr.second]==0) continue;
                if(tryErase(pr.first,pr.second)) any=true;
            }
            if(!any) break;
        }
    }
    
    // Main loop: erasure passes + swaps
    bool lastEraseImproved = true;
    while(elapsed()<tl*0.97){
        // (a) Full pass of tryErase in random order
        vector<pair<int,int>> cand;
        cand.reserve(n*n);
        for(int i=0;i<n;i++)for(int j=0;j<n;j++) if(cur[i][j]!=0) cand.push_back({i,j});
        shuffle(cand.begin(),cand.end(),rng);
        bool any=false;
        for(auto&pr:cand){
            if(elapsed()>tl*0.97) break;
            int i=pr.first, j=pr.second;
            if(cur[i][j]==0) continue;
            if(tryErase(i,j)) any=true;
        }
        lastEraseImproved = any;
        
        if(elapsed()>tl*0.97) break;
        
        if(!any){
            // (b) Up to 100 random valid swaps
            // Build list of adjacent non-zero pairs with different colors
            vector<tuple<int,int,int,int>> pairs;
            for(int i=0;i<n;i++)for(int j=0;j<n;j++){
                int c=cur[i][j];
                if(c==0) continue;
                if(j+1<n && cur[i][j+1]!=0 && cur[i][j+1]!=c){
                    pairs.push_back({i,j,i,j+1});
                }
                if(i+1<n && cur[i+1][j]!=0 && cur[i+1][j]!=c){
                    pairs.push_back({i,j,i+1,j});
                }
            }
            if(pairs.empty()) break;
            shuffle(pairs.begin(), pairs.end(), rng);
            int limit = min((int)pairs.size(), 100);
            bool anySwap = false;
            for(int k=0;k<limit;k++){
                if(elapsed()>tl*0.97) break;
                auto&p = pairs[k];
                int i1=get<0>(p), j1=get<1>(p), i2=get<2>(p), j2=get<3>(p);
                // Re-check: cells might have been modified by earlier swaps
                int c1=cur[i1][j1], c2=cur[i2][j2];
                if(c1==0||c2==0||c1==c2) continue;
                if(trySwap(i1,j1,i2,j2)) anySwap=true;
            }
            if(!anySwap && !lastEraseImproved){
                // No progress at all - try a few more randomized swaps
                // (since our pair list may have become stale)
                bool progress = false;
                for(int trial=0; trial<200 && elapsed()<tl*0.97; trial++){
                    int i = rng()%n, j = rng()%n;
                    if(cur[i][j]==0) continue;
                    int d = rng()%4;
                    int x=i+di[d], y=j+dj[d];
                    if(x<0||y<0||x>=n||y>=n) continue;
                    if(cur[x][y]==0) continue;
                    if(cur[x][y]==cur[i][j]) continue;
                    if(trySwap(i,j,x,y)){ progress=true; break; }
                }
                if(!progress) break;
            }
        }
    }

    // Final output
    string out; out.reserve((size_t)n*(n*4));
    for(int i=0;i<n;i++){
        for(int j=0;j<n;j++){
            out+=to_string(cur[i][j]);
            if(j+1<n) out+=' '; else out+='\n';
        }
    }
    cout<<out;
    return 0;
}
// EVOLVE-BLOCK-END
