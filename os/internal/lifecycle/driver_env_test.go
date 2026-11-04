package lifecycle

import (
	"os"
	"strings"
	"testing"
)

// A node's managed Python comes first on its Apps' PATH, ahead of the
// machine's own python3.
func TestTheManagedPythonComesFirstOnTheAppsPath(t *testing.T) {
	defer UsePython("")
	t.Setenv("PATH", "/usr/bin")
	UsePython("/home/u/.pantheon/runtime/python/cpython-3.12/bin")
	var path string
	for _, kv := range cleanEnv() {
		if v, ok := strings.CutPrefix(kv, "PATH="); ok {
			path = v
		}
	}
	if path != "/home/u/.pantheon/runtime/python/cpython-3.12/bin"+string(os.PathListSeparator)+"/usr/bin" {
		t.Fatal(path)
	}
	UsePython("")
	for _, kv := range cleanEnv() {
		if strings.HasPrefix(kv, "PATH=") && kv != "PATH=/usr/bin" {
			t.Fatal(kv)
		}
	}
}

// An image's shipped App environments reach the install hooks; nothing else
// of the node's environment does.
func TestTheImagesShippedEnvironmentsReachTheInstallHooks(t *testing.T) {
	t.Setenv("PANTHEON_PYTHON_ENV_SEEDS", "/opt/pantheon/app-envs")
	t.Setenv("PANTHEON_UNRELATED_SECRET", "x")
	found := false
	for _, kv := range cleanEnv() {
		found = found || kv == "PANTHEON_PYTHON_ENV_SEEDS=/opt/pantheon/app-envs"
		if strings.HasPrefix(kv, "PANTHEON_UNRELATED_SECRET=") {
			t.Fatal(kv)
		}
	}
	if !found {
		t.Fatal("seed directory not passed")
	}
}
