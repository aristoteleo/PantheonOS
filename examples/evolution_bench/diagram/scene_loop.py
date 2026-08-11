"""Scene 1 -- the loop every method plugs into."""
from evo_anim import (ARROW, BLUE, Canvas, EDGE, GREEN, GREY, ORANGE, PANEL, PURPLE, RED, SUB, 
                      TITLE, fade)

STEPS = 9


def frame(step: int):
    c = Canvas("The loop every method plugs into",
               "concurrency, budget and checkpoints live here — the search does not")

    a_seed = fade(step, 0)
    c.box(38, 49.5, 24, 5.2, "seed program", "the thing being evolved", alpha=a_seed)

    a_loop = fade(step, 1)
    c.region(11, 21.0, 78, 25.5, "EVOLUTION LOOP",
             "the loop never inspects a genome, never ranks anything, never decides what to try",
             alpha=a_loop)
    c.arrow((50, 49.3), (50, 46.8), alpha=a_loop)

    a_ask = fade(step, 2)
    c.box(14, 37.0, 26, 6.4, "method.ask(n)", "what to try next — selection, phase, prompt",
          alpha=a_ask)

    a_var = fade(step, 3)
    c.box(60, 37.0, 26, 6.4, "variator.create()", "writes the child: agent, or one completion",
          model=True, alpha=a_var)
    c.arrow((40.6, 40.2), (59.4, 40.2), alpha=a_var)

    a_ev = fade(step, 4)
    c.box(60, 24.5, 26, 6.4, "evaluator.measure()", "the only source of a number",
          alpha=a_ev)
    c.arrow((73, 36.4), (73, 31.6), alpha=a_ev)

    a_on = fade(step, 5)
    c.box(14, 24.5, 26, 6.4, "method.on_measured()", "admit, discard, update, learn",
          alpha=a_on)
    c.arrow((59.4, 27.7), (40.6, 27.7), alpha=a_on)
    c.arrow((27, 31.3), (27, 36.4), alpha=a_on)

    a_seam = fade(step, 6)
    c.note(14, 18.0, "the seam:", color=TITLE, size=12, alpha=a_seam, weight="bold")
    c.note(23, 18.0,
           "a method is these two callbacks and nothing else. If adding an algorithm ever "
           "required editing the loop, the seam would be in the wrong place.",
           color=SUB, size=10.5, alpha=a_seam)

    a_which = fade(step, 7)
    c.note(14, 14.4, "MAP-Elites", color=GREEN, size=11.5, alpha=a_which, weight="bold")
    c.note(26, 14.4, "grid of elites over feature bins, replicated across islands",
           color=SUB, size=10, alpha=a_which)
    c.note(14, 11.6, "SimpleTES", color=PURPLE, size=11.5, alpha=a_which, weight="bold")
    c.note(26, 11.6, "parallel chains; one prompt makes k candidates, the best one commits",
           color=SUB, size=10, alpha=a_which)
    c.note(14, 8.8, "AnnealedIdeaCode", color=BLUE, size=11.5, alpha=a_which, weight="bold")
    c.note(31.5, 8.8, "two populations — approaches and programs — on one annealed schedule",
           color=SUB, size=10, alpha=a_which)

    if fade(step, 8):
        c.legend_model(extra="  ·  the loop itself calls none")
    return c.render()
