#include <iostream>
#include <vector>
#include <algorithm>
#include <chrono>
#include <random>
#include <set> 
#include <unordered_set> 
#include <cmath> 
#include <iomanip> 
#include <numeric> // For std::iota
#include <string>  
#include <map>     

// === MACROS AND CONSTANTS ===
const int MAX_COORD_VAL = 100000;
const int MAX_VERTICES = 1000;
const int MAX_PERIMETER = 400000;
const double TIME_LIMIT_SECONDS_SAFETY_MARGIN = 0.1; // Increased safety margin
double ACTUAL_TIME_LIMIT_SECONDS = 1.95; 


// === RANDOM NUMBER GENERATION ===
struct XorShift {
    uint64_t x;
    XorShift() : x(std::chrono::steady_clock::now().time_since_epoch().count() ^ ((uint64_t)std::random_device()() << 32) ^ std::random_device()()) {}
    uint64_t next() {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        return x;
    }
    int next_int(int n) { if (n <= 0) return 0; return next() % n; }
    int next_int(int a, int b) { if (a > b) return a; return a + next_int(b - a + 1); } 
    double next_double() { return next() / (double)UINT64_MAX; }
};
XorShift rng; 

// === TIMER ===
struct Timer {
    std::chrono::steady_clock::time_point start_time;
    Timer() { reset(); }
    void reset() { start_time = std::chrono::steady_clock::now(); }
    double elapsed() const {
        auto now = std::chrono::steady_clock::now();
        return std::chrono::duration_cast<std::chrono::duration<double>>(now - start_time).count();
    }
};
Timer global_timer; 

// === GEOMETRIC STRUCTURES ===
struct Point {
    int x, y;
    bool operator<(const Point& other) const { 
        if (x != other.x) return x < other.x;
        return y < other.y;
    }
    bool operator==(const Point& other) const {
        return x == other.x && y == other.y;
    }
    Point operator-(const Point& other) const { 
        return {x - other.x, y - other.y};
    }
};

struct PointHash {
    std::size_t operator()(const Point& p) const {
        auto h1 = std::hash<int>{}(p.x);
        auto h2 = std::hash<int>{}(p.y);
        // Combining hashes: simple XOR might not be best, but often good enough.
        // For Point, a common way is boost::hash_combine.
        // h1 ^ (h2 << 1) is a common way that's okay.
        return h1 ^ (h2 << 1); 
    }
};

long long cross_product(Point a, Point b) { 
    return (long long)a.x * b.y - (long long)a.y * b.x;
}

struct Fish {
    Point p;
    int type; // 1 for mackerel, -1 for sardine
};
std::vector<Fish> all_fish_structs; 


// === KD-TREE ===
struct KDNode {
    Point pt; 
    int axis; 
    KDNode *left = nullptr, *right = nullptr;
    int fish_struct_idx = -1; 
};
KDNode* fish_kdtree_root = nullptr; 
std::vector<int> query_rect_indices_cache_kdtree; // Cache for KD-tree query results

KDNode* build_kdtree(std::vector<int>& point_indices, int l, int r, int axis) {
    if (l > r) return nullptr;
    int mid = l + (r - l) / 2;
    
    std::nth_element(point_indices.begin() + l, point_indices.begin() + mid, point_indices.begin() + r + 1, 
        [&](int a_idx, int b_idx) {
            const Point& pa = all_fish_structs[a_idx].p;
            const Point& pb = all_fish_structs[b_idx].p;
            if (axis == 0) return pa.x < pb.x;
            return pa.y < pb.y;
        });

    KDNode* node = new KDNode();
    node->fish_struct_idx = point_indices[mid];
    node->pt = all_fish_structs[node->fish_struct_idx].p;
    node->axis = axis;
    
    node->left = build_kdtree(point_indices, l, mid - 1, 1 - axis); 
    node->right = build_kdtree(point_indices, mid + 1, r, 1 - axis); 
    return node;
}

void query_kdtree_rectangle(KDNode* node, int min_x, int max_x, int min_y, int max_y, std::vector<int>& result_indices) {
    if (!node || min_x > max_x || min_y > max_y) return; 

    const Point& pt = node->pt;
    if (pt.x >= min_x && pt.x <= max_x && pt.y >= min_y && pt.y <= max_y) {
        result_indices.push_back(node->fish_struct_idx);
    }

    if (node->axis == 0) { // Split by X
        if (node->left && min_x <= node->pt.x) query_kdtree_rectangle(node->left, min_x, max_x, min_y, max_y, result_indices);
        if (node->right && max_x >= node->pt.x) query_kdtree_rectangle(node->right, min_x, max_x, min_y, max_y, result_indices);
    } else { // Split by Y
        if (node->left && min_y <= node->pt.y) query_kdtree_rectangle(node->left, min_x, max_x, min_y, max_y, result_indices);
        if (node->right && max_y >= node->pt.y) query_kdtree_rectangle(node->right, min_x, max_x, min_y, max_y, result_indices);
    }
}

void delete_kdtree(KDNode* node) { // Recursively delete KD-tree nodes
    if (!node) return;
    delete_kdtree(node->left);
    delete_kdtree(node->right);
    delete node;
}


// === POLYGON UTILITIES ===
long long calculate_perimeter(const std::vector<Point>& poly) {
    if (poly.size() < 2) return 0;
    long long perimeter = 0;
    for (size_t i = 0; i < poly.size(); ++i) {
        const Point& p1 = poly[i];
        const Point& p2 = poly[(i + 1) % poly.size()];
        perimeter += std::abs(p1.x - p2.x) + std::abs(p1.y - p2.y); 
    }
    return perimeter;
}

