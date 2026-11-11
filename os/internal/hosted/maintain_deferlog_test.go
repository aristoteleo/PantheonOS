package hosted

import (
	"bytes"
	"errors"
	"log"
	"os"
	"strings"
	"testing"
	"time"
)

// Combined check 1: a deferral that goes on is logged when it starts, then
// once every DeferLogEvery, and once when it ends.
func TestMaintenanceDeferralIsLoggedOncePerChange(t *testing.T) {
	var out bytes.Buffer
	log.SetOutput(&out)
	defer log.SetOutput(os.Stderr)
	defer log.SetFlags(log.Flags())
	log.SetFlags(0)
	a := &Allocator{}
	start := time.Unix(1_800_000_000, 0)
	gone := errors.New("nats: no responders available for request")
	for i := 0; i < 200; i++ { // 200 passes 3 s apart: 10 minutes
		a.deferLog("f1", "binding", "/j/a", gone, start.Add(time.Duration(i)*3*time.Second))
	}
	if n := strings.Count(out.String(), "\n"); n != 1 {
		t.Fatalf("logged %d lines in 10 minutes, want 1:\n%s", n, out.String())
	}
	a.deferLog("f1", "binding", "/j/a", gone, start.Add(DeferLogEvery+time.Second))
	if !strings.Contains(out.String(), "still deferred (200 passes") {
		t.Fatalf("the reminder does not say how long: %s", out.String())
	}
	a.deferLog("f1", "binding", "/j/a", errors.New("other"), start.Add(DeferLogEvery+2*time.Second))
	a.deferLog("f1", "binding", "/j/b", gone, start.Add(DeferLogEvery+2*time.Second))
	a.deferResolved("f1", "binding", "/j/a")
	a.deferResolved("f1", "binding", "/j/c") // never deferred: nothing
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 5 || !strings.HasSuffix(lines[4], "binding maintenance resumed") {
		t.Fatalf("lines: %q", lines)
	}
}
