package hosted

import (
	"testing"
	"time"
)

// Combined check 2: a tool waiting for its provider asks every 3 s; the
// refusal is logged once per owner and method per StartingLogEvery.
func TestProviderStartingIsLoggedOncePerPeriod(t *testing.T) {
	l := &logLimiter{}
	start := time.Unix(1_800_000_000, 0)
	logged := 0
	for i := 0; i < 100; i++ { // a five-minute wait
		if l.due("f1|allocator|bind_dependencies", start.Add(time.Duration(i)*3*time.Second)) {
			logged++
		}
	}
	if logged != 1 {
		t.Fatalf("logged %d times in 5 minutes", logged)
	}
	if !l.due("f2|allocator|bind_dependencies", start) || !l.due("f1|allocator|bind_dependencies", start.Add(StartingLogEvery)) {
		t.Fatal("another owner, or the next period, is logged")
	}
}