bool is_on_segment(Point p, Point seg_a, Point seg_b) {
    if (cross_product(seg_b - seg_a, p - seg_a) != 0) return false; // Not collinear
    return std::min(seg_a.x, seg_b.x) <= p.x && p.x <= std::max(seg_a.x, seg_b.x) &&
           std::min(seg_a.y, seg_b.y) <= p.y && p.y <= std::max(seg_a.y, seg_b.y);
}

bool is_inside_polygon_wn(Point p, const std::vector<Point>& polygon) {
    int n = polygon.size();
    if (n < 3) return false;

    // Check if on boundary first
    for (int i = 0; i < n; ++i) {
        if (is_on_segment(p, polygon[i], polygon[(i + 1) % n])) return true;
    }
    
    int wn = 0; // Winding number
    for (int i = 0; i < n; ++i) {
        Point p1 = polygon[i];
        Point p2 = polygon[(i + 1) % n];
        if (p1.y <= p.y) { // Start y <= P.y
            if (p2.y > p.y && cross_product(p2 - p1, p - p1) > 0) { // An upward crossing, P is left of edge
                wn++;
            }
        } else { // Start y > P.y
            if (p2.y <= p.y && cross_product(p2 - p1, p - p1) < 0) { // A downward crossing, P is right of edge
                wn--;
            }
        }
    }
    return wn != 0; // wn != 0 means inside; wn == 0 means outside.
}

// Calculate score from scratch by checking all fish
void calculate_score_from_scratch(const std::vector<Point>& poly, int& m_count, int& s_count) {
    m_count = 0; s_count = 0;
    if (poly.size() < 3) return; // Not a valid polygon for containment
    for (const auto& fish_s : all_fish_structs) {
        if (is_inside_polygon_wn(fish_s.p, poly)) {
            if (fish_s.type == 1) m_count++;
            else s_count++;
        }
    }
}

// Calculate fish counts in a given rectangle using KD-tree
void calculate_score_delta_for_rectangle(int r_min_x, int r_max_x, int r_min_y, int r_max_y, 
                                         int& delta_m, int& delta_s) {
    delta_m = 0; delta_s = 0;
    query_rect_indices_cache_kdtree.clear(); 
    
    if(!fish_kdtree_root || r_min_x > r_max_x || r_min_y > r_max_y) { // Invalid rectangle
        return;
    }
    
    query_kdtree_rectangle(fish_kdtree_root, r_min_x, r_max_x, r_min_y, r_max_y, query_rect_indices_cache_kdtree);
    
    for (int fish_struct_idx : query_rect_indices_cache_kdtree) {
        if (all_fish_structs[fish_struct_idx].type == 1) delta_m++;
        else delta_s++;
    }
}

// Check intersection between two orthogonal segments p1s-p1e and p2s-p2e
bool segments_intersect(Point p1s, Point p1e, Point p2s, Point p2e) {
    // Normalize segments (sort endpoints to simplify overlap checks)
    if (p1s.x == p1e.x) { if (p1s.y > p1e.y) std::swap(p1s.y, p1e.y); } // Vertical, sort by y
    else { if (p1s.x > p1e.x) std::swap(p1s.x, p1e.x); } // Horizontal, sort by x
    if (p2s.x == p2e.x) { if (p2s.y > p2e.y) std::swap(p2s.y, p2e.y); }
    else { if (p2s.x > p2e.x) std::swap(p2s.x, p2e.x); }

    bool seg1_is_H = (p1s.y == p1e.y);
    bool seg2_is_H = (p2s.y == p2e.y);

    if (seg1_is_H == seg2_is_H) { // Both horizontal or both vertical
        if (seg1_is_H) { // Both horizontal
            // Check for y-alignment and x-overlap
            return p1s.y == p2s.y && std::max(p1s.x, p2s.x) <= std::min(p1e.x, p2e.x);
        } else { // Both vertical
            // Check for x-alignment and y-overlap
            return p1s.x == p2s.x && std::max(p1s.y, p2s.y) <= std::min(p1e.y, p2e.y);
        }
    } else { // One horizontal, one vertical (potential T-junction or cross)
        Point h_s = seg1_is_H ? p1s : p2s; Point h_e = seg1_is_H ? p1e : p2e;
        Point v_s = seg1_is_H ? p2s : p1s; Point v_e = seg1_is_H ? p2e : p1e;
        // Check if intersection point (v_s.x, h_s.y) lies on both segments
        return v_s.x >= h_s.x && v_s.x <= h_e.x && // x_intersect within horizontal segment's x-range
               h_s.y >= v_s.y && h_s.y <= v_e.y;  // y_intersect within vertical segment's y-range
    }
}

bool check_self_intersection_full(const std::vector<Point>& poly) {
    int M = poly.size();
    if (M < 4) return false;
    for (int i = 0; i < M; ++i) {
        Point p1s = poly[i];
        Point p1e = poly[(i + 1) % M];
        for (int j = i + 2; j < M; ++j) {
            // Skip checking adjacent edges.
            // Edge i is (poly[i], poly[(i+1)%M]). Edge j is (poly[j], poly[(j+1)%M]).
            // If i=0 and j=M-1, then edge i is (poly[0], poly[1]) and edge j is (poly[M-1], poly[0]). These are adjacent.
            if (i == 0 && j == M - 1) continue; 

            Point p2s = poly[j];
            Point p2e = poly[(j + 1) % M];
            if (segments_intersect(p1s, p1e, p2s, p2e)) return true;
        }
    }
    return false;
}

