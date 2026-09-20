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
