//go:build !windows

package ptyapp

import (
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// A coding agent's terminal launch tells the Coding Agents service whether
// the agent is working by what its screen does, for agents that report
// nothing themselves. Output alone says little: a cursor that blinks or
// moves, a program that redraws the same screen, a clock that ticks once a
// second all write bytes. activityMeter keeps a model of the screen's cells
// and counts the cells that really changed in each half second: the agent is
// working while most of the last two seconds changed it (streamed text, a
// spinner), and idle once that has stopped for a moment. What the user types
// (echoed back) and the repaint after a resize do not count.
//
// Two things a program says to its terminal are read too: a desktop
// notification (OSC 9, OSC 777;notify), and the agent state some agents
// report (OSC 7501;state=working|idle|blocked…, which Codex sends).

const (
	activityTick   = 500 * time.Millisecond
	activityWindow = 4 // ticks: the last two seconds
	activityBusy   = 3 // changed ticks in the window that make it working
	activitySettle = 3 // ticks below that before it is idle again
	inputQuiet     = 400 * time.Millisecond
	resizeQuiet    = 1500 * time.Millisecond
	maxGridCells   = 500 * 1000
)

// activityStartup: an agent's first seconds draw its interface, which is not
// work.
var activityStartup = 4 * time.Second

type activityMeter struct {
	mu         sync.Mutex
	rows, cols int
	grid, prev [][]rune
	row, col   int
	savedRow   int
	savedCol   int
	top, bot   int // scroll region, inclusive
	// escape sequence parsing
	state  int
	params []byte
	osc    []byte
	pend   []byte // an incomplete UTF-8 character
	// what the last ticks saw
	dirty   bool
	recent  []bool
	working bool
	calm    int
	quiet   time.Time // changes before this do not count
	signals []codingagents.Activity
}

func newActivityMeter(cols, rows int, now time.Time) *activityMeter {
	m := &activityMeter{quiet: now.Add(activityStartup)}
	m.reset(cols, rows)
	return m
}

func (m *activityMeter) reset(cols, rows int) {
	if cols <= 0 || rows <= 0 || cols*rows > maxGridCells {
		cols, rows = 80, 24
	}
	m.cols, m.rows = cols, rows
	m.grid, m.prev = blankGrid(cols, rows), blankGrid(cols, rows)
	m.row, m.col, m.top, m.bot = 0, 0, 0, rows-1
}

func blankGrid(cols, rows int) [][]rune {
	g := make([][]rune, rows)
	for i := range g {
		g[i] = blankRow(cols)
	}
	return g
}

func blankRow(cols int) []rune {
	r := make([]rune, cols)
	for i := range r {
		r[i] = ' '
	}
	return r
}

// typed: the user typed; the echo that follows is theirs.
func (m *activityMeter) typed(now time.Time) {
	m.mu.Lock()
	m.quiet = later(m.quiet, now.Add(inputQuiet))
	m.mu.Unlock()
}

// resized: the program repaints for the new size.
func (m *activityMeter) resized(cols, rows int, now time.Time) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.quiet = later(m.quiet, now.Add(resizeQuiet))
	if cols != m.cols || rows != m.rows {
		m.reset(cols, rows)
	}
}

func later(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}

// tick closes a half second: whether the agent is working now, whether that
// changed, and what the program said meanwhile (notifications, its state).
func (m *activityMeter) tick(now time.Time) (working, changed bool, signals []codingagents.Activity) {
	m.mu.Lock()
	defer m.mu.Unlock()
	moved := false
	if m.dirty {
		for r := range m.grid {
			if !slices.Equal(m.grid[r], m.prev[r]) {
				moved = true
				copy(m.prev[r], m.grid[r])
			}
		}
		m.dirty = false
	}
	if now.Before(m.quiet) {
		// What changed is the user's or the resize's: this half second
		// says nothing either way.
		signals, m.signals = m.signals, nil
		return m.working, false, signals
	}
	m.recent = append(m.recent, moved)
	if len(m.recent) > activityWindow {
		m.recent = m.recent[len(m.recent)-activityWindow:]
	}
	n := 0
	for _, v := range m.recent {
		if v {
			n++
		}
	}
	was := m.working
	switch {
	case n >= activityBusy:
		m.working, m.calm = true, 0
	case m.working:
		if m.calm++; m.calm >= activitySettle {
			m.working = false
		}
	}
	signals, m.signals = m.signals, nil
	return m.working, m.working != was, signals
}

