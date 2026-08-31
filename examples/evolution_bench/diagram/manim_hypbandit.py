"""HypothesisBandit, as a narrated run.

    manim -qm --format=mp4 manim_hypbandit.py HypothesisBanditRun

Unlike the sibling method videos, nothing here is simulated: the whole video replays one real
AHC039 run (`hb_data`, wave4 seed 0) -- every hypothesis, every dR, every retirement and every
screen rejection, in recorded order.

**The stage is the method's two ledgers.** Left, the BANDIT PANEL: one row per live hypothesis,
bar = softmax share of priority(mu + sigma + eta*novelty) -- lavender while priority is pure
novelty, blue once evidence exists, struck through when retired. Center, THE PROGRAM as six
component blocks; a hypothesis owns exactly one block and an implementation edits only that
block. Right, the CREDIT LEDGER: each measured child drops its dR into the row of the component
it changed. Bottom-right, the run's score curve.

Acts:

  1. the card -- one real hypothesis, four fields, and the deliberate absence of a judge
     (the E1 ablation is the reason and the title card says so)
  2. the stage assembles: four opening hypotheses, six components, empty ledgers, the seed
  3. one slow lap: novelty-only pick, an edit that touches one block, a measurement, and the
     one number that lands in TWO ledgers
  4. the run at speed -- the champion arrives and its hypothesis retires in the same event;
     first impressions sour; the bandit follows the average, not the story
  5. the credit table points somewhere new: numerical-optimization, never edited, and the
     method asks for hypotheses THERE
  6. the 30-case screen: three candidates rejected before they can cost a full measurement
  7. the close: the tally, and wave4 across three seeds
"""
from __future__ import annotations

import math

import numpy as np
from manim import (DOWN, LEFT, RIGHT, UP, Create, DashedVMobject, FadeIn, FadeOut, Indicate,
                   LaggedStart, Line, MovingCameraScene, Rectangle, RoundedRectangle,
                   SurroundingRectangle, Transform, VGroup, VMobject, Write)

from hb_data import (BEST_SCORE, CARD, COMPONENTS, ETA, EVENTS, FULL_CASES, HYPS, K, LAM,
                     PRIOR_SIGMA, SCREEN_CASES, SEED_SCORE, TAU, WAVE4)
from manim_kit import (BLUE, EDGE, GREEN, INK, Kit, MUTED, ORANGE, PANEL, PURPLE, RED, SUB, fit,
                       para, score_color, spoke, txt)

LO, HI = 2.452, 2.487                    # the score range every colour in the video spans
LAVENDER = "#b9a7e6"
AMBER = "#9a6700"

COMP_COLOR = {"initialization": BLUE, "core-algorithm": RED, "search-strategy": GREEN,
              "numerical-optimization": ORANGE, "parameters": PURPLE,
              "output-construction": AMBER}
COMP_SHORT = {"initialization": "init", "core-algorithm": "core", "search-strategy": "search",
              "numerical-optimization": "numopt", "parameters": "params",
              "output-construction": "output"}

# ---- the fixed stage -------------------------------------------------------
ROW0_Y, ROW_H = 2.3, 0.56
LBL_X, BAR_X0, BAR_WMAX, PROB_X = -6.95, -4.5, 1.45, -2.72
PANEL_HDR = np.array([-6.95, 2.92, 0.0])

BLK_X, BLK_W, BLK_H, BLK0_Y, BLK_DY = -0.75, 2.9, 0.6, 2.62, 0.7
CHIP_X = 1.02

TICK_X0, TICK_W, TICK_GAP, MEAN_X = 1.5, 0.17, 0.06, 3.35

CURVE_X0, CURVE_X1, CURVE_Y0, CURVE_Y1 = 2.6, 6.7, -3.3, -1.6
Y_LO, Y_HI = 2.452, 2.487

CAP_AT = np.array([-1.6, -3.92, 0.0])
"""Captions are BOTTOM-anchored here, not centred: they run one to three lines, and a centred
three-liner at any y that suits one line leaves the frame."""


def blk_y(comp: str) -> float:
    return BLK0_Y - COMPONENTS.index(comp) * BLK_DY


def row_y(idx: int) -> float:
    return ROW0_Y - idx * ROW_H


