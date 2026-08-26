"""X11 capture: rectangle maths and tiling. (Capture itself needs a
display, so the encode path is exercised end-to-end in the sandbox.)"""

from pantheon.toolsets.desktop.x11cast import X11Caster, tile_rect


def test_odd_dimensions_crop_even():
    c = X11Caster(":9.0", 10, 20, 1401, 901)
    assert c.rect == (10, 20, 1400, 900)


def test_move_is_a_noop_for_the_same_rect():
    c = X11Caster(":9.0", 0, 0, 800, 600)
    before = c.rect
    c.move(0, 0, 801, 601)  # rounds to the same even rect
    assert c.rect == before


def test_tiles_never_overlap():
    w, h = 1400, 900
    seen = []
    for i in range(6):
        left, top = tile_rect(i, w, h, screen_w=8192, screen_h=4608)
        box = (left, top, left + w, top + h)
        for other in seen:
            apart = (box[2] <= other[0] or other[2] <= box[0]
                     or box[3] <= other[1] or other[3] <= box[1])
            assert apart, f"tile {i} overlaps {other}"
        seen.append(box)


def test_tiles_wrap_to_the_next_row():
    w, h = 1400, 900
    _, top0 = tile_rect(0, w, h)
    _, top6 = tile_rect(6, w, h)  # 5 fit across 8192
    assert top6 > top0





def test_pool_reuses_released_slots():
    from pantheon.toolsets.desktop.x11cast import TilePool

    pool = TilePool()
    a, b, c = pool.acquire("a"), pool.acquire("b"), pool.acquire("c")
    assert {a, b, c} == {0, 1, 2}
    pool.release("b")
    # The freed slot comes back rather than the index marching on.
    assert pool.acquire("d") == b
    assert pool.acquire("a") == a  # idempotent for a live key


def test_no_room_is_explicit():
    # A window taller than the screen cannot be placed disjointly.
    assert tile_rect(0, 2000, 4000, screen_w=4096, screen_h=2000) is None
    assert tile_rect(999, 1400, 900) is None


def test_density_cap_keeps_frames_encodable():
    from pantheon.toolsets.desktop.browser import CAST_PIXEL_BUDGET, cap_density

    # A small window keeps full density.
    assert cap_density(1100, 700, 2.0) == 2.0
    # A full-width window at 2x would be 11.7 Mpx; the cap pulls it back to
    # the budget — still far above 1x, and encodable at 30fps.
    s = cap_density(2196, 1332, 2.0)
    assert 1.5 < s < 2.0
    assert 2196 * 1332 * s * s <= CAST_PIXEL_BUDGET * 1.02
    # Never below 1: a viewer always gets at least CSS resolution.
    assert cap_density(3000, 3000, 2.0) == 1.0


def test_a_slow_pipeline_still_emits_frames():
    """The regression this pins: a consumer slower than the capture rate
    must keep encoding the newest frame, not fall permanently behind and
    emit nothing (which is what rate-accounting did — the stream went
    black). Replays the staleness arithmetic without needing a display."""
    from pantheon.toolsets.desktop.x11cast import STALE_AFTER_S

    fps = 60.0
    encode_cost = 1 / 25.0   # a pipeline that can only manage 25 fps
    wall = 0.0
    media0 = None
    wall0 = None
    encoded = skipped = 0
    for i in range(300):
        media = i / fps                      # the source's own clock
        if media > wall:                     # frames cannot arrive early
            wall = media
        if wall0 is None:
            wall0, media0 = wall, media
        age = (wall - wall0) - (media - media0)
        if encoded and age > STALE_AFTER_S:
            skipped += 1
            continue
        encoded += 1
        wall += encode_cost                  # encoding costs real time

    assert encoded > 40, f"a slow pipeline still emits frames, got {encoded}"
    assert skipped > 0, "and it does skip the ones it cannot keep up with"
