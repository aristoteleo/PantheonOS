//go:build !windows

package ptyapp

import (
	"sort"
	"strconv"
	"strings"
)

// A session's scrollback is replayed into a fresh terminal when a viewer
// rejoins it. Kept as raw bytes cut at a byte cap, that replay began wherever
// the cap fell — inside an escape sequence (`7;162;231m` printed as text, the
// tail of a truecolor colour) and after the sequences that put the program's
// terminal in its state: the alternate screen, a hidden cursor, mouse
// reporting. A full-screen program then showed a wreck of its screen.
//
// screenLog keeps the bytes together with what they did to the terminal's
// modes. Its front is only ever cut where no sequence is open, and the modes
// set by what was cut off are said again before the replay. While a program
// holds the alternate screen, the replay starts where it last entered or
// cleared it: nothing before that is on screen.

// vtParser follows the escape sequences in a byte stream far enough to know
// where each one starts and ends and which DEC private modes it sets.
type vtParser struct {
	state  int
	utf8   int // continuation bytes still due
	params []byte
	inter  []byte
	modes  modeSet
	// cleared: the last byte fed ended a full screen clear (ESC[2J, ESC c).
	cleared bool
}

const (
	stGround = iota
	stEsc
	stEscInter
	stCSI
	stString    // OSC, DCS, SOS, PM, APC — until BEL or ST
	stStringEsc // ESC inside a string: ST if `\` follows
	stUTF8      // within a multi-byte UTF-8 character
)

const maxParams = 64

// modeSet is the terminal state a replay must restore: DEC private modes that
// differ from their default, and the application keypad.
type modeSet struct {
	set    map[int]bool // tracked mode -> on
	keypad bool         // DECKPAM (ESC =)
}

// tracked: the modes worth restoring, with their power-on default.
var tracked = map[int]bool{
	1:    false, // application cursor keys
	7:    true,  // autowrap
	25:   true,  // cursor visible
	47:   false, // alternate screen
	1047: false, // alternate screen
	1049: false, // alternate screen, saving the cursor
	1000: false, // mouse: clicks
	1002: false, // mouse: drags
	1003: false, // mouse: all motion
	1004: false, // focus events
	1005: false, // mouse: UTF-8 encoding
	1006: false, // mouse: SGR encoding
	1015: false, // mouse: urxvt encoding
	2004: false, // bracketed paste
}

var altModes = []int{1049, 1047, 47}

func (m modeSet) on(mode int) bool {
	if v, ok := m.set[mode]; ok {
		return v
	}
	return tracked[mode]
}

func (m modeSet) alt() bool {
	for _, n := range altModes {
		if m.on(n) {
			return true
		}
	}
	return false
}

func (m modeSet) clone() modeSet {
	c := modeSet{set: make(map[int]bool, len(m.set)), keypad: m.keypad}
	for k, v := range m.set {
		c.set[k] = v
	}
	return c
}

// prefix: the sequences that put a terminal at its defaults into this state.
// The alternate screen comes first, so the rest applies to it.
func (m modeSet) prefix() []byte {
	var b strings.Builder
	for _, n := range altModes {
		if m.on(n) {
			b.WriteString("\x1b[?" + strconv.Itoa(n) + "h")
			break
		}
	}
	var rest []int
	for n := range tracked {
		if n != 47 && n != 1047 && n != 1049 && m.on(n) != tracked[n] {
			rest = append(rest, n)
		}
	}
	sort.Ints(rest)
	for _, n := range rest {
		if m.on(n) {
			b.WriteString("\x1b[?" + strconv.Itoa(n) + "h")
		} else {
			b.WriteString("\x1b[?" + strconv.Itoa(n) + "l")
		}
	}
	if m.keypad {
		b.WriteString("\x1b=")
	}
	return []byte(b.String())
}

// ground: not inside an escape sequence or a UTF-8 character, so a replay
// may begin at the next byte.
func (p *vtParser) ground() bool { return p.state == stGround }