// Local self-intersection check: checks edges starting at critical_edge_start_indices_const against all others
bool has_self_intersection_locally(const std::vector<Point>& poly, const std::vector<int>& critical_edge_start_indices_const) {
    int M = poly.size();
    if (M < 4) return false;
    
    std::vector<int> critical_indices = critical_edge_start_indices_const; // Make a copy to modify
    if (critical_indices.empty()) { 
      return false; 
    }
    
    std::sort(critical_indices.begin(), critical_indices.end());
    critical_indices.erase(std::unique(critical_indices.begin(), critical_indices.end()), critical_indices.end());

    for (int edge1_s_idx_val_orig : critical_indices) {
        int edge1_s_idx_val = (edge1_s_idx_val_orig % M + M) % M; // Ensure positive modulo
        // No need to check edge1_s_idx_val bounds, it will be in [0, M-1]

        Point p1s = poly[edge1_s_idx_val];
        Point p1e = poly[(edge1_s_idx_val + 1) % M];

        for (int edge2_s_idx = 0; edge2_s_idx < M; ++edge2_s_idx) {
            bool is_adj_or_same_to_p1s_p1e = (edge2_s_idx == edge1_s_idx_val ||                           // Same edge
                                   edge2_s_idx == (edge1_s_idx_val + 1) % M ||             // edge2 starts where edge1 ends
                                   (edge2_s_idx + 1) % M == edge1_s_idx_val); // edge2 ends where edge1 starts
            if (is_adj_or_same_to_p1s_p1e) continue;
            
            Point p2s = poly[edge2_s_idx];
            Point p2e = poly[(edge2_s_idx + 1) % M];
            if (segments_intersect(p1s, p1e, p2s, p2e)) {
                 return true;
            }
        }
    }
    return false;
}


bool has_distinct_vertices_unordered(const std::vector<Point>& poly) {
    if (poly.empty()) return true;
    std::unordered_set<Point, PointHash> distinct_pts; 
    distinct_pts.reserve(poly.size()); // Pre-allocate for efficiency
    for(const auto& p : poly) {
        if (!distinct_pts.insert(p).second) return false; // Insertion failed, duplicate found
    }
    return true;
}

// Check basic structural validity of the polygon
bool is_polygon_structurally_sound(const std::vector<Point>& poly) {
    int m = poly.size();
    if (m != 0 && (m < 4 || m > MAX_VERTICES)) return false;
    if (m == 0) return true; 

    if (calculate_perimeter(poly) > MAX_PERIMETER) return false;
    
    for (size_t i = 0; i < m; ++i) { 
        const Point& p1 = poly[i];
        const Point& p2 = poly[(i + 1) % m];
        // Check coordinate bounds for p1
        if (p1.x < 0 || p1.x > MAX_COORD_VAL || p1.y < 0 || p1.y > MAX_COORD_VAL) return false;
        // p2 is poly[(i+1)%m]. This check is implicitly done for p2 when it becomes p1,
        // except for the very last p2 which is poly[0]. poly[0] is checked as p1 in its iteration.
        // The original code had an explicit check for poly[(i+1)%m] too, which is redundant but harmless.
        // Let's keep it for safety/clarity.
        if (poly[(i+1)%m].x < 0 || poly[(i+1)%m].x > MAX_COORD_VAL || poly[(i+1)%m].y < 0 || poly[(i+1)%m].y > MAX_COORD_VAL) return false;


        // Check axis-parallel and non-zero length edges
        if (p1.x != p2.x && p1.y != p2.y) return false; // Not axis-parallel
        if (p1.x == p2.x && p1.y == p2.y) return false; // Zero-length edge (duplicate consecutive vertices)
    }
    return true;
}

