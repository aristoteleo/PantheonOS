package main

import (
	"path/filepath"
	"testing"
)

// Opening the app resumes the default state directory's node; another fleet's
// membership keeps its reopen settings to itself.
func TestAppLaunchSettingsStayWithTheirMembership(t *testing.T) {
	base := t.TempDir()
	if got := appLaunchPath(base, base); got != bundleResumePath() {
		t.Fatalf("base membership saves to %q", got)
	}
	other := filepath.Join(base, "fleets", "f_b")
	if got := appLaunchPath(base, other); got != filepath.Join(other, "macos-launch.json") {
		t.Fatalf("other membership saves to %q", got)
	}
}
