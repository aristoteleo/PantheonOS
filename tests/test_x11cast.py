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