// Initial polygon generation using Kadane's algorithm on a coarse grid
std::vector<Point> create_initial_polygon_kadane() {
    const int GRID_SIZE_KADANE = 350; // Tunable parameter
    const int NUM_VALUES_KADANE = MAX_COORD_VAL + 1;
    // Ensure ACTUAL_CELL_DIM_KADANE is at least 1
    const int ACTUAL_CELL_DIM_KADANE = std::max(1, (NUM_VALUES_KADANE + GRID_SIZE_KADANE - 1) / GRID_SIZE_KADANE); 

    std::vector<std::vector<long long>> grid_scores(GRID_SIZE_KADANE, std::vector<long long>(GRID_SIZE_KADANE, 0));
    for (const auto& fish_s : all_fish_structs) {
        int r = fish_s.p.y / ACTUAL_CELL_DIM_KADANE;
        int c = fish_s.p.x / ACTUAL_CELL_DIM_KADANE;
        r = std::min(r, GRID_SIZE_KADANE - 1); r = std::max(r,0); 
        c = std::min(c, GRID_SIZE_KADANE - 1); c = std::max(c,0);
        grid_scores[r][c] += fish_s.type; // Mackerel +1, Sardine -1
    }

    long long max_so_far = -3e18; // Sufficiently small number
    int best_r1 = 0, best_c1 = 0, best_r2 = -1, best_c2 = -1;

    // 2D Kadane's algorithm
    for (int c1_idx = 0; c1_idx < GRID_SIZE_KADANE; ++c1_idx) {
        std::vector<long long> col_strip_sum(GRID_SIZE_KADANE, 0); 
        for (int c2_idx = c1_idx; c2_idx < GRID_SIZE_KADANE; ++c2_idx) {
            for (int r_idx = 0; r_idx < GRID_SIZE_KADANE; ++r_idx) {
                col_strip_sum[r_idx] += grid_scores[r_idx][c2_idx];
            }
            
            // 1D Kadane's on col_strip_sum
            long long current_strip_val = 0;
            int current_r1_1d = 0;
            for (int r2_idx_1d = 0; r2_idx_1d < GRID_SIZE_KADANE; ++r2_idx_1d) {
                long long val_here = col_strip_sum[r2_idx_1d];
                if (current_strip_val > 0 && current_strip_val + val_here > 0) { // Extend if sum remains positive
                    current_strip_val += val_here;
                } else { // Start new subarray
                    current_strip_val = val_here;
                    current_r1_1d = r2_idx_1d;
                }
                
                if (current_strip_val > max_so_far) {
                    max_so_far = current_strip_val;
                    best_r1 = current_r1_1d;
                    best_r2 = r2_idx_1d;
                    best_c1 = c1_idx;
                    best_c2 = c2_idx;
                }
            }
        }
    }
    
    std::vector<Point> default_poly = {{0,0}, {1,0}, {1,1}, {0,1}}; // Minimal valid polygon

    // If no positive sum found, or issue, find best single cell
    if (best_r2 == -1 || max_so_far <=0 ) { 
        max_so_far = -3e18; // Reset search for single best cell
        bool found_cell = false;
        for(int r=0; r<GRID_SIZE_KADANE; ++r) for(int c=0; c<GRID_SIZE_KADANE; ++c) {
            if(grid_scores[r][c] > max_so_far) { 
                max_so_far = grid_scores[r][c];
                best_r1 = r; best_r2 = r; // Single cell
                best_c1 = c; best_c2 = c;
                found_cell = true;
            }
        }
        if (!found_cell || max_so_far <=0) return default_poly; // Still no good cell, return default
    }

    // Convert grid cell indices to actual coordinates
    int x_start = best_c1 * ACTUAL_CELL_DIM_KADANE;
    int y_start = best_r1 * ACTUAL_CELL_DIM_KADANE;
    int x_end = (best_c2 + 1) * ACTUAL_CELL_DIM_KADANE -1; 
    int y_end = (best_r2 + 1) * ACTUAL_CELL_DIM_KADANE -1; 
    
    // Clamp coordinates to valid range
    x_start = std::max(0, std::min(MAX_COORD_VAL, x_start));
    y_start = std::max(0, std::min(MAX_COORD_VAL, y_start));
    x_end = std::max(x_start, std::min(MAX_COORD_VAL, x_end)); // Ensure x_end >= x_start
    y_end = std::max(y_start, std::min(MAX_COORD_VAL, y_end)); // Ensure y_end >= y_start
    
    // Ensure non-zero dimensions for the polygon, minimum 1x1 actual area
    if (x_start == x_end) {
        if (x_start < MAX_COORD_VAL) x_end = x_start + 1;
        else if (x_start > 0) x_start = x_start -1; // Can't expand right, try expand left
        else return default_poly; // Single point at MAX_COORD_VAL, cannot form 1x1
    }
     if (y_start == y_end) {
        if (y_start < MAX_COORD_VAL) y_end = y_start + 1;
        else if (y_start > 0) y_start = y_start - 1;
        else return default_poly;
    }
    // After adjustment, if still degenerate, use default. This is rare.
    if (x_start == x_end || y_start == y_end) return default_poly;

    
    std::vector<Point> initial_poly = {
        {x_start, y_start}, {x_end, y_start}, {x_end, y_end}, {x_start, y_end}
    };
    return initial_poly;
}

// === SIMULATED ANNEALING ===
struct SAState {
    std::vector<Point> poly;
    int m_count; 
    int s_count; 

    SAState() : m_count(0), s_count(0) {}

    long long get_objective_score() const { 
        return std::max(0LL, (long long)m_count - s_count + 1);
    }
    double get_raw_objective_score() const { // Used for SA acceptance probability
        return (double)m_count - s_count;
    }
};

// Calculates signed area * 2 of a polygon (shoelace formula)
long long polygon_signed_area_times_2(const std::vector<Point>& poly) {
    if (poly.size() < 3) return 0;
    long long area_sum = 0;
    for (size_t i = 0; i < poly.size(); ++i) {
        const Point& p1 = poly[i];
        const Point& p2 = poly[(i + 1) % poly.size()];
        area_sum += (long long)(p1.x - p2.x) * (p1.y + p2.y); // (x1-x2)(y1+y2) variant
    }
    return area_sum; // Positive for CCW, negative for CW
}

std::vector<int> sa_critical_edge_indices_cache; // Cache for local intersection check

// Guide coordinates for SA moves
std::vector<int> static_x_guides; 
std::vector<int> static_y_guides; 
std::vector<int> best_poly_x_guides; 
std::vector<int> best_poly_y_guides; 

void update_best_poly_guides(const SAState& new_best_state) {
    best_poly_x_guides.clear();
    best_poly_y_guides.clear();
    if (new_best_state.poly.empty()) return;

    std::set<int> temp_x_set, temp_y_set;
    for (const auto& p : new_best_state.poly) {
        temp_x_set.insert(p.x);
        temp_y_set.insert(p.y);
    }
    best_poly_x_guides.assign(temp_x_set.begin(), temp_x_set.end());
    best_poly_y_guides.assign(temp_y_set.begin(), temp_y_set.end());
}


