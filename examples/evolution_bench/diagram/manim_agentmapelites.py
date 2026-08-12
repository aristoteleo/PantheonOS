"""Agent MAP-Elites, as a narrated run.

    manim -qm --format=mp4 manim_agentmapelites.py AgentMapElitesRun

**The tree is the protagonist.** Evolution is a tree growing; the MAP-Elites grid is the rule for
where it grows next; the agent is what does the growing -- the method is named for those last two.
Every structure holds one fixed position for the whole video: loop top-left, grids bottom-left,
tree right, curve bottom.

The tree RE-LAYS ITSELF as it grows: each step recomputes the tidy layout over what exists so far
and every node glides to its new place, so the root starts centred and the tree stays balanced.

Four acts:

  1. one full turn of the loop, slowly, all four structures updating in sync; then turns at
     speed, every one showing its selection (the drawn cell and the parent node flash first).
  2. the grid's bookkeeping: metric axes, a node projecting into its cell, the run's real
     admission cases, and what selection means over the cells now filled.
  3. islands. The method runs SEVERAL copies of the grid. With one seed, island 2 starts empty --
     children inherit their parent's island -- and is colonised by the first migration, which the
     recording supplies right here. Same niche, two islands, two representatives, two votes.
  4. the rest of the run at speed, both grids filling, closing on best-vs-coverage.

The run is real: `sim_agentmapelites` drives the actual `AgentMapElites` method through the actual
loop and records its decisions. The landscape and the mutation are invented -- which is also what
the title card's small print says, because a viewer should not have to read source to learn it.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
from manim import (DL, DOWN, DR, LEFT, ORIGIN, RIGHT, UL, UP, UR, Axes, Circle, Create,
                   DashedLine, Dot, FadeIn, FadeOut, GrowFromPoint, Indicate, LaggedStart, Line,
                   MoveAlongPath, MovingCameraScene, RoundedRectangle, Square, Transform, VGroup,
                   VMobject, Write)

from manim_kit import (BLUE, EDGE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, PURPLE, RED,
                       SUB, arc, dim, fit, para, score_color, spoke, txt)
from sim_agentmapelites import BINS, EVENTS, ISLANDS, PLACES

LO, HI = 0.25, 0.75                      # the score range every colour in the video spans

# ---- the fixed stage ------------------------------------------------------
LOOP_AT = {"select": np.array([-5.75, 1.75, 0.0]), "mutate": np.array([-3.95, 3.05, 0.0]),
           "evaluate": np.array([-2.15, 1.75, 0.0]), "record": np.array([-3.95, 0.45, 0.0])}
BOX_W, BOX_H = 2.55, 0.95
"""Equal boxes. Four different widths read as four different kinds of thing, and they are four
stations of one loop."""
GRID_C = {0: np.array([-5.25, -1.72, 0.0]), 1: np.array([-2.95, -1.72, 0.0])}
GRID_CELL = 0.34
"""Two grids side by side, sized so BOTH fit under the loop -- island 2's spot is reserved from
the first frame even though nothing appears there until the migration act, so nothing has to
shuffle when it arrives."""
ISLAND_COLOR = {0: EDGE, 1: PURPLE}
CURVE_C = np.array([3.35, -2.75, 0.0])
CAP_AT = np.array([-3.6, -3.42, 0.0])
TREE_CX, TREE_TOP, TREE_DY, TREE_R = 3.45, 3.0, 0.5, 0.155

BY_ID = {e["id"]: e for e in PLACES}


def tree_layout(events):
    """Tidy layout over WHATEVER EXISTS SO FAR: each subtree owns a contiguous span of leaf
    slots, every parent sits centred over its children, and the whole thing is centred on
    TREE_CX. Recomputed every step so the root starts centred and the tree stays balanced."""
    children = defaultdict(list)
    for e in events:
        children[e.get("parent")].append(e["id"])
    root = events[0]["id"]

    xs, depths, next_slot = {}, {}, [0]

    def place(nid, depth):
        depths[nid] = depth
        kids = children.get(nid, [])
        if not kids:
            xs[nid] = float(next_slot[0])
            next_slot[0] += 1
            return
        for k in kids:
            place(k, depth + 1)
        xs[nid] = sum(xs[k] for k in kids) / len(kids)

    place(root, 0)
    n_leaves = next_slot[0]
    dx = min(0.52, 6.4 / max(1, n_leaves - 1))
    x0 = TREE_CX - dx * (n_leaves - 1) / 2
    return {nid: np.array([x0 + xs[nid] * dx, TREE_TOP - depths[nid] * TREE_DY, 0.0])
            for nid in xs}


def tree_dot(ev, pos, lit=False, island=None) -> Circle:
    """Filled if it holds a cell in the grid, hollow if it was measured and is not a
    representative -- the tree keeps both. The ring colour carries the ISLAND, so a lineage that
    migrated is visible on the tree itself."""
    isl = ev["island"] if island is None else island
    base = ISLAND_COLOR.get(isl, EDGE) if ev["admitted"] else MUTED
    return Circle(radius=TREE_R if ev["admitted"] else TREE_R * 0.7,
                  stroke_width=2.6 if lit else (2.0 if ev["admitted"] else 1.6),
                  color=GREEN if lit else base,
                  fill_color=score_color(ev["score"], LO, HI),
                  fill_opacity=1.0 if ev["admitted"] else 0.0).move_to(pos)


def edge_line(a, b, island=0) -> Line:
    d = b - a
    u = d / max(float(np.linalg.norm(d)), 1e-6)
    return Line(a + u * (TREE_R + 0.02), b - u * (TREE_R + 0.02),
                color=ISLAND_COLOR.get(island, EDGE),
                stroke_width=1.5).set_stroke(opacity=0.8)


def cell_center(island, cell) -> np.ndarray:
    return GRID_C[island] + np.array([(cell[0] - (BINS - 1) / 2) * GRID_CELL,
                                      (cell[1] - (BINS - 1) / 2) * GRID_CELL, 0.0])


def grid_square(island, cell, score=None, lit=False) -> Square:
    return Square(side_length=GRID_CELL, stroke_width=2.6 if lit else 1.2,
                  color=GREEN if lit else EDGE,
                  fill_color=PANEL if score is None else score_color(score, LO, HI),
                  fill_opacity=1.0).move_to(cell_center(island, cell))


def blank_grid(island) -> VGroup:
    return VGroup(*[grid_square(island, (cx, cy)) for cx in range(BINS) for cy in range(BINS)])


def loop_box(key: str, top: str, sub: str) -> VGroup:
    body = VGroup(txt(top, 20, INK), txt(sub, 13.5, SUB)).arrange(DOWN, buff=0.09)
    frame = RoundedRectangle(width=BOX_W, height=BOX_H, corner_radius=0.13, stroke_width=1.7,
                             color=EDGE, fill_color=PANEL, fill_opacity=1.0)
    return VGroup(frame, body).move_to(LOOP_AT[key])


def rect_anchor(box, other, pad=0.08) -> np.ndarray:
    """Where the centre-to-centre line leaves `box` -- so every arrow sits exactly in the gap
    between its two boxes instead of swinging out from an edge midpoint."""
    c, o = box.get_center(), other.get_center()
    d = o - c
    u = d / max(float(np.linalg.norm(d)), 1e-6)
    t = min(BOX_W / 2 / abs(u[0]) if abs(u[0]) > 1e-6 else 1e9,
            BOX_H / 2 / abs(u[1]) if abs(u[1]) > 1e-6 else 1e9)
    return c + u * (t + pad)


def ring_arrow(a_box, b_box):
    return spoke(rect_anchor(a_box, b_box), rect_anchor(b_box, a_box), SUB, 2.6,
                 0.0, 0.0, tip=0.16)


class AgentMapElitesRun(Kit, MovingCameraScene):

    def construct(self):
        title = txt("Agent MAP-Elites", 54, INK, weight="BOLD")
        sub = para("evolution is a tree, growing — a MAP-Elites grid picks where,\n"
                   "and a coding agent does the growing", 27, SUB)
        fine = txt("simulated landscape · real method decisions", 16, MUTED)
        card = VGroup(title, sub, fine).arrange(DOWN, buff=0.42)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), FadeIn(fine), run_time=1.4)
        self.wait(2.0)
        self.play(FadeOut(card), run_time=0.9)

        self.tree_mobs: dict = {}
        self.edge_mobs: dict = {}
        self.cells: dict = {}                  # (island, cell) -> Square
        self.curve_dots, self.best_pts = VGroup(), []
        self.caption = None
        self.ptr = 0                           # index into EVENTS
        self.n_places = 0
        self.migrations_seen = 0
        self.island1_open = False

        self.act_one()
        self.act_two()
        self.act_rest()                        # islands act fires when the recording says so

    # ---- shared drawing ---------------------------------------------------
    def say(self, text: str, size=22, color=INK, hold=0.0):
        new = para(text, size, color) if "\n" in text else txt(text, size, color)
        fit(new, 6.6).move_to(CAP_AT)
        if self.caption is None:
            self.caption = new
            self.play(FadeIn(new), run_time=0.5)
        else:
            self.play(Transform(self.caption, new), run_time=0.5)
        if hold:
            self.wait(hold)

    def drop_caption(self):
        if self.caption is not None:
            self.play(FadeOut(self.caption), run_time=0.4)
            self.caption = None

    def tree_anims(self, ev, lit=False):
        """Add the child AND glide everything already there to the new layout."""
        self.n_places += 1
        pos = tree_layout(PLACES[: self.n_places])
        anims = []
        for nid, mob in self.tree_mobs.items():
            if float(np.linalg.norm(mob.get_center() - pos[nid])) > 1e-3:
                anims.append(mob.animate.move_to(pos[nid]))
        for (pa, ch), mob in self.edge_mobs.items():
            anims.append(Transform(mob, edge_line(pos[pa], pos[ch], BY_ID[ch]["island"])))

        dot = tree_dot(ev, pos[ev["id"]], lit=lit)
        self.tree_mobs[ev["id"]] = dot
        pid = ev.get("parent")
        if pid in self.tree_mobs:
            edge = edge_line(pos[pid], pos[ev["id"]], ev["island"])
            self.edge_mobs[(pid, ev["id"])] = edge
            anims.append(Create(edge))
        anims.append(FadeIn(dot, scale=0.5))
        return anims

    def grid_anims(self, ev, lit=False):
        """Diff the WHOLE recorded snapshot against what is on screen.

        Updating only the child's cell was enough with one island; it is not any more. A range
        widening rebuilds every bin and a migration refiles individuals, so cells other than the
        child's appear, change and VANISH under any event. The snapshot is the truth; the screen
        follows it.
        """
        anims = []
        for key, (_, score) in ev["grid"].items():
            if key[0] == 1 and not self.island1_open:
                continue                       # island 2 is introduced by its own act
            is_child = key == (ev.get("island"), ev.get("cell"))
            sq = grid_square(*key, score, lit=lit and is_child)
            shown = self.cells.get(key)
            if shown is None:
                self.cells[key] = sq
                anims.append(FadeIn(sq, scale=0.6))
            elif shown.get_fill_color().to_hex() != sq.get_fill_color().to_hex() \
                    or (lit and is_child):
                anims.append(Transform(shown, sq))
        for key in [k for k in self.cells if k not in ev["grid"]]:
            anims.append(FadeOut(self.cells.pop(key)))
        return anims

    def curve_anims(self, ev):
        i = len(self.best_pts)
        p = self.ax.c2p(i + 1, ev["score"])
        d = Dot(p, radius=0.045, color=BLUE).set_opacity(0.65)
        self.curve_dots.add(d)
        anims = [FadeIn(d, scale=0.4)]
        bp = self.ax.c2p(i + 1, ev["best"])
        if self.best_pts:
            seg = Line(self.best_pts[-1], bp, color=ORANGE, stroke_width=3.6)
            self.best_line.add(seg)
            anims.append(Create(seg))
        self.best_pts.append(bp)
        return anims

    def led(self, key) -> np.ndarray:
        """A docking point inside each box's top-left corner for the runner dot."""
        return self.boxes[key][0].get_corner(UL) + np.array([0.18, -0.18, 0.0])

    def lap_anim(self):
        """One full turn of the loop, run by the dot -- so the machine visibly runs on every
        step instead of sitting still while its products appear."""
        path = VMobject().set_points_as_corners(
            [self.runner.get_center(), self.led("mutate"), self.led("evaluate"),
             self.led("record")])
        return MoveAlongPath(self.runner, path)

    def select_flashes(self, ev):
        """The selection, made visible on every single step: the drawn cell and the parent node
        flash together, BEFORE the child exists."""
        anims = []
        key = (ev.get("parent_island"), ev.get("parent_cell"))
        if ev.get("from_menu") and key in self.cells:
            anims.append(Indicate(self.cells[key], color=ORANGE, scale_factor=1.18))
        if ev.get("parent") in self.tree_mobs:
            anims.append(Indicate(self.tree_mobs[ev["parent"]], color=ORANGE, scale_factor=1.3))
        return anims

    def step(self, ev, run_time=0.55):
        flashes = self.select_flashes(ev)
        if flashes:
            self.play(*flashes, self.runner.animate.move_to(self.led("select")),
                      run_time=run_time * 0.55)
        self.play(*self.tree_anims(ev), *self.grid_anims(ev), *self.curve_anims(ev),
                  self.lap_anim(), run_time=run_time)

    def next_event(self):
        ev = EVENTS[self.ptr]
        self.ptr += 1
        return ev

    # ---- act 1: the loop, the tree, the grid, the curve --------------------
    def act_one(self):
        boxes = {"select": loop_box("select", "select a parent", "from the MAP-Elites grid"),
                 "mutate": loop_box("mutate", "mutate it", "a coding agent"),
                 "evaluate": loop_box("evaluate", "evaluate", "the verifier scores it"),
                 "record": loop_box("record", "record", "tree grows · grid updates")}
        self.boxes = boxes
        b = boxes
        ring = VGroup(ring_arrow(b["select"][0], b["mutate"][0]),
                      ring_arrow(b["mutate"][0], b["evaluate"][0]),
                      ring_arrow(b["evaluate"][0], b["record"][0]),
                      ring_arrow(b["record"][0], b["select"][0]))
        self.ring = ring

        grid_frame = blank_grid(0)
        self.grid_lab = txt("the MAP-Elites grid", 17, SUB).move_to([-4.1, -0.62, 0])
        # No "island 1" tag yet: while there is one island the label is noise, and it was
        # colliding with the metric axis label that act 2 puts in the same spot. Both island
        # tags arrive with the act that makes them mean something.
        self.isl_tag0 = txt("island 1", 13, MUTED).next_to(grid_frame, DOWN, buff=0.42)

        self.ax = Axes(x_range=[0, len(PLACES) + 1, 5], y_range=[0, 0.8, 0.2],
                       x_length=6.6, y_length=2.1, tips=False,
                       axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        self.ax.move_to(CURVE_C)
        ticks = VGroup(*[txt(f"{v:.1f}", 13, MUTED).next_to(self.ax.c2p(0, v), LEFT, buff=0.1)
                         for v in (0.0, 0.4, 0.8)],
                       *[txt(str(v), 13, MUTED).next_to(self.ax.c2p(v, 0), DOWN, buff=0.1)
                         for v in (10, 20, 30)],
                       txt("step", 13, MUTED).next_to(self.ax.c2p(len(PLACES) + 1, 0), DOWN,
                                                      buff=0.1))
        curve_lab = txt("fitness · every child, and the best so far", 15, SUB)
        curve_lab.next_to(self.ax, UP, buff=0.1).align_to(self.ax, LEFT)
        self.best_line = VGroup()
        self.add(self.best_line, self.curve_dots)

        tree_lab = txt("the tree — everything ever made", 17, SUB).move_to([TREE_CX, 3.55, 0])
        self.runner = Dot(radius=0.07, color=ORANGE)

        seed = self.next_event()
        self.play(LaggedStart(*[FadeIn(boxes[k], scale=0.9) for k in
                                ("select", "mutate", "evaluate", "record")], lag_ratio=0.15),
                  LaggedStart(*[Create(a) for a in ring], lag_ratio=0.15),
                  FadeIn(grid_frame), FadeIn(self.grid_lab),
                  Create(self.ax), FadeIn(ticks), FadeIn(curve_lab), FadeIn(tree_lab),
                  run_time=2.0)
        self.runner.move_to(self.led("select"))
        self.play(FadeIn(self.runner, scale=0.4), run_time=0.4)

        self.n_places += 1
        pos0 = tree_layout(PLACES[:1])
        seed_dot = tree_dot(seed, pos0[seed["id"]])
        self.tree_mobs[seed["id"]] = seed_dot
        self.play(FadeIn(seed_dot, scale=0.5), *self.grid_anims(seed), *self.curve_anims(seed),
                  run_time=0.8)
        self.say("the seed: one node on the tree, one cell in the grid, one point on the curve",
                 hold=1.6)

        # ---- one slow lap, on the run's real first child -------------------
        ev = self.next_event()

        # SELECT -- the cell and the node flash together; they are the same thing seen twice
        self.play(Indicate(boxes["select"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("select")),
                  *self.select_flashes(ev), run_time=1.0)
        self.play(*self.select_flashes(ev), run_time=0.9)
        self.say("select — a uniform draw over the grid's filled cells, one vote each.\n"
                 "The cell holds a pointer; the parent it points at is a node of the tree.",
                 hold=2.2)

        # MUTATE: the panel is the mutate box opened up, and it has to LOOK like that
        agent = self.agent_inset()
        mut = boxes["mutate"][0]
        callout = VGroup(
            DashedLine(mut.get_corner(DL), agent[0].get_corner(UL), color=ORANGE,
                       stroke_width=1.6, dash_length=0.08),
            DashedLine(mut.get_corner(DR), agent[0].get_corner(UR), color=ORANGE,
                       stroke_width=1.6, dash_length=0.08))
        self.play(Indicate(boxes["mutate"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("mutate")),
                  GrowFromPoint(agent, mut.get_center()), run_time=0.9)
        self.play(Create(callout), run_time=0.4)
        self.say("mutate — the operator is a coding agent: it edits, runs the evaluator itself,\n"
                 "and submits only an edit that passes. Not one model call — a loop of its own.")
        for _ in range(2):
            for j in range(3):
                self.play(Indicate(agent[1][j][1], color=ORANGE, scale_factor=1.25),
                          run_time=0.32)
        self.wait(0.7)

        # EVALUATE
        self.play(FadeOut(agent), FadeOut(callout),
                  Indicate(boxes["evaluate"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("evaluate")),
                  *self.curve_anims(ev), run_time=0.9)
        self.say(f"evaluate — the verifier reports the metrics; this child scores "
                 f"{ev['score']:.2f}", hold=1.6)

        # RECORD
        self.play(Indicate(boxes["record"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("record")),
                  *self.tree_anims(ev, lit=True), *self.grid_anims(ev, lit=True), run_time=1.0)
        self.say("record — the tree keeps everything, always. The grid updates one cell's\n"
                 "representative — the rules for that are the next scene.", hold=2.2)
        # Green means "this just happened"; a ring that survives the beat reads as a legend
        # entry nobody defined.
        key = (ev["island"], ev["cell"])
        self.play(self.tree_mobs[ev["id"]].animate.set_stroke(EDGE, width=2.0),
                  self.cells[key].animate.set_stroke(EDGE, width=1.2),
                  run_time=0.5)

        # ---- more laps, at speed, each with its selection visible ----------
        self.say("the same turn, again and again — watch the grid hand out each parent",
                 color=SUB)
        while self.n_places < 10:
            self.step(self.next_event())
        self.wait(1.0)
        self.drop_caption()

    def agent_inset(self) -> VGroup:
        """The mutate box, opened up: content-sized, opaque, retry path as an elbow BELOW the
        row -- an arc hugging the boxes read as a strikethrough."""
        steps = VGroup(*[VGroup(RoundedRectangle(width=1.0, height=0.5, corner_radius=0.08,
                                                 stroke_width=1.4, color=EDGE,
                                                 fill_color=PANEL, fill_opacity=1.0),
                                txt(w, 15, INK))
                         for w in ("edit", "run", "check")])
        for box in steps:
            box[1].move_to(box[0])
        steps.arrange(RIGHT, buff=0.5)
        fwd = VGroup(spoke(steps[0].get_right(), steps[1].get_left(), MUTED, 1.6,
                           0.04, 0.04, tip=0.12),
                     spoke(steps[1].get_right(), steps[2].get_left(), MUTED, 1.6,
                           0.04, 0.04, tip=0.12))
        rail_y = steps.get_bottom()[1] - 0.42
        down = Line(steps[2][0].get_bottom() + DOWN * 0.04,
                    [steps[2][0].get_bottom()[0], rail_y, 0], color=MUTED, stroke_width=1.6)
        across = Line([steps[2][0].get_bottom()[0], rail_y, 0],
                      [steps[0][0].get_bottom()[0], rail_y, 0], color=MUTED, stroke_width=1.6)
        up = spoke([steps[0][0].get_bottom()[0], rail_y, 0],
                   steps[0][0].get_bottom() + DOWN * 0.04, MUTED, 1.6, 0.0, 0.02, tip=0.12)
        retry = txt("fails? edit again", 13, MUTED).next_to(across, DOWN, buff=0.08)
        head = txt("mutate it, opened up", 15, ORANGE)
        head.next_to(steps, UP, buff=0.22)

        content = VGroup(head, steps, fwd, down, across, up, retry)
        frame = RoundedRectangle(width=content.width + 0.55, height=content.height + 0.4,
                                 corner_radius=0.15, stroke_width=1.8, color=ORANGE,
                                 fill_color=PANEL, fill_opacity=1.0)
        frame.move_to(content.get_center())
        return VGroup(frame, steps, fwd, VGroup(down, across, up), retry, head).move_to(
            [-3.95, 1.32, 0])

    # ---- act 2: the grid's bookkeeping --------------------------------------
    def act_two(self):
        # the loop has been taught; attention moves to the grid, which stays where it is.
        # kit dim(), not set_stroke: arrowheads are filled polygons and would stay dark.
        self.play(*[self.boxes[k][0].animate.set_stroke(opacity=0.35) for k in self.boxes],
                  *[self.boxes[k][1].animate.set_opacity(0.35) for k in self.boxes],
                  *dim([(self.ring, "stroke")], 0.22),
                  run_time=0.8)

        g0 = GRID_C[0]
        xlab = txt("metric 1 →", 14, MUTED).next_to(g0 + DOWN * (BINS * GRID_CELL / 2), DOWN,
                                                    buff=0.12)
        ylab = txt("metric 2 →", 14, MUTED).rotate(np.pi / 2).next_to(
            g0 + LEFT * (BINS * GRID_CELL / 2), LEFT, buff=0.12)
        self.play(FadeIn(xlab), FadeIn(ylab), run_time=0.7)
        self.say("the grid's axes are two metrics the evaluator reports — whatever the problem\n"
                 "measures. Each cell is one niche; a filled cell holds its best program so far.",
                 hold=2.4)

        # projection: a tree node flies into its cell
        pe = PLACES[3]
        ghost = self.tree_mobs[pe["id"]].copy()
        self.say("every tree node has coordinates in this space — the grid files each one by "
                 "its cell", color=SUB)
        self.play(ghost.animate.move_to(cell_center(pe["island"], pe["cell"])).scale(1.35),
                  run_time=1.2)
        self.play(FadeOut(ghost), run_time=0.4)

        # the three admission cases, in stream order -- the run supplies them back to back
        while self.n_places < 11:
            self.step(self.next_event(), run_time=0.45)

        cases = [
            ("its cell is EMPTY, so it becomes the representative —\n"
             "even at {score:.2f}, below the best so far ({best:.2f})", True),
            ("it lands on a held cell and SCORES HIGHER —\nit takes over as the representative",
             True),
            ("it lands on a held cell and loses. It is not the representative —\n"
             "but it stays on the tree, and it can still be drawn as a parent", False),
        ]
        for text, admitted in cases:
            ev = self.next_event()
            assert ev["kind"] == "place" and ev["admitted"] == admitted, \
                "the recorded run no longer matches the script"
            flashes = self.select_flashes(ev)
            if flashes:
                self.play(*flashes, self.runner.animate.move_to(self.led("select")),
                          run_time=0.4)
            self.play(*self.tree_anims(ev), *self.curve_anims(ev), self.lap_anim(),
                      run_time=0.7)
            tok = self.tree_mobs[ev["id"]].copy().scale(1.25)
            self.say(text.format(score=ev["score"], best=ev["best"]))
            self.play(tok.animate.move_to(cell_center(ev["island"], ev["cell"])), run_time=0.9)
            if admitted:
                self.play(FadeOut(tok), *self.grid_anims(ev, lit=True), run_time=0.7)
                self.wait(1.4)
                key = (ev["island"], ev["cell"])
                self.play(self.cells[key].animate.set_stroke(EDGE, width=1.2),
                          self.tree_mobs[ev["id"]].animate.set_stroke(EDGE, width=2.0),
                          run_time=0.4)
            else:
                self.play(tok.animate.set_stroke(RED, width=3.0), run_time=0.4)
                self.play(tok.animate.move_to(self.tree_mobs[ev["id"]].get_center())
                          .scale(1 / 1.25), run_time=0.8)
                self.play(FadeOut(tok), run_time=0.3)
                self.wait(1.4)

        # the selection rule, over everything the grid now holds
        votes = VGroup(*[sq.copy().set_stroke(ORANGE, width=2.8).set_fill(opacity=0)
                         for sq in self.cells.values()])
        self.say(f"and this is what selection means now: {len(self.cells)} filled cells,\n"
                 "one vote each — however crowded a niche is, it gets exactly one", hold=0.4)
        self.play(LaggedStart(*[FadeIn(v) for v in votes], lag_ratio=0.06), run_time=1.2)
        self.wait(1.8)
        self.play(FadeOut(votes), run_time=0.5)

        note = para("the grid holds no programs, only pointers into the tree. Its one job in "
                    "this method:\ndecide where the tree grows next.", 21, SUB)
        fit(note, 6.6).move_to(CAP_AT)
        self.play(Transform(self.caption, note), run_time=0.6)
        self.wait(2.8)

    # ---- act 3 (islands) + act 4 (the rest), driven by the recording --------
    def act_rest(self):
        while self.ptr < len(EVENTS):
            ev = self.next_event()
            if ev["kind"] == "migrate":
                self.migrations_seen += 1
                if self.migrations_seen == 1:
                    self.act_islands(ev)
                else:
                    self.quick_migration(ev)
                continue
            self.step(ev, run_time=0.4)

        # coverage joins the curve for the closing point
        cov = VGroup()
        for i in range(1, len(PLACES)):
            cov.add(Line(self.ax.c2p(i, PLACES[i - 1]["coverage"]),
                         self.ax.c2p(i + 1, PLACES[i]["coverage"]),
                         color=BLUE, stroke_width=3.2))
        cov_lab = txt("cells filled · both islands", 14, BLUE).next_to(
            self.ax.c2p(len(PLACES) - 5, PLACES[-1]["coverage"]), UP, buff=0.14)
        self.play(Create(cov), FadeIn(cov_lab), run_time=1.6)
        self.say("the best score stalls for long stretches; coverage does not — and every cell\n"
                 "filled during a stall is a parent the search would not otherwise have had",
                 hold=3.0)
        self.say("(two islands fit on a slide; the real runs use three)", color=SUB, hold=2.4)

    def act_islands(self, ev):
        """Islands, introduced by the run's own first migration.

        With one seed the fact is exact: children inherit their parent's island, so island 2 has
        been empty this whole time and is COLONISED here. That is why this act can sit after the
        single-grid story without any of it having been a simplification.
        """
        self.say("so far, one island. The method actually runs SEVERAL copies of the grid —\n"
                 "and with a single seed, the second one has been empty all along", hold=2.2)
        frame1 = blank_grid(1)
        tag1 = txt("island 2", 13, MUTED).next_to(frame1, DOWN, buff=0.42)
        self.island1_open = True
        self.play(FadeIn(frame1), FadeIn(tag1), FadeIn(self.isl_tag0), run_time=0.9)

        movers = [nid for nid, _, to in ev["moved"] if to == 1 and nid in self.tree_mobs]
        self.say(f"migration — every few steps, a slice of each island moves to the next.\n"
                 f"Here: {len(movers)} programs change island; their ring turns purple", hold=0.4)
        self.play(*[Indicate(self.tree_mobs[n], color=PURPLE, scale_factor=1.3)
                    for n in movers], run_time=1.0)
        self.play(*[self.tree_mobs[n].animate.set_stroke(PURPLE, width=2.0) for n in movers],
                  *self.grid_anims(ev), run_time=0.9)
        self.wait(1.8)

        self.say("from now on their children are born on island 2 and compete in ITS grid —\n"
                 "so one niche can keep a different best alive on each island, one vote apiece",
                 hold=3.0)

    def quick_migration(self, ev):
        """A later migration, in one breath. The ring colour follows the DESTINATION -- reading
        the birth island here would paint a node that just left island 2 as if it still lived
        there."""
        here = [(nid, to) for nid, _, to in ev["moved"] if nid in self.tree_mobs]
        self.play(*[Indicate(self.tree_mobs[nid], color=PURPLE, scale_factor=1.25)
                    for nid, _ in here[:8]], run_time=0.7)
        self.play(*[self.tree_mobs[nid].animate.set_stroke(ISLAND_COLOR[to], width=2.0)
                    for nid, to in here if BY_ID[nid]["admitted"]],
                  *self.grid_anims(ev), run_time=0.7)