// ---- the screen ------------------------------------------------------------

const (
	vtGround = iota
	vtEsc
	vtEscInter
	vtCSI
	vtOSC
	vtOSCEsc
	vtString // DCS, SOS, PM, APC: skipped until ST
	vtStringEsc
)

func (m *activityMeter) write(p []byte) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.dirty = true
	if len(m.pend) > 0 {
		p = append(m.pend, p...)
		m.pend = nil
	}
	for i := 0; i < len(p); {
		b := p[i]
		if m.state != vtGround || b < 0x80 {
			m.byte(b)
			i++
			continue
		}
		r, size := utf8.DecodeRune(p[i:])
		if r == utf8.RuneError && size <= 1 {
			if !utf8.FullRune(p[i:]) {
				m.pend = append([]byte(nil), p[i:]...)
				return
			}
			i++
			continue
		}
		m.put(r)
		i += size
	}
}

func (m *activityMeter) byte(b byte) {
	switch m.state {
	case vtGround:
		switch {
		case b == 0x1b:
			m.state = vtEsc
		case b == '\r':
			m.col = 0
		case b == '\n', b == 0x0b, b == 0x0c:
			m.lineFeed()
		case b == '\b':
			if m.col > 0 {
				m.col--
			}
		case b == '\t':
			m.col = min((m.col/8+1)*8, m.cols-1)
		case b >= 0x20 && b != 0x7f:
			m.put(rune(b))
		}
	case vtEsc:
		switch b {
		case '[':
			m.state, m.params = vtCSI, m.params[:0]
		case ']':
			m.state, m.osc = vtOSC, m.osc[:0]
		case 'P', 'X', '^', '_':
			m.state = vtString
		case '7':
			m.savedRow, m.savedCol, m.state = m.row, m.col, vtGround
		case '8':
			m.row, m.col, m.state = m.savedRow, m.savedCol, vtGround
		case 'D':
			m.lineFeed()
			m.state = vtGround
		case 'E':
			m.col = 0
			m.lineFeed()
			m.state = vtGround
		case 'M':
			m.reverseFeed()
			m.state = vtGround
		case 'c':
			m.reset(m.cols, m.rows)
			m.state = vtGround
		default:
			if b >= 0x20 && b <= 0x2f {
				m.state = vtEscInter
			} else {
				m.state = vtGround
			}
		}
	case vtEscInter:
		if b >= 0x30 {
			m.state = vtGround
		}
	case vtCSI:
		switch {
		case b >= 0x40 && b <= 0x7e:
			m.csi(b)
			m.state = vtGround
		case b == 0x1b:
			m.state = vtEsc
		case len(m.params) < 64:
			m.params = append(m.params, b)
		}
	case vtOSC:
		switch {
		case b == 0x07:
			m.oscDone()
		case b == 0x1b:
			m.state = vtOSCEsc
		case len(m.osc) < 4096:
			m.osc = append(m.osc, b)
		}
	case vtOSCEsc:
		if b == '\\' {
			m.oscDone()
		} else {
			m.state = vtGround
		}
	case vtString:
		if b == 0x1b {
			m.state = vtStringEsc
		} else if b == 0x07 {
			m.state = vtGround
		}
	case vtStringEsc:
		if b == '\\' {
			m.state = vtGround
		} else {
			m.state = vtString
		}
	}
}

