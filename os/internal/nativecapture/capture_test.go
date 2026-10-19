package nativecapture

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestExplicitHelperMustBeAbsoluteAndExisting(t *testing.T) {
	t.Setenv("PANTHEON_NATIVE_CAPTURE_HELPER", "relative-helper")
	if Path() != "" {
		t.Fatal("relative helper accepted")
	}
	path := filepath.Join(t.TempDir(), "helper")
	t.Setenv("PANTHEON_NATIVE_CAPTURE_HELPER", path)
	if Path() != "" {
		t.Fatal("missing helper accepted")
	}
	if err := os.WriteFile(path, []byte("fixture"), 0700); err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "darwin" || runtime.GOOS == "windows" {
		if Path() != path {
			t.Fatal("explicit helper not found")
		}
	} else if Path() != "" {
		t.Fatal("unsupported OS advertised capture")
	}
}

func TestQuPathExplicitExecutable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "QuPath")
	if err := os.WriteFile(path, []byte("fixture"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PANTHEON_QUPATH_EXECUTABLE", path)
	if QuPath() != path {
		t.Fatal("configured native executable not found")
	}
}

func TestSetupNeeded(t *testing.T) {
	missing := Status{Available: true, Interactive: true, Setup: true, ScreenRecording: true}
	for _, tc := range []struct {
		name                string
		platform            string
		status              Status
		ssh, disabled, want bool
	}{
		{"missing input", "darwin", missing, false, false, true},
		{"missing recording", "darwin", Status{Available: true, Interactive: true, Setup: true, Input: true}, false, false, true},
		{"already ready", "darwin", Status{Available: true, Interactive: true, Setup: true, ScreenRecording: true, Input: true}, false, false, false},
		{"old helper", "darwin", Status{Available: true, Interactive: true}, false, false, false},
		{"no desktop", "darwin", Status{Available: true, Setup: true}, false, false, false},
		{"unavailable", "darwin", Status{Interactive: true, Setup: true}, false, false, false},
		{"ssh", "darwin", missing, true, false, false},
		{"opt out", "darwin", missing, false, true, false},
		{"windows", "windows", missing, false, false, false},
		{"linux", "linux", missing, false, false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := SetupNeeded(tc.platform, tc.status, tc.ssh, tc.disabled); got != tc.want {
				t.Fatalf("SetupNeeded = %v, want %v", got, tc.want)
			}
		})
	}
}
