// EVOLVE-BLOCK-START
#include <bits/stdc++.h>
using namespace std;

int N, M;
static const int di[4]={-1,1,0,0}, dj[4]={0,0,-1,1};

int main(int argc, char**argv){
    double tl = argc>1 ? atof(argv[1]) : 2.0;
    auto t0 = chrono::steady_clock::now();
    auto el=[&](){ return chrono::duration<double>(chrono::steady_clock::now()-t0).count(); };
    if(!(cin>>N>>M)) return 0;
    vector<int> C(N*N);
    for(int i=0;i<N*N;i++) cin>>C[i];
    vector<char> isBorder(N*N,0);
    for(int i=0;i<N;i++)for(int j=0;j<N;j++) if(i==0||j==0||i==N-1||j==N-1) isBorder[i*N+j]=1;
    vector<char> borderColor(M+1,0);
    for(int k=0;k<N*N;k++) if(isBorder[k]) borderColor[C[k]]=1;

    // build old adjacency set once
    set<pair<int,int>> oldA;
    for(int i=0;i<N;i++)for(int j=0;j<N;j++){
        int a=C[i*N+j];
        for(int d=0;d<4;d++){
            int x=i+di[d],y=j+dj[d];
            int b=(x<0||y<0||x>=N||y>=N)?0:C[x*N+y];
            if(a!=b){ int aa=a,bb=b; if(aa>bb) swap(aa,bb); oldA.insert({aa,bb}); }
        }
    }

    vector<char> kept(N*N,1);
    vector<int> D(N*N);
    vector<int> cnt(M+1,0);
    vector<char> vis(N*N,0);
    vector<int> qbuf;

    auto validityOK=[&]()->bool{
        // skip if no zero present
        bool anyZero=false;
        for(int k=0;k<N*N;k++){ D[k]=kept[k]?C[k]:0; if(D[k]==0) anyZero=true; }
        if(!anyZero) return true;
        // connectivity
        for(int c=0;c<=M;c++) cnt[c]=0;
        for(int k=0;k<N*N;k++) cnt[D[k]]++;
        fill(vis.begin(), vis.end(), 0);
        for(int c=0;c<=M;c++){
            if(cnt[c]==0) continue;
            qbuf.clear();
            if(c==0){
                bool any=false;
                for(int k=0;k<N*N;k++) if(D[k]==0 && isBorder[k]){ vis[k]=1; qbuf.push_back(k); any=true; }
                if(!any) return false;
            } else {
                int seed=-1;
                for(int k=0;k<N*N;k++) if(D[k]==c){seed=k;break;}
                vis[seed]=1; qbuf.push_back(seed);
            }
            size_t head=0;
            while(head<qbuf.size()){
                int k=qbuf[head++]; int x=k/N,y=k%N;
                for(int d=0;d<4;d++){
                    int a=x+di[d],b=y+dj[d];
                    if(a<0||b<0||a>=N||b>=N) continue;
                    int kk=a*N+b;
                    if(vis[kk] || D[kk]!=c) continue;
                    vis[kk]=1; qbuf.push_back(kk);
                }
            }
            if((int)qbuf.size()!=cnt[c]) return false;
        }
        // adjacency preservation
        // For each old pair (a,b), must exist in new; no new pairs allowed
        // Compute new adjacency as set
        static set<pair<int,int>> newA;
        newA.clear();
        for(int i=0;i<N;i++)for(int j=0;j<N;j++){
            int a=D[i*N+j];
            for(int d=0;d<4;d++){
                int x=i+di[d],y=j+dj[d];
                int b=(x<0||y<0||x>=N||y>=N)?0:D[x*N+y];
                if(a==b) continue;
                int aa=a,bb=b; if(aa>bb) swap(aa,bb);
                if(oldA.count({aa,bb}) == 0) return false; // new pair not in old
                newA.insert({aa,bb});
            }
        }
        if(newA.size()!=oldA.size()) return false;
        return true;
    };

    mt19937_64 rng(88172645463325252ULL);
    vector<int> order(N*N);
    iota(order.begin(),order.end(),0);

    // Initial: try removing each cell once in order
    auto doPass=[&](double deadline){
        shuffle(order.begin(),order.end(),rng);
        bool changed=false;
        for(int k: order){
            if(el()>deadline) return changed;
            if(!kept[k] || C[k]==0) continue;
            kept[k]=0;
            if(validityOK()) changed=true;
            else kept[k]=1;
        }
        return changed;
    };

    // Do several passes until time runs out
    for(int pass=0; pass<30; pass++){
        if(el() > tl*0.85) break;
        if(!doPass(tl*0.85)) break;
    }

    string out;
    out.reserve((size_t)N*(N*4+2));
    for(int i=0;i<N;i++){
        for(int j=0;j<N;j++){
            int v = kept[i*N+j]? C[i*N+j] : 0;
            out += to_string(v);
            out += (j+1<N?' ':'\n');
        }
    }
    cout<<out;
    return 0;
}
// EVOLVE-BLOCK-END
