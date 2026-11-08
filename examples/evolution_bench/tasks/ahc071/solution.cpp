// AHC071 seed: in each column with a hole, a tower of width-1 bricks from y = 0 up to the column's
// highest hole. Always supported (each brick sits on the one below); cost = 5 * total tower height.
#include <bits/stdc++.h>
using namespace std;

int W, H, K;
int cost[10];
vector<pair<int,int>> holes;

// EVOLVE-BLOCK-START
struct Brick { int x, y, l; };
static vector<Brick> plan() {
    vector<int> top(W, -1);
    for (auto &h : holes) top[h.first] = max(top[h.first], h.second);
    vector<Brick> out;
    for (int x = 0; x < W; x++)
        for (int y = 0; y <= top[x]; y++) out.push_back({x, y, 1});
    return out;
}
// EVOLVE-BLOCK-END

int main(int argc, char **argv) {
    cin >> W >> H >> K;
    for (int l : {1, 3, 5, 7, 9}) cin >> cost[l];
    holes.resize(K); for (auto &h : holes) cin >> h.first >> h.second;
    auto b = plan();
    printf("%d\n", (int)b.size());
    for (auto &x : b) printf("%d %d %d\n", x.x, x.y, x.l);
    return 0;
}