void simulated_annealing_main() {
    SAState current_state;
    current_state.poly = create_initial_polygon_kadane();
    calculate_score_from_scratch(current_state.poly, current_state.m_count, current_state.s_count);
    
    std::vector<Point> default_tiny_poly = {{0,0}, {1,0}, {1,1}, {0,1}};

    // Ensure initial polygon is valid, otherwise use default
    bool current_poly_initial_valid = is_polygon_structurally_sound(current_state.poly) &&
                                      current_state.poly.size() >= 4 && 
                                      has_distinct_vertices_unordered(current_state.poly) &&
                                      !check_self_intersection_full(current_state.poly);

    if (!current_poly_initial_valid) { 
         current_state.poly = default_tiny_poly; 
         calculate_score_from_scratch(current_state.poly, current_state.m_count, current_state.s_count);
    }

    SAState best_state = current_state;
    update_best_poly_guides(best_state); 
    
    // Prepare static guide coordinates from fish locations
    std::set<int> sx_set, sy_set; 
    for(const auto& f_s : all_fish_structs) { 
        sx_set.insert(f_s.p.x); sx_set.insert(std::max(0,f_s.p.x-1)); sx_set.insert(std::min(MAX_COORD_VAL, f_s.p.x+1));
        sy_set.insert(f_s.p.y); sy_set.insert(std::max(0,f_s.p.y-1)); sy_set.insert(std::min(MAX_COORD_VAL, f_s.p.y+1));
    }
    sx_set.insert(0); sx_set.insert(MAX_COORD_VAL); // Boundary guides
    sy_set.insert(0); sy_set.insert(MAX_COORD_VAL);

    static_x_guides.assign(sx_set.begin(), sx_set.end()); 
    static_y_guides.assign(sy_set.begin(), sy_set.end());


    double start_temp = 150.0; 
    double end_temp = 0.01;    
    
    long long current_signed_area = polygon_signed_area_times_2(current_state.poly);
    if (current_signed_area == 0 && current_state.poly.size() >=3) { 
         current_signed_area = 1; // Avoid issues with zero area for sign logic
    }

    sa_critical_edge_indices_cache.reserve(10); // Max expected critical edges for current moves

    while (global_timer.elapsed() < ACTUAL_TIME_LIMIT_SECONDS) {
        double time_ratio = global_timer.elapsed() / ACTUAL_TIME_LIMIT_SECONDS;
        double temperature = start_temp * std::pow(end_temp / start_temp, time_ratio);
        // Fine-tune temperature near end or if it drops too fast
        if (temperature < end_temp && time_ratio < 0.95) temperature = end_temp; 
        if (time_ratio > 0.95 && temperature > end_temp * 0.1) temperature = end_temp * 0.1; // Lower temp aggressively at the very end
        
        if (current_state.poly.size() < 4) { // Should not happen if logic is correct, but as a safeguard
            current_state.poly = default_tiny_poly; 
            calculate_score_from_scratch(current_state.poly, current_state.m_count, current_state.s_count);
            current_signed_area = polygon_signed_area_times_2(current_state.poly);
            if (current_signed_area == 0 && current_state.poly.size() >=3) current_signed_area = 1;
        }

        SAState candidate_state = current_state; 
        sa_critical_edge_indices_cache.clear();
        
        int move_type_roll = rng.next_int(100);
        // Base probabilities for moves
        int move_edge_prob = 48;
        int add_bulge_prob = 24; 
        // Remaining probability for simplify polygon move
        
        long long current_poly_perimeter_cached = 0; 
        bool check_limits = (candidate_state.poly.size() > 200 || candidate_state.poly.size() > MAX_VERTICES - 20);
        if (check_limits && candidate_state.poly.size() > 200) { 
            // Only calculate perimeter if near limits and already large, it's somewhat expensive
            current_poly_perimeter_cached = calculate_perimeter(candidate_state.poly);
        }

        // Adjust move probabilities based on polygon size/perimeter
        if (candidate_state.poly.size() + 2 > MAX_VERTICES || (check_limits && current_poly_perimeter_cached > MAX_PERIMETER * 0.95)) { // If adding bulge would exceed max vertices
            move_edge_prob = 45; add_bulge_prob = 0; // Heavily restrict adding vertices
        } else if (candidate_state.poly.size() > 200 || (check_limits && current_poly_perimeter_cached > MAX_PERIMETER * 0.9)) {
            move_edge_prob = 40; add_bulge_prob = 15;
        } else if (candidate_state.poly.size() > 50) {
            move_edge_prob = 45; add_bulge_prob = 20;
        }

        bool move_made = false;
        
        // Probabilities for snapping to guide coordinates
        double prob_dynamic_guide_snap = 0.20 + 0.20 * time_ratio; 
        double prob_static_guide_snap_if_not_dynamic = 0.75; 

        if (move_type_roll < move_edge_prob && candidate_state.poly.size() >= 4 ) { // Move Edge
            int edge_idx = rng.next_int(candidate_state.poly.size());
            Point p1_orig = candidate_state.poly[edge_idx]; 
            Point p2_orig = candidate_state.poly[(edge_idx + 1) % candidate_state.poly.size()];
            
            int new_coord_val = -1; 
            int cur_delta_m=0, cur_delta_s=0;
            bool coord_selected_successfully = false;
            
            // Determine which guides are relevant (X or Y)
            const std::vector<int>* relevant_dyn_guides = (p1_orig.x == p2_orig.x) ? &best_poly_x_guides : &best_poly_y_guides;
            const std::vector<int>* relevant_static_guides = (p1_orig.x == p2_orig.x) ? &static_x_guides : &static_y_guides;

            // Try snapping to dynamic (best poly) guides
            if (!relevant_dyn_guides->empty() && rng.next_double() < prob_dynamic_guide_snap) {
                new_coord_val = (*relevant_dyn_guides)[rng.next_int(relevant_dyn_guides->size())];
                coord_selected_successfully = true;
            }
            // If not, try snapping to static (fish) guides
            if (!coord_selected_successfully) {
                if (!relevant_static_guides->empty() && rng.next_double() < prob_static_guide_snap_if_not_dynamic) {
                    new_coord_val = (*relevant_static_guides)[rng.next_int(relevant_static_guides->size())];
                    coord_selected_successfully = true;
                }
            }
            // If still not selected, use random displacement
            if (!coord_selected_successfully) { 
                double step_factor = std::max(0.1, 1.0 - time_ratio * 0.95); // Step size decreases over time
                int base_step_max = std::max(1, (int)( (MAX_COORD_VAL/150.0) * step_factor + 1 ) ); 
                int random_displacement = rng.next_int(-base_step_max, base_step_max);
                if (time_ratio > 0.75 && rng.next_double() < 0.7) { // Very small steps near end
                    random_displacement = rng.next_int(-2,2); 
                }
                if (random_displacement == 0) random_displacement = (rng.next_double() < 0.5) ? -1:1; 
                
                if (p1_orig.x == p2_orig.x) new_coord_val = p1_orig.x + random_displacement; // Vertical edge, move X
                else new_coord_val = p1_orig.y + random_displacement; // Horizontal edge, move Y
            }

            new_coord_val = std::max(0, std::min(MAX_COORD_VAL, new_coord_val)); // Clamp to bounds

            if (p1_orig.x == p2_orig.x) { // Vertical edge: (X_orig, Y_s) to (X_orig, Y_e)
                if (new_coord_val == p1_orig.x) {move_made = false; goto end_move_attempt_label;} // No change
                
                int query_min_x, query_max_x;
                if (new_coord_val > p1_orig.x) { // Moved right
                    query_min_x = p1_orig.x + 1;
                    query_max_x = new_coord_val;
                } else { // Moved left (new_coord_val < p1_orig.x)
                    query_min_x = new_coord_val;
                    query_max_x = p1_orig.x - 1;
                }
                
                calculate_score_delta_for_rectangle(
                    query_min_x, query_max_x, 
                    std::min(p1_orig.y, p2_orig.y), std::max(p1_orig.y, p2_orig.y),
                    cur_delta_m, cur_delta_s);

                int sign = (new_coord_val > p1_orig.x) ? 1 : -1; // Moving right is positive X change
                if (p1_orig.y > p2_orig.y) sign *= -1; // Correct for edge Y-direction (p1_orig.y to p2_orig.y)
                if (current_signed_area < 0) sign *= -1; // Correct for CW polygon (area < 0)
                
                candidate_state.poly[edge_idx].x = new_coord_val; 
                candidate_state.poly[(edge_idx + 1) % candidate_state.poly.size()].x = new_coord_val;
                candidate_state.m_count += sign * cur_delta_m;
                candidate_state.s_count += sign * cur_delta_s;
            } else { // Horizontal edge: (X_s, Y_orig) to (X_e, Y_orig)
                if (new_coord_val == p1_orig.y) {move_made = false; goto end_move_attempt_label;} // No change

                int query_min_y, query_max_y;
                if (new_coord_val > p1_orig.y) { // Moved up (Y increases)
                    query_min_y = p1_orig.y + 1;
                    query_max_y = new_coord_val;
                } else { // Moved down (Y decreases, new_coord_val < p1_orig.y)
                    query_min_y = new_coord_val;
                    query_max_y = p1_orig.y - 1;
                }
                
                calculate_score_delta_for_rectangle(
                    std::min(p1_orig.x, p2_orig.x), std::max(p1_orig.x, p2_orig.x), 
                    query_min_y, query_max_y,
                    cur_delta_m, cur_delta_s);
                
                int sign = (new_coord_val < p1_orig.y) ? 1 : -1; // Moving "down" (Y decreases) means positive sign if it expands area
                if (p1_orig.x > p2_orig.x) sign *= -1; // Correct for edge X-direction (p1_orig.x to p2_orig.x)
                if (current_signed_area < 0) sign *= -1; // Correct for CW polygon
                
                candidate_state.poly[edge_idx].y = new_coord_val; 
                candidate_state.poly[(edge_idx + 1) % candidate_state.poly.size()].y = new_coord_val;
                candidate_state.m_count += sign * cur_delta_m;
                candidate_state.s_count += sign * cur_delta_s;
            }
            int M_cand = candidate_state.poly.size();
            sa_critical_edge_indices_cache.push_back((edge_idx - 1 + M_cand) % M_cand);
            sa_critical_edge_indices_cache.push_back(edge_idx);
            sa_critical_edge_indices_cache.push_back((edge_idx + 1) % M_cand);
            move_made = true;

        } else if (move_type_roll < move_edge_prob + add_bulge_prob && candidate_state.poly.size() + 2 <= MAX_VERTICES && candidate_state.poly.size() >=4) { // Add Bulge
            int edge_idx = rng.next_int(candidate_state.poly.size());
            Point p_s = candidate_state.poly[edge_idx]; // Start point of edge
            Point p_e = candidate_state.poly[(edge_idx + 1) % candidate_state.poly.size()]; // End point of edge
            
            int new_coord_val = -1; 
            bool coord_selected_successfully = false;

            const std::vector<int>* relevant_dyn_guides = (p_s.x == p_e.x) ? &best_poly_x_guides : &best_poly_y_guides;
            const std::vector<int>* relevant_static_guides = (p_s.x == p_e.x) ? &static_x_guides : &static_y_guides;

            // Try snapping bulge coord
            if (!relevant_dyn_guides->empty() && rng.next_double() < prob_dynamic_guide_snap) {
                new_coord_val = (*relevant_dyn_guides)[rng.next_int(relevant_dyn_guides->size())];
                coord_selected_successfully = true;
            }
            if (!coord_selected_successfully) {
                if (!relevant_static_guides->empty() && rng.next_double() < prob_static_guide_snap_if_not_dynamic) { 
                    new_coord_val = (*relevant_static_guides)[rng.next_int(relevant_static_guides->size())];
                    coord_selected_successfully = true;
                }
            }
            // If not snapped, random depth for bulge
            if (!coord_selected_successfully) { 
                double depth_factor = std::max(0.1, 1.0 - time_ratio * 0.9); 
                int base_depth_max = std::max(1, (int)( (MAX_COORD_VAL/300.0) * depth_factor + 1 ) ); 
                int random_abs_depth = rng.next_int(1, base_depth_max);
                if (time_ratio > 0.75 && rng.next_double() < 0.7) { 
                    random_abs_depth = rng.next_int(1,2); 
                }
                int bulge_dir_sign = (rng.next_double() < 0.5) ? 1 : -1; // Randomly outwards or inwards relative to edge line
                if (p_s.x == p_e.x) new_coord_val = p_s.x + bulge_dir_sign * random_abs_depth; // Vertical edge, bulge in X
                else new_coord_val = p_s.y + bulge_dir_sign * random_abs_depth; // Horizontal edge, bulge in Y
            }
            
            new_coord_val = std::max(0, std::min(MAX_COORD_VAL, new_coord_val));

            Point v1_mod, v2_mod; // New vertices for the bulge
            int cur_delta_m=0, cur_delta_s=0;
            
            if (p_s.x == p_e.x) { // Original edge is vertical
                if (new_coord_val == p_s.x) {move_made = false; goto end_move_attempt_label;} // Bulge is flat
                v1_mod = {new_coord_val, p_s.y}; v2_mod = {new_coord_val, p_e.y}; 
                // Rectangle for delta score is between X=p_s.x and X=new_coord_val, over Y-span of original edge
                calculate_score_delta_for_rectangle(
                    std::min(p_s.x, new_coord_val), std::max(p_s.x, new_coord_val), 
                    std::min(p_s.y,p_e.y), std::max(p_s.y,p_e.y), 
                    cur_delta_m, cur_delta_s);
                int sign = (new_coord_val > p_s.x) ? 1 : -1; // Bulge to the right of edge is positive X change
                if (p_s.y > p_e.y) sign *= -1; // Correct for edge Y-direction
                if (current_signed_area < 0) sign *= -1; // Correct for CW polygon
                candidate_state.m_count += sign * cur_delta_m;
                candidate_state.s_count += sign * cur_delta_s;
            } else { // Original edge is horizontal
                if (new_coord_val == p_s.y) {move_made = false; goto end_move_attempt_label;} // Bulge is flat
                v1_mod = {p_s.x, new_coord_val}; v2_mod = {p_e.x, new_coord_val};
                // Rectangle for delta score is between Y=p_s.y and Y=new_coord_val, over X-span of original edge
                calculate_score_delta_for_rectangle(
                    std::min(p_s.x,p_e.x), std::max(p_s.x,p_e.x), 
                    std::min(p_s.y, new_coord_val), std::max(p_s.y, new_coord_val), 
                    cur_delta_m, cur_delta_s);
                int sign = (new_coord_val < p_s.y) ? 1 : -1; // Bulge "downwards" (Y decreases) means positive sign if it expands area
                if (p_s.x > p_e.x) sign *= -1; // Correct for edge X-direction
                if (current_signed_area < 0) sign *= -1; // Correct for CW polygon
                candidate_state.m_count += sign * cur_delta_m;
                candidate_state.s_count += sign * cur_delta_s;
            }
            
            // Insert new vertices into polygon
            auto insert_pos_iter = candidate_state.poly.begin() + (edge_idx + 1);
            insert_pos_iter = candidate_state.poly.insert(insert_pos_iter, v1_mod); 
            candidate_state.poly.insert(insert_pos_iter + 1, v2_mod); 
            
            // Mark affected edges/vertices as critical for local intersection check
            sa_critical_edge_indices_cache.push_back(edge_idx); 
            sa_critical_edge_indices_cache.push_back(edge_idx + 1); 
            sa_critical_edge_indices_cache.push_back(edge_idx + 2); 
            move_made = true;

        } else if (candidate_state.poly.size() > 4) { // Simplify Polygon (remove collinear vertex)
            int R_start_idx = rng.next_int(candidate_state.poly.size()); // Random start for search
            bool simplified_this_turn = false;
            for(int k_offset=0; k_offset < candidate_state.poly.size() ; ++k_offset) { 
                int current_poly_size_before_erase = candidate_state.poly.size();
                if (current_poly_size_before_erase <= 4) break; // Cannot simplify further
                
                int p1_idx = (R_start_idx + k_offset) % current_poly_size_before_erase;
                int p0_idx_old = (p1_idx - 1 + current_poly_size_before_erase) % current_poly_size_before_erase;
                int p2_idx_old = (p1_idx + 1) % current_poly_size_before_erase;

                const Point& p0 = candidate_state.poly[p0_idx_old]; 
                const Point& p1 = candidate_state.poly[p1_idx]; 
                const Point& p2 = candidate_state.poly[p2_idx_old];

                bool collinear_x = (p0.x == p1.x && p1.x == p2.x);
                bool collinear_y = (p0.y == p1.y && p1.y == p2.y);

                if (collinear_x || collinear_y) { 
                    candidate_state.poly.erase(candidate_state.poly.begin() + p1_idx);
                    simplified_this_turn = true;
                    
                    int M_cand = candidate_state.poly.size();
                    int critical_vertex_idx_in_new_poly;
                    // Vertex p0 (at p0_idx_old) forms the new corner. Its index in new poly:
                    if (p1_idx == 0) { // If p1 was poly[0], p0 was poly[last]. p0 is now poly[new_last]
                        critical_vertex_idx_in_new_poly = M_cand -1; 
                    } else { // Otherwise, p0's index p1_idx-1 is preserved.
                        critical_vertex_idx_in_new_poly = p1_idx - 1; 
                    }

                    if (!candidate_state.poly.empty()) { 
                        sa_critical_edge_indices_cache.push_back((critical_vertex_idx_in_new_poly - 1 + M_cand) % M_cand);
                        sa_critical_edge_indices_cache.push_back(critical_vertex_idx_in_new_poly); 
                        sa_critical_edge_indices_cache.push_back((critical_vertex_idx_in_new_poly + 1) % M_cand); 
                    }
                    break; // Simplified one vertex, enough for this turn
                }
            }
            if (!simplified_this_turn) {move_made = false; goto end_move_attempt_label;} // No simplification found/possible
            move_made = true;
        }
        
        end_move_attempt_label:; // Label for goto if a move is aborted (e.g. no change)
        if (!move_made) continue; // No valid move attempted or made

        // Validate candidate polygon
        if (!is_polygon_structurally_sound(candidate_state.poly) || candidate_state.poly.size() < 4 ||
            !has_distinct_vertices_unordered(candidate_state.poly)) {
            continue; // Invalid basic structure or duplicate vertices
        }
        
        if (has_self_intersection_locally(candidate_state.poly, sa_critical_edge_indices_cache)) {
             continue; // Self-intersection found
        }
        
        // Accept or reject candidate based on SA criteria
        double candidate_raw_obj_score = candidate_state.get_raw_objective_score();
        double current_raw_obj_score = current_state.get_raw_objective_score();
        double score_diff = candidate_raw_obj_score - current_raw_obj_score;

        if (score_diff >= 0 || (temperature > 1e-9 && rng.next_double() < std::exp(score_diff / temperature))) {
            current_state = std::move(candidate_state); // Accept move
            current_signed_area = polygon_signed_area_times_2(current_state.poly); // Update signed area
             if (current_signed_area == 0 && !current_state.poly.empty() && current_state.poly.size() >=3) current_signed_area = 1; // Handle degenerate
            
            if (current_state.get_objective_score() > best_state.get_objective_score()) {
                best_state = current_state; // New best solution found
                update_best_poly_guides(best_state); // Update dynamic guides
            }
        }
    } // End SA loop
    
    // Final validation of the best found state
    bool needs_reset_to_default = false;
    if (!is_polygon_structurally_sound(best_state.poly) || 
        best_state.poly.size() < 4 || 
        !has_distinct_vertices_unordered(best_state.poly) ||
        check_self_intersection_full(best_state.poly) ) { // Full intersection check on best
        needs_reset_to_default = true;
    }

    if (needs_reset_to_default) { // If best state is invalid, revert to default
        best_state.poly = default_tiny_poly;
        calculate_score_from_scratch(best_state.poly, best_state.m_count, best_state.s_count);
    }
    
    // If best score is 0, check if default polygon gives >0. (max(0, val+1))
    // The score is max(0, M-S+1). So if M-S = -1, score is 0. If M-S = 0, score is 1.
    // If best_state.get_objective_score() == 0, it means M-S+1 <= 0, so M-S <= -1.
    // Default polygon has M=0, S=0, so M-S+1 = 1. Score is 1.
    // So, if best_state score is 0, default is always better (score 1) or equal (if default also somehow gets 0).
    if (best_state.get_objective_score() == 0) { 
        // This case implies M-S <= -1 for best_state. Default gives score 1.
        // It's possible that the problem setter implies an empty polygon is not allowed or scores 0.
        // The problem implies outputting a polygon. The default_tiny_poly is a valid polygon.
        // The current logic already handles falling back to default_tiny_poly if the Kadane one is invalid.
        // This check ensures if SA ends up with a 0-score polygon (e.g. captures many sardines),
        // we check if the basic tiny square is better.
        SAState temp_default_state; // Create a temporary default state to calculate its score
        temp_default_state.poly = default_tiny_poly;
        calculate_score_from_scratch(temp_default_state.poly, temp_default_state.m_count, temp_default_state.s_count);
        // If the objectively computed score of the best_state is less than the default one, use default.
        // This is useful if best_state.get_objective_score() became 0 due to M-S+1 <= 0, while default_tiny_poly has M-S+1=1.
        if (best_state.get_objective_score() < temp_default_state.get_objective_score()) {
             best_state = temp_default_state;
        }
    }


    // Output the best polygon
    std::cout << best_state.poly.size() << "\n";
    for (const auto& p : best_state.poly) {
        std::cout << p.x << " " << p.y << "\n";
    }
}


