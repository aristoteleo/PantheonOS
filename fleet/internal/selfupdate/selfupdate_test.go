package selfupdate

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestVersionsCompareByReleaseOrder(t *testing.T) {
	v := func(s string) Version {
		out, err := ParseVersion(s)
		if err != nil {
			t.Fatal(err)
		}
		return out
	}
	cases := []struct {
		a, b  string
		newer bool
	}{
		{"0.5.0-model.6", "0.5.0-model.5", true},
		{"0.5.0-model.10", "0.5.0-model.9", true},
		{"0.5.0-model.5", "0.5.0-model.5", false},
		{"0.5.0-model.4", "0.5.0-model.5", false},
		{"0.5.0", "0.5.0-model.9", true},
		{"0.6.0-model.1", "0.5.9", true},
	}
	for _, c := range cases {
		if got := Newer(v(c.a), v(c.b)); got != c.newer {
			t.Fatalf("Newer(%s, %s) = %v", c.a, c.b, got)
		}
	}
	for _, bad := range []string{"v0.5.0", "fleet-v0.5.0-model.6; rm -rf /", "fleet-v0.5", "../fleet-v1.0.0"} {
		if _, err := ParseTag(bad); err == nil {
			t.Fatalf("accepted tag %q", bad)
		}
	}
}

// release serves files and a SHA256SUMS for one tag, like a GitHub release.
func release(t *testing.T, tag string, files map[string][]byte, lie string) {
	t.Helper()
	var sums strings.Builder
	for name, body := range files {
		sum := sha256.Sum256(body)
		digest := hex.EncodeToString(sum[:])
		if name == lie {
			digest = strings.Repeat("0", 64)
		}
		fmt.Fprintf(&sums, "%s  %s\n", digest, name)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		name := strings.TrimPrefix(r.URL.Path, "/"+tag+"/")
		if name == "SHA256SUMS" {
			w.Write([]byte(sums.String()))
			return
		}
		if body, ok := files[name]; ok && strings.HasPrefix(r.URL.Path, "/"+tag+"/") {
			w.Write(body)
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(server.Close)
	old := ReleaseBase
	ReleaseBase = server.URL + "/"
	t.Cleanup(func() { ReleaseBase = old })
}

func TestUpdatesTheBinaryOnlyWhenNewerIdleAndVerified(t *testing.T) {
	dir := t.TempDir()
	exe := filepath.Join(dir, "fleet")
	os.WriteFile(exe, []byte("old"), 0o755)
	locate := func() (Target, error) { return Target{Executable: exe, Asset: "fleet-linux-amd64"}, nil }
	release(t, "fleet-v0.5.0-model.6", map[string][]byte{"fleet-linux-amd64": []byte("new")}, "")
	ctx := context.Background()

	busy := "a task is running"
	u := &Updater{Current: "0.5.0-model.5", Busy: func() string { return busy }, Locate: locate}
	res, _, err := u.Apply(ctx, "fleet-v0.5.0-model.6")
	if err != nil || res.Status != "deferred" || res.Reason != busy {
		t.Fatalf("busy node: %+v %v", res, err)
	}
	if res, _, _ := u.Apply(ctx, "fleet-v0.5.0-model.4"); res.Status != "up_to_date" {
		t.Fatalf("downgrade not refused: %+v", res)
	}
	busy = ""
	res, path, err := u.Apply(ctx, "fleet-v0.5.0-model.6")
	if err != nil || res.Status != "updated" || path != exe {
		t.Fatalf("update: %+v %q %v", res, path, err)
	}
	if b, _ := os.ReadFile(exe); string(b) != "new" {
		t.Fatalf("binary not replaced: %q", b)
	}
	if b, _ := os.ReadFile(exe + ".previous"); string(b) != "old" {
		t.Fatalf("previous binary not kept: %q", b)
	}
	if info, _ := os.Stat(exe); info.Mode().Perm()&0o100 == 0 {
		t.Fatal("updated binary is not executable")
	}
}

func TestRefusesAFileThatDoesNotMatchTheReleaseChecksum(t *testing.T) {
	dir := t.TempDir()
	exe := filepath.Join(dir, "fleet")
	os.WriteFile(exe, []byte("old"), 0o755)
	release(t, "fleet-v0.5.0-model.6", map[string][]byte{"fleet-linux-amd64": []byte("tampered")}, "fleet-linux-amd64")
	u := &Updater{Current: "0.5.0-model.5",
		Locate: func() (Target, error) { return Target{Executable: exe, Asset: "fleet-linux-amd64"}, nil }}
	if _, _, err := u.Apply(context.Background(), "fleet-v0.5.0-model.6"); err == nil || !strings.Contains(err.Error(), "checksum") {
		t.Fatalf("accepted a mismatching file: %v", err)
	}
	if b, _ := os.ReadFile(exe); string(b) != "old" {
		t.Fatal("working binary was touched")
	}
	if left, _ := filepath.Glob(filepath.Join(dir, ".fleet-update-*")); len(left) != 0 {
		t.Fatalf("temporary files left: %v", left)
	}
}

func TestSwapsTheWholeMacAppBundle(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("ditto is macOS only")
	}
	dir := t.TempDir()
	bundle := filepath.Join(dir, "Pantheon Fleet.app")
	os.MkdirAll(filepath.Join(bundle, "Contents", "MacOS"), 0o755)
	os.WriteFile(filepath.Join(bundle, "Contents", "MacOS", "fleet"), []byte("old"), 0o755)
	// Build a release zip the way CI does (ditto -c -k --keepParent).
	src := filepath.Join(t.TempDir(), "Pantheon Fleet.app")
	os.MkdirAll(filepath.Join(src, "Contents", "MacOS"), 0o755)
	os.WriteFile(filepath.Join(src, "Contents", "MacOS", "fleet"), []byte("new"), 0o755)
	os.WriteFile(filepath.Join(src, "Contents", "MacOS", "fleet-native-capture"), []byte("helper"), 0o755)
	zip := filepath.Join(t.TempDir(), "Fleet-arm64.app.zip")
	if out, err := exec.Command("/usr/bin/ditto", "-c", "-k", "--keepParent", src, zip).CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, out)
	}
	body, _ := os.ReadFile(zip)
	release(t, "fleet-v0.5.0-model.6", map[string][]byte{"Fleet-arm64.app.zip": body}, "")
	exe := filepath.Join(bundle, "Contents", "MacOS", "fleet")
	u := &Updater{Current: "0.5.0-model.5", Locate: func() (Target, error) {
		return Target{Executable: exe, Bundle: bundle, Asset: "Fleet-arm64.app.zip"}, nil
	}}
	res, path, err := u.Apply(context.Background(), "fleet-v0.5.0-model.6")
	if err != nil || res.Status != "updated" || path != exe {
		t.Fatalf("%+v %q %v", res, path, err)
	}
	if b, _ := os.ReadFile(exe); string(b) != "new" {
		t.Fatalf("bundle binary not replaced: %q", b)
	}
	if _, err := os.Stat(filepath.Join(bundle, "Contents", "MacOS", "fleet-native-capture")); err != nil {
		t.Fatal("helper missing from the new bundle")
	}
	if b, _ := os.ReadFile(filepath.Join(bundle+".previous", "Contents", "MacOS", "fleet")); string(b) != "old" {
		t.Fatal("previous bundle not kept")
	}
}