def curve_pt(i: int, score: float, n_max: int = 10) -> np.ndarray:
    x = CURVE_X0 + (CURVE_X1 - CURVE_X0) * i / n_max
    y = CURVE_Y0 + (CURVE_Y1 - CURVE_Y0) * (score - Y_LO) / (Y_HI - Y_LO)
    return np.array([x, y, 0.0])


def chip(score: float, at) -> VGroup:
    box = RoundedRectangle(width=0.78, height=0.32, corner_radius=0.07, stroke_width=1.4,
                           color=EDGE, fill_color=score_color(score, LO, HI),
                           fill_opacity=1.0).move_to(at)
    ink = "#ffffff" if score > LO + 0.55 * (HI - LO) else INK
    return VGroup(box, txt(f"{score * K:.0f}", 12.5, ink).move_to(at))


class HypothesisBanditRun(Kit, MovingCameraScene):

    # ---- captions ----------------------------------------------------------
    def say(self, text, size=21, color=INK, hold=0.0):
        new = para(text, size, color) if "\n" in text else txt(text, size, color)
        fit(new, 10.2).move_to(CAP_AT, aligned_edge=DOWN)
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

    # ---- bandit state (mirrors the method's controller) --------------------
    def probs(self):
        ids = [h for h in self.order if h not in self.retired]
        if not ids:
            return {}
        pr = {}
        for i in ids:
            g = self.gains[i]
            n = len(g)
            mu = (sum(g) / n) if n else 0.0
            pr[i] = mu + LAM * PRIOR_SIGMA / math.sqrt(1 + n) + ETA / (1 + n)
        m = max(pr.values())
        e = {i: math.exp((v - m) / TAU) for i, v in pr.items()}
        t = sum(e.values())
        return {i: e[i] / t for i in e}

    # ---- panel rows --------------------------------------------------------
    def make_row(self, hid: str, idx: int) -> VGroup:
        h = HYPS[hid]
        y = row_y(idx)
        tag = txt(COMP_SHORT[h["comp"]], 11.5, COMP_COLOR[h["comp"]], weight="BOLD")
        lbl = txt(h["label"], 13, INK)
        head = VGroup(tag, lbl).arrange(RIGHT, buff=0.12)
        fit(head, abs(BAR_X0 - LBL_X) - 0.25)
        head.move_to([LBL_X, y, 0], aligned_edge=LEFT)
        bar = Rectangle(width=0.02, height=0.2, stroke_width=0, fill_color=LAVENDER,
                        fill_opacity=1.0).move_to([BAR_X0 + 0.01, y, 0])
        prob = txt("", 11, SUB).move_to([PROB_X, y, 0], aligned_edge=LEFT)
        self.rows[hid] = {"head": head, "bar": bar, "prob": prob, "y": y}
        return VGroup(head, bar, prob)

    def panel_anims(self):
        """Animations bringing every live bar/probability to the current state."""
        pp = self.probs()
        anims = []
        for hid, r in self.rows.items():
            if hid in self.retired:
                continue
            p = pp.get(hid, 0.0)
            w = max(0.02, p * BAR_WMAX)
            col = BLUE if self.gains[hid] else LAVENDER
            bar = Rectangle(width=w, height=0.2, stroke_width=0, fill_color=col,
                            fill_opacity=1.0).move_to([BAR_X0 + w / 2, r["y"], 0])
            anims.append(Transform(r["bar"], bar))
            new = txt(f"{p * 100:.0f}%", 11, SUB).move_to([PROB_X, r["y"], 0],
                                                          aligned_edge=LEFT)
            anims += [FadeOut(r["prob"], run_time=0.3), FadeIn(new, run_time=0.3)]
            r["prob"] = new
        return anims

    def retire_anims(self, hid: str):
        r = self.rows[hid]
        self.retired.add(hid)
        strike = Line([LBL_X, r["y"], 0], [PROB_X + 0.42, r["y"], 0], color=RED,
                      stroke_width=1.6)
        w = max(0.02, 0.02)
        bar = Rectangle(width=w, height=0.2, stroke_width=0, fill_color=MUTED,
                        fill_opacity=0.6).move_to([BAR_X0 + w / 2, r["y"], 0])
        return [r["head"].animate.set_opacity(0.35), Transform(r["bar"], bar),
                r["prob"].animate.set_opacity(0.35), Create(strike)]

    # ---- ledger ------------------------------------------------------------
    def ledger_anims(self, comp: str, dR: float):
        self.credit[comp].append(dR)
        gs = self.credit[comp]
        y = blk_y(comp)
        n = len(gs) - 1
        tick = Rectangle(width=TICK_W, height=0.26, stroke_width=0.8, color=EDGE,
                         fill_color=GREEN if dR > 0 else RED, fill_opacity=0.85)
        tick.move_to([TICK_X0 + n * (TICK_W + TICK_GAP) + TICK_W / 2, y, 0])
        mean = sum(gs) / len(gs)
        new = txt(f"A {mean * K:+.1f}", 12, GREEN if mean > 0 else RED)
        new.move_to([MEAN_X, y, 0], aligned_edge=LEFT)
        anims = [FadeIn(tick, scale=1.6)]
        old = self.means.get(comp)
        if old is not None:
            anims.append(FadeOut(old, run_time=0.3))
        anims.append(FadeIn(new, run_time=0.3))
        self.means[comp] = new
        return anims

    # ---- curve -------------------------------------------------------------
    def curve_anims(self, score: float, dR: float):
        self.n_meas += 1
        best = max(self.best, score)
        p_raw = curve_pt(self.n_meas, score)
        p_best = curve_pt(self.n_meas, best)
        dot = RoundedRectangle(width=0.09, height=0.09, corner_radius=0.045, stroke_width=0,
                               fill_color=GREEN if dR > 0 else RED,
                               fill_opacity=0.9).move_to(p_raw)
        seg = Line(curve_pt(self.n_meas - 1, self.best), p_best, color=INK, stroke_width=2.4)
        self.best = best
        return [FadeIn(dot), Create(seg)]

    # ========================================================================
    def construct(self):
        self.caption = None
        self.rows, self.order, self.gains, self.retired = {}, [], {}, set()
        self.credit = {c: [] for c in COMPONENTS}
        self.means = {}
        self.best, self.n_meas = SEED_SCORE, 0
        self.ptr = 0

        self.act_title()
        self.act_card()
        self.act_stage()
        self.act_slow_lap()
        self.act_speed()
        self.act_headroom()
        self.act_gate()
        self.act_close()

    # ---- act 0: title ------------------------------------------------------
    def act_title(self):
        title = txt("HypothesisBandit", 52, INK, weight="BOLD")
        sub = para("state a mechanism  ->  edit one component  ->  measure\n"
                   "and let evidence, not opinion, allocate the budget", 24, SUB)
        src = txt("every number in this video is one real AHC039 run "
                  "(wave4 · seed 0 · gpt-5.6-luna)", 16, MUTED)
        grp = VGroup(title, sub, src).arrange(DOWN, buff=0.55).move_to([0, 0.2, 0])
        self.play(Write(title), run_time=1.2)
        self.play(FadeIn(sub, shift=UP * 0.2), run_time=0.8)
        self.play(FadeIn(src), run_time=0.6)
        self.wait(2.2)
        self.play(FadeOut(grp), run_time=0.6)

    # ---- act 1: the card ---------------------------------------------------
    def act_card(self):
        fields = [("target_component", CARD["target_component"], BLUE),
                  ("mechanism", CARD["mechanism"], INK),
                  ("expected_effect", CARD["expected_effect"], INK),
                  ("falsification", CARD["falsification"], ORANGE)]
        rows = VGroup()
        for name, val, col in fields:
            k = txt(name, 17, MUTED)
            v = para(val, 18, col) if "\n" in val else txt(val, 18, col)
            rows.add(VGroup(k, v).arrange(DOWN, buff=0.1, aligned_edge=LEFT))
        rows.arrange(DOWN, buff=0.34, aligned_edge=LEFT)
        frame = RoundedRectangle(width=rows.width + 0.9, height=rows.height + 0.7,
                                 corner_radius=0.18, stroke_width=2.0, color=EDGE,
                                 fill_color=PANEL, fill_opacity=1.0)
        card = VGroup(frame, rows.move_to(frame.get_center())).move_to([0, 0.55, 0])
        hdr = txt("a hypothesis  (verbatim from the run, shortened)", 19, SUB)
        hdr.next_to(card, UP, buff=0.3)

        self.say("the unit of search is not a program edit — it is a CLAIM about the program:\n"
                 "one component, one mechanism, and what would prove it wrong")
        self.play(FadeIn(hdr), FadeIn(card, shift=UP * 0.15), run_time=0.9)
        self.wait(2.6)
        self.play(Indicate(rows[3], color=ORANGE, scale_factor=1.04), run_time=1.0)
        self.say("no LLM grades this card. A structural gate admits it — its VALUE will come\n"
                 "from measurements only. (In our ablations an LLM judge produced 0/39\n"
                 "improving children; random selection produced 9/56.)", hold=3.4)
        self.card_mob, self.card_hdr = card, hdr

    # ---- act 2: the stage --------------------------------------------------
    def act_stage(self):
        # program column
        blocks = VGroup()
        for c in COMPONENTS:
            y = blk_y(c)
            fr = RoundedRectangle(width=BLK_W, height=BLK_H, corner_radius=0.1,
                                  stroke_width=1.6, color=EDGE, fill_color=PANEL,
                                  fill_opacity=1.0).move_to([BLK_X, y, 0])
            strip = Rectangle(width=0.09, height=BLK_H - 0.1, stroke_width=0,
                              fill_color=COMP_COLOR[c], fill_opacity=0.9)
            strip.move_to([BLK_X - BLK_W / 2 + 0.1, y, 0])
            blocks.add(VGroup(fr, strip, txt(c, 14, INK).move_to([BLK_X, y, 0])))
        self.blocks = {c: blocks[i] for i, c in enumerate(COMPONENTS)}
        blk_hdr = txt("the program — six components", 16, SUB, weight="BOLD")
        blk_hdr.move_to([BLK_X, BLK0_Y + 0.62, 0])
        seed_tag = txt(f"seed  {SEED_SCORE * K:.0f} per case", 14, SUB)
        seed_tag.move_to([BLK_X, BLK0_Y + 0.95, 0])

        # ledger
        led_hdr = para("component credit\nA(v) = mean ΔR when v was edited", 14, SUB)
        led_hdr.move_to([2.55, BLK0_Y + 0.78, 0])
        dashes = VGroup(*[txt("—", 12, MUTED).move_to([TICK_X0 + 0.08, blk_y(c), 0])
                          for c in COMPONENTS])
        self.led_dashes = {c: dashes[i] for i, c in enumerate(COMPONENTS)}

        # panel header
        pan_hdr = VGroup(txt("live hypotheses", 16, SUB, weight="BOLD"),
                         txt("priority = μ + σ + ½·novelty   →  softmax share", 12.5, MUTED))
        pan_hdr.arrange(DOWN, buff=0.08, aligned_edge=LEFT)
        pan_hdr.move_to(PANEL_HDR, aligned_edge=LEFT)

        # curve frame
        ax = VGroup(Line([CURVE_X0 - 0.15, CURVE_Y0, 0], [CURVE_X1 + 0.1, CURVE_Y0, 0],
                         color=MUTED, stroke_width=1.4),
                    Line([CURVE_X0 - 0.15, CURVE_Y0, 0], [CURVE_X0 - 0.15, CURVE_Y1 + 0.15, 0],
                         color=MUTED, stroke_width=1.4))
        yticks = VGroup()
        for v in (2.46, 2.47, 2.48):
            p = curve_pt(0, v)
            yticks.add(txt(f"{v * K:.0f}", 10, MUTED).move_to([CURVE_X0 - 0.5, p[1], 0]))
        xlab = txt("full measurements →", 12, MUTED).move_to([(CURVE_X0 + CURVE_X1) / 2,
                                                             CURVE_Y0 - 0.28, 0])
        seed_dot = RoundedRectangle(width=0.1, height=0.1, corner_radius=0.05, stroke_width=0,
                                    fill_color=SUB, fill_opacity=1.0).move_to(
                                        curve_pt(0, SEED_SCORE))

        # the featured card shrinks into row 1 of the panel; the other three follow
        first = [e["id"] for e in EVENTS[:4]]
        self.order = list(first)
        for hid in first:
            self.gains[hid] = []
        rows = VGroup(*[self.make_row(hid, i) for i, hid in enumerate(first)])
        target = self.rows["43e8ce52"]["head"]

        self.say("the stage: hypotheses and their bandit on the left, the program's six\n"
                 "components in the middle, the credit ledger on the right")
        self.play(FadeOut(self.card_hdr),
                  Transform(self.card_mob, target.copy().set_opacity(0.0)),
                  FadeIn(blk_hdr), FadeIn(seed_tag), LaggedStart(*[FadeIn(b) for b in blocks],
                                                                 lag_ratio=0.08),
                  run_time=1.4)
        self.remove(self.card_mob)
        self.play(FadeIn(pan_hdr), LaggedStart(*[FadeIn(r) for r in rows], lag_ratio=0.15),
                  FadeIn(led_hdr), FadeIn(dashes), run_time=1.2)
        self.play(Create(ax), FadeIn(yticks), FadeIn(xlab), FadeIn(seed_dot), run_time=0.9)
        self.say("four hypotheses to start — each owns exactly ONE component", hold=1.2)
        self.play(*self.panel_anims(), run_time=0.8)
        self.ptr = 4

    # ---- a measurement, animated -------------------------------------------
    def do_measure(self, ev, slow=False):
        hid = ev["hyp"]
        comp = HYPS[hid]["comp"]
        r = self.rows[hid]
        blk = self.blocks[comp]
        y = r["y"]

        halo = SurroundingRectangle(VGroup(r["head"], r["bar"], r["prob"]), color=COMP_COLOR[comp],
                                    stroke_width=2.0, buff=0.07)
        edit = spoke([PROB_X + 0.5, y, 0], blk[0].get_left() + np.array([-0.05, 0, 0]),
                     COMP_COLOR[comp], 2.6, tip=0.14)
        self.play(Create(halo), run_time=0.4 if slow else 0.25)
        self.play(Create(edit), Indicate(blk, color=COMP_COLOR[comp], scale_factor=1.03),
                  run_time=0.7 if slow else 0.4)
        if slow:
            self.say("implement = EDIT. The instruction: change ONLY the component the\n"
                     "hypothesis names — the other five stay untouched", hold=1.4)

        ch = chip(ev["score"], [CHIP_X, blk_y(comp), 0])
        self.play(FadeIn(ch, scale=1.4), run_time=0.6 if slow else 0.35)
        if slow:
            self.say(f"the verifier runs all {FULL_CASES} cases: mean {ev['score'] * K:.0f} per case — "
                     f"ΔR = {ev['dR'] * K:+.1f} fish per case against its base", hold=1.4)
        star = None
        if ev.get("best"):
            star = txt("★ new best", 13, GREEN, weight="BOLD").next_to(ch, UP, buff=0.08)
            self.play(FadeIn(star, scale=1.3), run_time=0.5)

        dtxt = txt(f"{ev['dR'] * K:+.1f}", 13, GREEN if ev["dR"] > 0 else RED, weight="BOLD")
        dtxt.next_to(ch, DOWN, buff=0.08)
        d2 = dtxt.copy()
        self.play(FadeIn(dtxt), run_time=0.4 if slow else 0.25)

        # one number, two ledgers
        self.gains[hid].append(ev["dR"])
        dash = self.led_dashes.pop(comp, None)
        led = self.ledger_anims(comp, ev["dR"])
        if dash is not None:
            led.append(FadeOut(dash))
        self.play(dtxt.animate.move_to([BAR_X0 + 0.5, y, 0]).scale(0.85),
                  d2.animate.move_to([TICK_X0 + 0.6, blk_y(comp), 0]).scale(0.85),
                  run_time=0.9 if slow else 0.5)
        self.play(FadeOut(dtxt), FadeOut(d2), *led, *self.panel_anims(),
                  *self.curve_anims(ev["score"], ev["dR"]), run_time=0.9 if slow else 0.5)
        if slow:
            self.say("one number, two ledgers: the hypothesis earns EVIDENCE (its bar turns\n"
                     "blue), and the component it touched earns CREDIT", hold=1.8)
            self.say("notice the bar DROPS anyway — spent novelty outweighs a +16-point gain.\n"
                     "Priority keeps favouring the untried; where evidence really bites\n"
                     "is retirement. Watch.", hold=2.2)

        anims = [FadeOut(halo), FadeOut(edit), FadeOut(ch)]
        if star is not None:
            anims.append(FadeOut(star))
        g = self.gains[hid]
        if len(g) >= 2 and sum(g) / len(g) <= 0 and hid not in self.retired:
            anims += self.retire_anims(hid)
            self.retired_now = hid
        else:
            self.retired_now = None
        self.play(*anims, run_time=0.5 if slow else 0.35)
        if self.retired_now:
            self.play(*self.panel_anims(), run_time=0.5)

    # ---- act 3: one slow lap ----------------------------------------------
    def act_slow_lap(self):
        ev = EVENTS[self.ptr]
        self.ptr += 1
        self.say("no evidence yet, so priority is pure novelty — every bar equal.\n"
                 "The bandit draws: the rectangle-seeding hypothesis", hold=1.6)
        self.do_measure(ev, slow=True)

    # ---- act 4: speed ------------------------------------------------------
    def act_speed(self):
        self.say("the same turn, at speed", hold=0.4)
        while self.ptr < len(EVENTS):
            ev = EVENTS[self.ptr]
            if ev["kind"] == "screen":
                break
            self.ptr += 1
            if ev["kind"] == "hyp":
                hid = ev["id"]
                if hid == "ec37d56d":
                    self.ptr -= 1
                    return          # act 5 narrates this arrival
                self.order.append(hid)
                self.gains[hid] = []
                row = self.make_row(hid, len(self.order) - 1)
                self.play(FadeIn(row, shift=LEFT * 0.2), *self.panel_anims(), run_time=0.6)
                continue
            if ev.get("best"):
                self.do_measure(ev)
                self.say("the bay-fill hypothesis lands the run's best program — and RETIRES\n"
                         "in the same event: two children, average negative. The program\n"
                         "stays; the hypothesis stops spending.", hold=2.6)
                continue
            if ev.get("inflight"):
                self.say("(a third child was already in flight when it retired — it lands,\n"
                         "and changes nothing)", hold=1.0)
            self.do_measure(ev)
            if self.retired_now == "de72dd2f":
                self.say("core-algorithm: two misses, retired", hold=1.2)
            if self.retired_now == "43e8ce52":
                self.say("initialization's first child looked good; its average did not.\n"
                         "The bandit believes the average, not the first impression.",
                         hold=2.2)

    # ---- act 5: headroom ---------------------------------------------------
    def act_headroom(self):
        halo = SurroundingRectangle(
            VGroup(self.blocks["numerical-optimization"], self.led_dashes["numerical-optimization"]),
            color=ORANGE, stroke_width=2.2, buff=0.09)
        self.say("the credit table also says where to LOOK: numerical-optimization has\n"
                 "never been edited — highest optimistic credit, A(v) unknown", hold=0.6)
        self.play(Create(halo), run_time=0.6)
        self.wait(1.0)
        ev = EVENTS[self.ptr]           # hyp ec37d56d
        self.ptr += 1
        self.order.append(ev["id"])
        self.gains[ev["id"]] = []
        row = self.make_row(ev["id"], len(self.order) - 1)
        self.say("so the method asks for a hypothesis THERE — proposing is also steered\n"
                 "by measured credit, not taste", hold=0.6)
        self.play(FadeIn(row, shift=LEFT * 0.2), *self.panel_anims(), run_time=0.7)
        self.wait(0.6)
        self.play(FadeOut(halo), run_time=0.4)
        # the two remaining full measures before the screens
        while EVENTS[self.ptr]["kind"] == "measure":
            ev = EVENTS[self.ptr]
            self.ptr += 1
            if ev.get("inflight"):
                self.say("(initialization's third child was already in flight — it lands,\n"
                         "and changes nothing)", hold=0.8)
            self.do_measure(ev)

    # ---- act 6: the screen -------------------------------------------------
    def act_gate(self):
        gate = VGroup(
            DashedVMobject(RoundedRectangle(width=2.3, height=0.62, corner_radius=0.1,
                                            stroke_width=2.0, color=ORANGE),
                           num_dashes=40),
            para(f"{SCREEN_CASES}-case screen\n1/5 the cost", 12.5, ORANGE))
        gate[1].move_to(gate[0].get_center())
        gate.move_to([-0.75, -1.62, 0])
        self.say(f"this task has a cheap fidelity: {SCREEN_CASES} cases instead of "
                 f"{FULL_CASES}.\nEvery candidate faces it first", hold=0.6)
        self.play(FadeIn(gate), run_time=0.7)
        while self.ptr < len(EVENTS):
            ev = EVENTS[self.ptr]
            self.ptr += 1
            if ev["kind"] == "hyp":
                self.order.append(ev["id"])
                self.gains[ev["id"]] = []
                row = self.make_row(ev["id"], len(self.order) - 1)
                self.say("its successor is already on the panel — numerical-optimization\n"
                         "gets another stated mechanism to try", hold=0.6)
                self.play(FadeIn(row, shift=LEFT * 0.2), *self.panel_anims(), run_time=0.7)
                continue
            hid = ev["hyp"]
            r = self.rows[hid]
            if ev["score"] < 2.0:
                self.say(f"a {HYPS[hid]['comp']} candidate scores {ev['score'] * K:.0f} against a "
                         f"base of {ev['base'] * K:.0f}\n— rejected. The {FULL_CASES}-case budget "
                         "is never spent on it")
            else:
                self.say(f"a {HYPS[hid]['comp']} candidate: {ev['score'] * K:.0f} — close, but "
                         "short of\nthe promotion margin. Rejected too")
            ch = chip(ev["score"], [PROB_X + 0.35, r["y"], 0])
            self.play(FadeIn(ch, scale=1.3), run_time=0.4)
            self.play(ch.animate.move_to(gate.get_center() + np.array([-1.6, 0, 0])),
                      run_time=0.6)
            x1 = Line(gate.get_left() + np.array([-0.32, -0.18, 0]),
                      gate.get_left() + np.array([0.04, 0.18, 0]), color=RED, stroke_width=3.2)
            x2 = Line(gate.get_left() + np.array([-0.32, 0.18, 0]),
                      gate.get_left() + np.array([0.04, -0.18, 0]), color=RED, stroke_width=3.2)
            self.play(Create(x1), Create(x2), run_time=0.35)
            self.wait(0.8)
            fails = self.fails = getattr(self, "fails", {})
            fails[hid] = fails.get(hid, 0) + 1
            anims = [FadeOut(ch), FadeOut(x1), FadeOut(x2)]
            if fails[hid] >= 2 and hid not in self.retired:
                anims += self.retire_anims(hid)
            self.play(*anims, run_time=0.5)
            if fails.get(hid, 0) >= 2 and hid in self.retired:
                self.play(*self.panel_anims(), run_time=0.5)
                self.say("two screen failures retire a hypothesis just like two bad\n"
                         "measurements — cheap evidence is still evidence", hold=1.4)
        self.play(FadeOut(gate), run_time=0.5)

    # ---- act 7: close ------------------------------------------------------
    def act_close(self):
        self.drop_caption()
        star = txt("★", 20, GREEN).move_to(curve_pt(4, BEST_SCORE) + np.array([0, 0.22, 0]))
        final = txt(f"best {BEST_SCORE * K:.0f} per case  ({(BEST_SCORE - SEED_SCORE) * K:+.1f} over the seed)",
                    15, INK, weight="BOLD").move_to([(CURVE_X0 + CURVE_X1) / 2,
                                                     CURVE_Y1 + 0.42, 0])
        self.play(FadeIn(star), FadeIn(final), run_time=0.8)
        self.wait(1.2)

        tally = para("this run:  7 hypotheses · 10 full measurements · 3 screened out\n"
                     "4 hypotheses retired by their own evidence", 17, INK)
        tally.move_to(CAP_AT, aligned_edge=DOWN)
        self.play(FadeIn(tally), run_time=0.7)
        self.wait(2.4)

        hb, me = WAVE4["hypothesis_bandit"], WAVE4["agent_map_elites"]
        board = VGroup(
            txt("wave4 · AHC039 · three seeds · same model, same budget · official pts per case", 18, SUB),
            VGroup(txt(f"HypothesisBandit   mean {hb['mean'] * K:.0f}   "
                       f"{hb['measured']} measured programs", 20, PURPLE, weight="BOLD"),
                   txt(f"AgentMapElites      mean {me['mean'] * K:.0f}   "
                       f"{me['measured']} measured programs", 20, BLUE)).arrange(
                       DOWN, buff=0.22, aligned_edge=LEFT),
            para("the same place, at a third of the measurements —\n"
                 "because every evaluation was spent on a stated, falsifiable mechanism",
                 19, INK)).arrange(DOWN, buff=0.4)
        stage = [m for m in self.mobjects if isinstance(m, VMobject) and m is not tally]
        self.play(*[FadeOut(m) for m in stage], FadeOut(tally), run_time=0.8)
        board.move_to([0, 0.1, 0])
        self.play(FadeIn(board, shift=UP * 0.2), run_time=0.9)
        self.wait(3.2)
        self.play(FadeOut(board), run_time=0.7)
