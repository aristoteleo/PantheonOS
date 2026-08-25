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
        left, top = tile_rect(i, w, h, screen_w=5120, screen_h=3200)
        box = (left, top, left + w, top + h)
        for other in seen:
            apart = (box[2] <= other[0] or other[2] <= box[0]
                     or box[3] <= other[1] or other[3] <= box[1])
            assert apart, f"tile {i} overlaps {other}"
        seen.append(box)


def test_tiles_wrap_to_the_next_row():
    w, h = 1400, 900
    _, top0 = tile_rect(0, w, h)
    _, top3 = tile_rect(3, w, h)  # 3 fit across 5120
    assert top3 > top0


def test_out_of_room_falls_back_to_the_origin():
    # A window taller than the screen has nowhere disjoint to go.
    assert tile_rect(9, 2000, 1500, screen_w=4096, screen_h=2000) == (0, 0)