// oscDone takes a notification — OSC 9;text (iTerm2, Codex) or OSC
// 777;notify;title;body; OSC 9;4;… is ConEmu's progress, not one — or an
// agent's state: OSC 7501;state=S[:key=value…] (its kind says what a blocked
// agent waits for).
func (m *activityMeter) oscDone() {
	m.state = vtGround
	s := string(m.osc)
	if len(m.signals) >= 16 {
		return
	}
	if rest, ok := strings.CutPrefix(s, "7501;"); ok {
		fields := map[string]string{}
		for _, f := range strings.Split(rest, ":") {
			if k, v, ok := strings.Cut(f, "="); ok {
				fields[k] = v
			}
		}
		state := map[string]string{"working": "working", "busy": "working", "idle": "ready", "done": "ready",
			"blocked": "waiting", "waiting": "waiting"}[fields["state"]]
		if state != "" {
			m.signals = append(m.signals, codingagents.Activity{State: state, Detail: fields["kind"]})
		}
		return
	}
	var text string
	if rest, ok := strings.CutPrefix(s, "9;"); ok {
		if code, _, _ := strings.Cut(rest, ";"); code != "" && strings.Trim(code, "0123456789") == "" {
			return
		}
		text = rest
	} else if rest, ok := strings.CutPrefix(s, "777;notify;"); ok {
		title, body, _ := strings.Cut(rest, ";")
		text = strings.TrimSpace(title + ": " + body)
		text = strings.TrimSuffix(strings.TrimPrefix(text, ": "), ":")
	}
	if text = strings.TrimSpace(text); text != "" {
		m.signals = append(m.signals, codingagents.Activity{Notice: text})
	}
}

func (m *activityMeter) put(r rune) {
	if m.col >= m.cols {
		m.col = 0
		m.lineFeed()
	}
	if m.row >= 0 && m.row < m.rows {
		m.grid[m.row][m.col] = r
	}
	m.col++
}

func (m *activityMeter) lineFeed() {
	if m.row == m.bot {
		m.scrollUp(1)
	} else if m.row < m.rows-1 {
		m.row++
	}
}

func (m *activityMeter) reverseFeed() {
	if m.row == m.top {
		m.scrollDown(1)
	} else if m.row > 0 {
		m.row--
	}
}

func (m *activityMeter) scrollUp(n int) {
	for ; n > 0; n-- {
		copy(m.grid[m.top:m.bot+1], m.grid[m.top+1:m.bot+1])
		m.grid[m.bot] = blankRow(m.cols)
	}
}

func (m *activityMeter) scrollDown(n int) {
	for ; n > 0; n-- {
		copy(m.grid[m.top+1:m.bot+1], m.grid[m.top:m.bot])
		m.grid[m.top] = blankRow(m.cols)
	}
}