int main(int argc, char *argv[]) {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(NULL);

    // Allow overriding time limit via command line arg, for local testing
    if (argc > 1) { 
        try {
            ACTUAL_TIME_LIMIT_SECONDS = std::stod(argv[1]);
        } catch (const std::exception& e) { /* keep default if parse fails */ }
    }
    ACTUAL_TIME_LIMIT_SECONDS -= TIME_LIMIT_SECONDS_SAFETY_MARGIN;
    if (ACTUAL_TIME_LIMIT_SECONDS < 0.2) ACTUAL_TIME_LIMIT_SECONDS = 0.2; // Minimum sensible time limit

    query_rect_indices_cache_kdtree.reserve(2 * 5000 + 500); // N_half max is 5000
    sa_critical_edge_indices_cache.reserve(10); // Small, for a few critical edges


    int N_half; // Number of mackerels (and sardines)
    std::cin >> N_half; 

    all_fish_structs.resize(2 * N_half);
    std::vector<int> fish_indices_for_kdtree(2 * N_half);
    if (2 * N_half > 0) { 
        std::iota(fish_indices_for_kdtree.begin(), fish_indices_for_kdtree.end(), 0); 
    }

    // Read mackerels
    for (int i = 0; i < N_half; ++i) {
        std::cin >> all_fish_structs[i].p.x >> all_fish_structs[i].p.y;
        all_fish_structs[i].type = 1; 
    }
    // Read sardines
    for (int i = 0; i < N_half; ++i) {
        std::cin >> all_fish_structs[N_half + i].p.x >> all_fish_structs[N_half + i].p.y;
        all_fish_structs[N_half + i].type = -1; 
    }
    
    // Build KD-tree if there are fish
    if (!all_fish_structs.empty()) {
      fish_kdtree_root = build_kdtree(fish_indices_for_kdtree, 0, (int)all_fish_structs.size() - 1, 0);
    }
    
    simulated_annealing_main(); 
    
    // Clean up KD-tree memory
    if (fish_kdtree_root) delete_kdtree(fish_kdtree_root); 

    return 0;
}
