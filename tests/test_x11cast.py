"""X11 capture: rectangle maths and tiling. (Capture itself needs a
display, so the encode path is exercised end-to-end in the sandbox.)"""

from pantheon.toolsets.desktop.x11cast import TilePool, X11Caster


def test_odd_dimensions_crop_even():
    c = X11Caster(":9.0", 10, 20, 1401, 901)
    assert c.rect == (10, 20, 1400, 900)


def test_move_is_a_noop_for_the_same_rect():
    c = X11Caster(":9.0", 0, 0, 800, 600)
    before = c.rect
    c.move(0, 0, 801, 601)  # rounds to the same even rect
    assert c.rect == before


def test_windows_of_different_sizes_never_overlap():
    """The bug this class exists to prevent, in the shape it actually took.

    A grid indexed by slot is only disjoint while every window is the same
    size: a 1280-wide window's third cell landed at x=2576, inside a
    4136-wide window's first, and the smaller one's capture returned the
    larger one's pixels — or the bare desk between them.
    """
    pool = TilePool()
    sizes = [(4136, 2358), (1280, 980), (4136, 2358), (2200, 1500), (900, 700)]
    placed = []
    for i, (w, h) in enumerate(sizes):
        spot = pool.place(f"w{i}", w, h)
        assert spot is not None, f"window {i} found no room"
        box = (spot[0], spot[1], w, h)
        for other in placed:
            apart = (box[0] + box[2] <= other[0] or other[0] + other[2] <= box[0]
                     or box[1] + box[3] <= other[1] or other[1] + other[3] <= box[1])
            assert apart, f"window {i} at {box} overlaps {other}"
        placed.append(box)


def test_a_resize_moves_the_rectangle_rather_than_keeping_both():
    pool = TilePool()
    pool.place("a", 1000, 800)
    pool.place("b", 1000, 800)
    before = pool.rect("b")
    # 'a' grows: it must not still be holding its old, smaller rectangle.
    pool.place("a", 4000, 3000)
    assert pool.rect("b") == before
    a = pool.rect("a")
    assert a is not None and (a[2], a[3]) == (4000, 3000)
    apart = (a[0] + a[2] <= before[0] or before[0] + before[2] <= a[0]
             or a[1] + a[3] <= before[1] or before[1] + before[3] <= a[1])
    assert apart, "the resized window landed on its neighbour"


def test_the_display_holds_several_full_size_retina_windows():
    """A 2300x1350 window at 2x — the size real use actually asks for.

    The display was once exactly one such window wide, so the second
    window silently lost its place and dropped to the slow path.
    """
    pool = TilePool()
    w, h = 2300 * 2, 1350 * 2 + 180  # physical, chrome included
    assert all(pool.place(f"w{i}", w, h) is not None for i in range(4))


def test_released_room_comes_back():
    pool = TilePool()
    first = pool.place("a", 2000, 1500)
    pool.place("b", 2000, 1500)
    pool.release("a")
    assert pool.place("c", 2000, 1500) == first


def test_no_room_is_explicit():
    # Taller than the display: there is nowhere to put it, and saying so
    # is the whole point — a silent stack at the origin is what made one
    # window stream another's pixels.
    small = TilePool(screen_w=4096, screen_h=2000)
    assert small.place("a", 2000, 4000) is None


def test_h264_stays_inside_the_link_budget():
    """Hard motion must cost bandwidth the tunnel actually has.

    Under plain CRF the encoder ignores bit_rate completely: scrolling
    text measured over 200 Mbps against a ~20 Mbps shared tunnel. The
    socket blocked, captured frames aged out before they could be sent,
    and a pipeline that encodes at 130 fps delivered five. The ceiling is
    the encoder's job, so this asserts the encoder does it — and checks
    the unconstrained settings really would blow past it, so the test
    cannot quietly stop meaning anything.
    """
    import fractions

    import numpy as np
    import av

    from pantheon.toolsets.desktop.x11cast import CLOCK, H264_OPTS, LINK_BITRATE

    def measured_bps(params: str) -> float:
        w, h, fps, n = 1280, 720, 30, 45
        enc = av.CodecContext.create("libx264", "w")
        enc.width, enc.height, enc.pix_fmt = w, h, "yuv420p"
        enc.time_base = fractions.Fraction(1, CLOCK)
        enc.options = {k: v for k, v in H264_OPTS.items() if k != "x264-params"}
        enc.options["x264-params"] = params
        rng = np.random.default_rng(0)
        tall = (rng.random((h + 300, w, 3)) * 255).astype(np.uint8)
        total = 0
        for i in range(n):
            off = (i * 30) % 300
            frame = av.VideoFrame.from_ndarray(
                np.ascontiguousarray(tall[off:off + h]), format="rgb24")
            frame = frame.reformat(format="yuv420p")
            frame.pts = int(i * CLOCK / fps)
            for pk in enc.encode(frame):
                total += pk.size
        return total * 8 / (n / fps)

    capped = measured_bps(H264_OPTS["x264-params"])
    assert capped <= LINK_BITRATE * 1.25, f"{capped/1e6:.1f} Mbps exceeds the cap"

    uncapped = measured_bps("repeat-headers=1:keyint=120:scenecut=0")
    assert uncapped > LINK_BITRATE * 2, (
        "unconstrained encoding no longer overshoots; this test proves nothing")