func (m *activityMeter) csi(final byte) {
	raw := string(m.params)
	private := strings.HasPrefix(raw, "?")
	if private || strings.HasPrefix(raw, ">") || strings.HasPrefix(raw, "<") || strings.HasPrefix(raw, "=") {
		raw = raw[1:]
	}
	if private && (final == 'h' || final == 'l') {
		for _, p := range strings.Split(raw, ";") {
			if p == "1049" || p == "1047" || p == "47" {
				// Another screen: what is on it now is what the program draws next.
				m.grid, m.row, m.col = blankGrid(m.cols, m.rows), 0, 0
				m.top, m.bot = 0, m.rows-1
			}
		}
		return
	}
	if private || strings.ContainsAny(raw, " !\"$'") {
		return
	}
	var ps []int
	for _, p := range strings.Split(raw, ";") {
		n, _ := strconv.Atoi(strings.SplitN(p, ":", 2)[0])
		ps = append(ps, n)
	}
	arg := func(i, def int) int {
		if i < len(ps) && ps[i] > 0 {
			return ps[i]
		}
		return def
	}
	clampRow := func(r int) int { return max(0, min(r, m.rows-1)) }
	clampCol := func(c int) int { return max(0, min(c, m.cols-1)) }
	switch final {
	case 'A':
		m.row = clampRow(m.row - arg(0, 1))
	case 'B', 'e':
		m.row = clampRow(m.row + arg(0, 1))
	case 'C', 'a':
		m.col = clampCol(m.col + arg(0, 1))
	case 'D':
		m.col = clampCol(m.col - arg(0, 1))
	case 'E':
		m.row, m.col = clampRow(m.row+arg(0, 1)), 0
	case 'F':
		m.row, m.col = clampRow(m.row-arg(0, 1)), 0
	case 'G', '`':
		m.col = clampCol(arg(0, 1) - 1)
	case 'd':
		m.row = clampRow(arg(0, 1) - 1)
	case 'H', 'f':
		m.row, m.col = clampRow(arg(0, 1)-1), clampCol(arg(1, 1)-1)
	case 'J':
		m.eraseDisplay(arg(0, 0))
	case 'K':
		m.eraseLine(arg(0, 0))
	case 'X':
		m.blank(m.row, m.col, min(m.cols, m.col+arg(0, 1)))
	case 'P':
		if m.row < m.rows {
			line, n := m.grid[m.row], min(arg(0, 1), m.cols-m.col)
			copy(line[m.col:], line[m.col+n:])
			m.blank(m.row, m.cols-n, m.cols)
		}
	case '@':
		if m.row < m.rows {
			line, n := m.grid[m.row], min(arg(0, 1), m.cols-m.col)
			copy(line[m.col+n:], line[m.col:m.cols-n])
			m.blank(m.row, m.col, m.col+n)
		}
	case 'L', 'M':
		if m.row < m.top || m.row > m.bot {
			return
		}
		top := m.top
		m.top = m.row
		if final == 'L' {
			m.scrollDown(min(arg(0, 1), m.bot-m.row+1))
		} else {
			m.scrollUp(min(arg(0, 1), m.bot-m.row+1))
		}
		m.top = top
	case 'S':
		m.scrollUp(min(arg(0, 1), m.bot-m.top+1))
	case 'T':
		m.scrollDown(min(arg(0, 1), m.bot-m.top+1))
	case 'r':
		top, bot := arg(0, 1)-1, arg(1, m.rows)-1
		if top < bot && bot < m.rows {
			m.top, m.bot = top, bot
		} else {
			m.top, m.bot = 0, m.rows-1
		}
		m.row, m.col = 0, 0
	case 's':
		m.savedRow, m.savedCol = m.row, m.col
	case 'u':
		m.row, m.col = m.savedRow, m.savedCol
	}
}

func (m *activityMeter) blank(row, from, to int) {
	if row < 0 || row >= m.rows {
		return
	}
	for c := max(0, from); c < min(to, m.cols); c++ {
		m.grid[row][c] = ' '
	}
}

func (m *activityMeter) eraseLine(mode int) {
	switch mode {
	case 0:
		m.blank(m.row, m.col, m.cols)
	case 1:
		m.blank(m.row, 0, m.col+1)
	case 2:
		m.blank(m.row, 0, m.cols)
	}
}

func (m *activityMeter) eraseDisplay(mode int) {
	switch mode {
	case 0:
		m.eraseLine(0)
		for r := m.row + 1; r < m.rows; r++ {
			m.blank(r, 0, m.cols)
		}
	case 1:
		for r := 0; r < m.row; r++ {
			m.blank(r, 0, m.cols)
		}
		m.eraseLine(1)
	case 2, 3:
		for r := 0; r < m.rows; r++ {
			m.blank(r, 0, m.cols)
		}
	}
}

// ---- the PTY session's side ------------------------------------------------

// watchActivity measures the screen of a session that runs a coding agent's
// terminal launch, and reports it until the session ends.
func (s *ptySession) watchActivity(terminal string) {
	s.meter = newActivityMeter(s.cols, s.rows, time.Now())
	go func() {
		t := time.NewTicker(activityTick)
		defer t.Stop()
		for {
			select {
			case <-s.waitDone:
				codingagents.ReportActivity(codingagents.Activity{Terminal: terminal, Closed: true})
				return
			case now := <-t.C:
				// The screen first: what the program said comes after, and
				// outranks it.
				working, changed, signals := s.meter.tick(now)
				if changed {
					codingagents.ReportActivity(codingagents.Activity{Terminal: terminal, Working: working})
				}
				for _, sig := range signals {
					sig.Terminal = terminal
					codingagents.ReportActivity(sig)
				}
			}
		}
	}()
}
