package lifecycle

import "testing"

// A model engine loading hundreds of GB may take well over ten minutes to become
// ready; readiness waits up to MaxReadinessSeconds while hooks stay short.
func TestReadinessMayWaitUpToAnHour(t *testing.T) {
	for _, tc := range []struct {
		seconds int
		ok      bool
	}{{1, true}, {600, true}, {MaxReadinessSeconds, true}, {0, false}, {MaxReadinessSeconds + 1, false}} {
		def := definition()
		def.Components[0].Readiness.TimeoutSeconds = tc.seconds
		if err := def.Validate(); (err == nil) != tc.ok {
			t.Fatalf("readiness %ds: ok=%v err=%v", tc.seconds, tc.ok, err)
		}
	}
}
