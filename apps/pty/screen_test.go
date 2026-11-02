//go:build !windows

package ptyapp

import (
	"bytes"
	"strings"
	"testing"
)

// The cap falls inside a truecolor colour: the replay must not begin with
// its tail printed as text (the `7;162;231m█` a rejoined TUI showed).
func TestReplayNeverStartsMidSequence(t *testing.T) {
	l := newScreenLog(20)
	l.write([]byte(strings.Repeat("x", 20)))
	// 46 bytes: the cap's cut (26) is inside the colour (20..37).
	l.write([]byte("\x1b[38;2;7;162;231m█ after"))
	if got := string(l.replay()); got != "█ after" {
		t.Fatalf("replay should start after the cut sequence: %q", got)
	}
}

// Nor inside a multi-byte character.
func TestReplayNeverStartsMidCharacter(t *testing.T) {
	l := newScreenLog(8)
	l.write([]byte("ab█████")) // 17 bytes: the cut (9) is inside the third █
	if got := l.replay(); !bytes.Equal(got, []byte("██")) {
		t.Fatalf("replay %q", got)
	}
}

// Modes set by output that the cap cut off are said again first.
func TestReplayRestoresModesSetBeforeTheCut(t *testing.T) {
	l := newScreenLog(16)
	l.write([]byte("\x1b[?2004h\x1b[?25l\x1b[?1000;1006h\x1b="))
	l.write([]byte(strings.Repeat("y", 40)))
	got := string(l.replay())
	want := "\x1b[?25l\x1b[?1000h\x1b[?1006h\x1b[?2004h\x1b=" + strings.Repeat("y", 16)
	if got != want {
		t.Fatalf("replay\n got %q\nwant %q", got, want)
	}
	// Nothing cut off, nothing added: a shell's history replays as it was.
	l = newScreenLog(1024)
	l.write([]byte("\x1b[?2004hprompt$ "))
	if got := string(l.replay()); got != "\x1b[?2004hprompt$ " {
		t.Fatalf("an uncut log changed on replay: %q", got)
	}
}

// A full-screen program: the replay starts where it entered the alternate
// screen, or last cleared it, with the modes it had then.
func TestReplayStartsAtTheLastAltScreenPaint(t *testing.T) {
	l := newScreenLog(1 << 20)
	l.write([]byte("$ opencode\r\n\x1b[?2004l"))
	l.write([]byte("\x1b[?1049h\x1b[?25lfirst paint"))
	if got := string(l.replay()); got != "\x1b[?1049h\x1b[?25lfirst paint" {
		t.Fatalf("replay from entry %q", got)
	}
	if !l.inAlt() {
		t.Fatal("not seen in the alternate screen")
	}
	l.write([]byte("more\x1b[H\x1b[2Jsecond paint"))
	if got := string(l.replay()); got != "\x1b[?1049h\x1b[?25l\x1b[2Jsecond paint" {
		t.Fatalf("replay from the clear %q", got)
	}
	// Leaving it: the whole history again.
	l.write([]byte("\x1b[?1049l\x1b[?25h$ "))
	if l.inAlt() {
		t.Fatal("still in the alternate screen")
	}
	if got := string(l.replay()); !strings.HasPrefix(got, "$ opencode") {
		t.Fatalf("replay after leaving %q", got)
	}
}

// The entry fell off the cap: the replay still opens the alternate screen
// before what is left, and starts on a sequence boundary.
func TestReplayInAltScreenWhoseEntryWasCut(t *testing.T) {
	l := newScreenLog(64)
	l.write([]byte("\x1b[?1049h\x1b[?1002h"))
	for i := 0; i < 20; i++ {
		l.write([]byte("\x1b[38;2;7;162;231m█\x1b[0m"))
	}
	got := l.replay()
	rest, ok := bytes.CutPrefix(got, []byte("\x1b[?1049h\x1b[?1002h"))
	if !ok || !(bytes.HasPrefix(rest, []byte("█")) || bytes.HasPrefix(rest, []byte("\x1b["))) {
		t.Fatalf("replay %q", got)
	}
}

// Split across writes, a sequence is still read as one.
func TestModesAcrossChunks(t *testing.T) {
	l := newScreenLog(1 << 20)
	l.write([]byte("\x1b[?10"))
	l.write([]byte("49h"))
	if !l.inAlt() {
		t.Fatal("split ?1049h not seen")
	}
	l.write([]byte("\x1b]0;title\x07\x1b[?1049l"))
	if l.inAlt() {
		t.Fatal("?1049l after an OSC not seen")
	}
}
