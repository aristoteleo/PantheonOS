"""Translate the existing desktop_act pixel/chord API, validating before input."""
import math
import re

ALIASES = {'Ctrl': 'ControlLeft', 'Control': 'ControlLeft', 'Alt': 'AltLeft', 'Shift': 'ShiftLeft',
    'Meta': 'MetaLeft', 'Super': 'MetaLeft', 'Command': 'MetaLeft', 'Return': 'Enter', 'Esc': 'Escape',
    'Left': 'ArrowLeft', 'Right': 'ArrowRight', 'Up': 'ArrowUp', 'Down': 'ArrowDown', ' ': 'Space'}
MODIFIERS = {'ControlLeft': 'ctrl', 'ShiftLeft': 'shift', 'AltLeft': 'alt', 'MetaLeft': 'meta'}
KEYS = {'Enter', 'Escape', 'Tab', 'Space', 'Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown',
    'Home', 'End', 'PageUp', 'PageDown', 'Minus', 'Equal', 'BracketLeft', 'BracketRight', 'Backslash',
    'Semicolon', 'Quote', 'Comma', 'Period', 'Slash', 'Backquote', *MODIFIERS}


def physical(name):
    name = ALIASES.get(name, name)
    if len(name) == 1 and name.isascii() and name.isalpha(): return 'Key' + name.upper()
    if len(name) == 1 and name.isdecimal(): return 'Digit' + name
    if name in KEYS or re.fullmatch(r'(?:Key[A-Z]|Digit[0-9]|F(?:[1-9]|1[0-2]))', name): return name
    raise ValueError('Unsupported native key: ' + name)


def batch(actions, width, height):
    if not isinstance(actions, list) or not 1 <= len(actions) <= 32:
        raise ValueError('actions must contain 1–32 actions')
    result = []
    text_size = 0
    def point(a, x='x', y='y'):
        px, py = a.get(x), a.get(y)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (px, py)):
            raise ValueError('Native coordinates must be finite numbers')
        if not 0 <= px < width or not 0 <= py < height:
            raise ValueError('Coordinates are outside the native window screenshot')
        return {'x': px / width, 'y': py / height}
    for a in actions:
        kind = a.get('type')
        if kind in ('click', 'dblclick', 'rightclick', 'move', 'drag'):
            pos = point(a)
            button = {'left': 0, 'middle': 1, 'right': 2}.get(a.get('button', 'left'))
            if button is None: raise ValueError('Unsupported mouse button')
            if kind == 'rightclick': button = 2
            event = {'kind': 'pointer', **pos, 'button': button, 'clicks': 1}
            result.append({**event, 'phase': 'move', 'buttons': 0})
            if kind != 'move':
                mask = {0: 1, 1: 4, 2: 2}[button]
                result.append({**event, 'phase': 'down', 'buttons': mask})
                if kind == 'drag':
                    end = point(a, 'to_x', 'to_y')
                    duration = a.get('duration_ms', 300)
                    if not isinstance(duration, (int, float)) or not 0 <= duration <= 2000: raise ValueError('Invalid drag duration')
                    for step in range(1, 11):
                        result.append({**event, 'x': pos['x'] + (end['x'] - pos['x']) * step / 10,
                            'y': pos['y'] + (end['y'] - pos['y']) * step / 10, 'phase': 'move', 'buttons': mask,
                            '_delay': duration / 10000})
                    event.update(end)
                result.append({**event, 'phase': 'up', 'buttons': 0})
                if kind == 'dblclick':
                    result.append({**event, 'phase': 'down', 'buttons': mask, 'clicks': 2})
                    result.append({**event, 'phase': 'up', 'buttons': 0, 'clicks': 2})
        elif kind == 'wheel':
            result.append({'kind': 'pointer', 'phase': 'move', 'buttons': 0, **point(a)})
            deltas = [a.get(key, 0) for key in ('delta_x', 'delta_y')]
            if any(isinstance(v, bool) or not isinstance(v, int) or abs(v) > 100 for v in deltas): raise ValueError('Wheel steps must be integers from -100 to 100')
            result.append({'kind': 'wheel', 'dx': deltas[0] * 120, 'dy': deltas[1] * 120})
        elif kind == 'key':
            names = a.get('key', '').split('+')
            codes = [physical(name) for name in names]
            if len(codes) > 5 or any(code not in MODIFIERS for code in codes[:-1]): raise ValueError('Invalid key chord')
            held = []
            for code in codes:
                if code in MODIFIERS: held.append(MODIFIERS[code])
                result.append({'kind': 'key', 'code': code, 'down': True, 'modifiers': held.copy()})
            for code in reversed(codes):
                if code in MODIFIERS: held.remove(MODIFIERS[code])
                result.append({'kind': 'key', 'code': code, 'down': False, 'modifiers': held.copy()})
        elif kind == 'text':
            value = a.get('text')
            if not isinstance(value, str): raise ValueError('Text must be a string')
            text_size += len(value)
            if text_size > 2000: raise ValueError('Text batch exceeds 2000 characters')
            # Keep helper commands below the UTF-8 byte limit.
            for offset in range(0, len(value), 512): result.append({'kind': 'text', 'text': value[offset:offset+512]})
        else: raise ValueError('Unsupported native action')
    return result