// feed advances the parser by one byte.
func (p *vtParser) feed(c byte) {
	p.cleared = false
	// CAN and SUB abort any sequence; ESC starts a new one (except as ST).
	if c == 0x18 || c == 0x1a {
		p.state = stGround
		return
	}
	switch p.state {
	case stUTF8:
		p.state = stGround
		if c&0xc0 == 0x80 {
			if p.utf8--; p.utf8 > 0 {
				p.state = stUTF8
			}
			return
		}
		p.feed(c) // a broken character: read this byte afresh
		return
	case stString:
		switch c {
		case 0x07:
			p.state = stGround
		case 0x1b:
			p.state = stStringEsc
		}
		return
	case stStringEsc:
		if c == '\\' {
			p.state = stGround
			return
		}
		p.state = stEsc // a new sequence, not a terminator
		p.params, p.inter = p.params[:0], p.inter[:0]
		p.feed(c)
		return
	}
	if c == 0x1b {
		p.state = stEsc
		p.params, p.inter = p.params[:0], p.inter[:0]
		return
	}
	switch p.state {
	case stGround:
		// The lead byte of a multi-byte character: how many follow.
		switch {
		case c&0xe0 == 0xc0:
			p.utf8 = 1
		case c&0xf0 == 0xe0:
			p.utf8 = 2
		case c&0xf8 == 0xf0:
			p.utf8 = 3
		default:
			return
		}
		p.state = stUTF8
	case stEsc:
		switch {
		case c == '[':
			p.state = stCSI
		case c == ']' || c == 'P' || c == 'X' || c == '^' || c == '_':
			p.state = stString
		case c >= 0x20 && c <= 0x2f:
			p.state = stEscInter
		case c == '=':
			p.modes.keypad = true
			p.state = stGround
		case c == '>':
			p.modes.keypad = false
			p.state = stGround
		case c == 'c': // RIS: everything back to its defaults
			p.modes = modeSet{set: map[int]bool{}}
			p.cleared = true
			p.state = stGround
		case c < 0x20:
			// a C0 control inside a sequence is executed; the sequence goes on
		default:
			p.state = stGround
		}
	case stEscInter:
		if c >= 0x30 && c <= 0x7e {
			p.state = stGround
		}
	case stCSI:
		switch {
		case c >= 0x30 && c <= 0x3f:
			if len(p.params) < maxParams {
				p.params = append(p.params, c)
			}
		case c >= 0x20 && c <= 0x2f:
			if len(p.inter) < maxParams {
				p.inter = append(p.inter, c)
			}
		case c >= 0x40 && c <= 0x7e:
			p.dispatch(c)
			p.state = stGround
		case c < 0x20:
			// executed in place
		default:
			p.state = stGround
		}
	}
}

func (p *vtParser) dispatch(final byte) {
	if len(p.inter) > 0 {
		return
	}
	params := string(p.params)
	if final == 'J' && params == "2" {
		p.cleared = true
		return
	}
	if (final != 'h' && final != 'l') || !strings.HasPrefix(params, "?") {
		return
	}
	for _, f := range strings.Split(params[1:], ";") {
		n, err := strconv.Atoi(f)
		if err != nil {
			continue
		}
		if _, ok := tracked[n]; ok {
			p.modes.set[n] = final == 'h'
		}
	}
}

func newParser() vtParser {
	return vtParser{modes: modeSet{set: map[int]bool{}}}
}

// screenLog is a session's capped output plus what a replay needs to show
// the screen it drew.
type screenLog struct {
	cap  int
	buf  []byte
	head vtParser // has read all of buf: the terminal's state now
	base vtParser // has read what was cut off: the state at buf[0]

	dropped int64 // bytes cut off the front so far
	// While the alternate screen is up: where (absolute offset) it was last
	// entered or cleared, and the modes just before that.
	anchor      int64
	anchorModes modeSet
	hasAnchor   bool
	seqStart    int64 // absolute offset of the ESC that began head's sequence
}

func newScreenLog(capacity int) *screenLog {
	return &screenLog{cap: capacity, head: newParser(), base: newParser()}
}

func (l *screenLog) write(data []byte) {
	for i, c := range data {
		at := l.dropped + int64(len(l.buf)+i)
		wasAlt := l.head.modes.alt()
		var before modeSet
		if c == 0x1b {
			l.seqStart = at
		}
		// Modes before this byte, kept only when it could end an anchor.
		if l.head.state == stCSI || l.head.state == stEsc {
			before = l.head.modes.clone()
		}
		l.head.feed(c)
		nowAlt := l.head.modes.alt()
		switch {
		case !wasAlt && nowAlt:
			l.anchor, l.anchorModes, l.hasAnchor = l.seqStart, before, true
		case nowAlt && l.head.cleared:
			l.anchor, l.anchorModes, l.hasAnchor = l.seqStart, before, true
		case !nowAlt:
			l.hasAnchor = false
		}
	}
	l.buf = append(l.buf, data...)
	if over := len(l.buf) - l.cap; over > 0 {
		l.trim(over)
	}
}

// trim cuts at least n bytes off the front, then on to where no sequence or
// character is open.
func (l *screenLog) trim(n int) {
	i := 0
	for ; i < len(l.buf) && (i < n || !l.base.ground()); i++ {
		l.base.feed(l.buf[i])
	}
	l.dropped += int64(i)
	l.buf = append(l.buf[:0:0], l.buf[i:]...)
}

// inAlt reports whether the program holds the alternate screen.
func (l *screenLog) inAlt() bool { return l.head.modes.alt() }

// replay is what a fresh terminal must be fed to show this session's screen.
func (l *screenLog) replay() []byte {
	if l.hasAnchor && l.inAlt() && l.anchor >= l.dropped {
		from := l.buf[l.anchor-l.dropped:]
		return append(l.anchorModes.prefix(), from...)
	}
	return append(l.base.modes.prefix(), l.buf...)
}
