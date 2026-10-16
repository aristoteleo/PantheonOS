package localmode

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

// bundle writes a local product bundle whose owner's pantheon package records
// how it was run, and exits with the code its environment asks for.
func bundle(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	platform := runtime.GOOS + "-" + runtime.GOARCH
	write := func(rel, body string) {
		path := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	index, _ := json.Marshal(map[string]any{"protocol": 1, "platform": platform, "release": "apps",
		"binaries": map[string]any{}, "owner": "owner"})
	write("local-bundle.json", string(index))
	write("owner/requirements.txt", "")
	write("owner/pantheon/__init__.py", "")
	write("owner/pantheon/__main__.py", `import json, os, sys
stdin = sys.stdin.read() if sys.argv[1] == 'local-setup' else ''
with open(os.environ['RECORD'], 'w') as f:
    json.dump({'argv': sys.argv, 'fleet': os.environ.get('PANTHEON_FLEET_BINARY'),
               'key': os.environ.get('PANTHEON_API_KEY'), 'path': sys.path[0], 'stdin': stdin}, f)
if os.environ.get('CALLS'):
    with open(os.environ['CALLS'], 'a') as f:
        f.write(json.dumps({'argv': sys.argv, 'stdin': stdin}) + '\n')
if os.environ.get('CALLS') and sys.argv[1] == 'local-setup':
    out = sys.argv[sys.argv.index('--output') + 1]
    os.mkdir(out)
    bundle = sys.argv[sys.argv.index('--bundle') + 1]
    with open(os.path.join(out, 'launch.json'), 'w') as f:
        json.dump({'protocol': 1, 'launcher': ['/x'], 'bundle': bundle, 'setup': out + '/setup.json',
                   'profile': out + '/profile', 'workspace': '/w'}, f)
sys.exit(int(os.environ.get('EXIT', '0')))
`)
	return root
}

func fakeRuntime(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is not installed")
	}
	saved, savedExe := pythonFor, executable
	pythonFor = func(context.Context, owner, func(string)) (string, error) { return python, nil }
	executable = func() (string, error) { return "/opt/pantheon/bin/pantheon", nil }
	t.Cleanup(func() { pythonFor, executable = saved, savedExe })
}

type record struct {
	Argv  []string `json:"argv"`
	Fleet string   `json:"fleet"`
	Key   *string  `json:"key"`
	Path  string   `json:"path"`
	Stdin string   `json:"stdin"`
}

func run(t *testing.T, args ...string) (record, int, string) {
	t.Helper()
	out := filepath.Join(t.TempDir(), "record.json")
	t.Setenv("RECORD", out)
	var stderr bytes.Buffer
	code := Main(context.Background(), args, IO{Stdin: strings.NewReader(""), Stdout: &bytes.Buffer{}, Stderr: &stderr})
	var r record
	if data, err := os.ReadFile(out); err == nil {
		json.Unmarshal(data, &r)
	}
	return r, code, stderr.String()
}

// The bundle's own Python runs its commands, told that this binary runs the
// Fleet, without the owner's keys; its exit status is the command's.
func TestTheBundlesOwnPythonRunsItsCommands(t *testing.T) {
	fakeRuntime(t)
	b := bundle(t)
	t.Setenv("PANTHEON_API_KEY", "pbk_secret")
	r, code, stderr := run(t, "local-setup", "--bundle", b, "--workspace", "/w", "--output", "/o")
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr)
	}
	want := []string{"pantheon", "local-setup", "--bundle", b, "--workspace", "/w", "--output", "/o",
		"--pantheon", "/opt/pantheon/bin/pantheon"}
	if !slices.Equal(r.Argv, want) || r.Fleet != "/opt/pantheon/bin/pantheon" || r.Key != nil {
		t.Fatalf("%+v", r)
	}
	if r.Path != filepath.Join(b, "owner") {
		t.Fatalf("ran from %s", r.Path)
	}
	// An explicit interpreter is kept; the exit status passes through.
	t.Setenv("EXIT", "3")
	r, code, _ = run(t, "local-setup", "--bundle", b, "--python", "/usr/bin/python3")
	if code != 3 || slices.Contains(r.Argv, "--pantheon") {
		t.Fatalf("exit %d, %v", code, r.Argv)
	}
}

// `pantheon up` runs the saved launch; given a bundle, it runs that.
func TestUpRunsTheSavedLaunch(t *testing.T) {
	fakeRuntime(t)
	b := bundle(t)
	home := t.TempDir()
	t.Setenv("PANTHEON_LOCAL_HOME", home)
	if _, code, stderr := run(t, "up"); code == 0 || !strings.Contains(stderr, "local-setup") {
		t.Fatalf("no setup: exit %d %s", code, stderr)
	}
	launch, _ := json.Marshal(Launch{Protocol: 1, Launcher: []string{"/opt/pantheon/bin/pantheon"}, Bundle: b,
		Setup: "/s/setup.json", Profile: "/s/profile", Workspace: "/w"})
	os.WriteFile(filepath.Join(home, "launch.json"), launch, 0o600)
	r, code, stderr := run(t, "up", "--agent", "agent")
	if code != 0 || !slices.Equal(r.Argv, []string{"pantheon", "local", "--launch", filepath.Join(home, "launch.json"), "--agent", "agent"}) {
		t.Fatalf("exit %d %s %v", code, stderr, r.Argv)
	}
	r, _, _ = run(t, "up", "--bundle", b, "--setup", "/s", "--profile", "/p", "--workspace", "/w")
	if !slices.Equal(r.Argv[:3], []string{"pantheon", "local", "--bundle"}) {
		t.Fatal(r.Argv)
	}
}

func TestOnlyCommandsThatNameABundleRunFromIt(t *testing.T) {
	for args, want := range map[string]bool{
		"up": true, "down": true, "status --json": true, "local --bundle /b": true, "local-release --launch=/l": true,
		"local": false, "local-setup --python /p": false, "cli --bundle /b": false, "apps list": false,
	} {
		if got := Handles(strings.Fields(args)); got != want {
			t.Errorf("%q: %v", args, got)
		}
	}
}

func TestABundleCannotPointOutsideItself(t *testing.T) {
	b := bundle(t)
	index, _ := json.Marshal(map[string]any{"protocol": 1, "platform": runtime.GOOS + "-" + runtime.GOARCH,
		"release": "apps", "binaries": map[string]any{}, "owner": "../../elsewhere"})
	os.WriteFile(filepath.Join(b, "local-bundle.json"), index, 0o644)
	if _, err := ownerOf(b); err == nil || !strings.Contains(err.Error(), "outside") {
		t.Fatal(err)
	}
}

func TestStatusAndDownWithoutARunningOwner(t *testing.T) {
	home := t.TempDir()
	t.Setenv("PANTHEON_LOCAL_HOME", home)
	profile := filepath.Join(home, "profile")
	os.MkdirAll(profile, 0o700)
	launch, _ := json.Marshal(Launch{Protocol: 1, Launcher: []string{"/x"}, Bundle: "/b", Setup: "/s",
		Profile: profile, Workspace: "/w"})
	os.WriteFile(filepath.Join(home, "launch.json"), launch, 0o600)
	// A receipt naming a process that is not its owner (this test's own pid,
	// with another start time) is not signalled.
	receipt, _ := json.Marshal(map[string]any{"phase": "ready", "owner": map[string]any{"pid": os.Getpid(), "created": 1.0}})
	os.WriteFile(filepath.Join(profile, "processes.json"), receipt, 0o600)
	var out bytes.Buffer
	if code := Main(context.Background(), []string{"down"}, IO{Stdout: &out, Stderr: &out}); code != 0 || !strings.Contains(out.String(), "not running") {
		t.Fatalf("%d %s", code, out.String())
	}
	out.Reset()
	if code := Main(context.Background(), []string{"status", "--json"}, IO{Stdout: &out, Stderr: &out}); code != 0 {
		t.Fatalf("%d %s", code, out.String())
	}
	var s Summary
	if json.Unmarshal(out.Bytes(), &s); s.State != "stopped" || s.Profile != profile {
		t.Fatalf("%s", out.String())
	}
}

// The summary is the one `pantheon local` reports.
func TestSummaryMatchesTheOwnersReport(t *testing.T) {
	parse := func(raw string) deployment {
		var d deployment
		if err := json.Unmarshal([]byte(raw), &d); err != nil {
			t.Fatal(err)
		}
		return d
	}
	s := summarize(parse(`{"spec":{"apps":{"agent":{"intent":"running"},"shell":{"intent":"running"}}},
		"status":{"apps":{"agent":{"state":"ready"},"shell":{"state":"backoff","reason":"exited"}}}}`), "/p")
	if s.State != "starting" || !slices.Equal(s.Attention, []string{"shell"}) || s.Apps["shell"].Reason != "exited" {
		t.Fatalf("%+v", s)
	}
	if s := summarize(parse(`{"spec":{"apps":{"agent":{"intent":"running"}}},"status":{"apps":{"agent":{"state":"ready"}}}}`), "/p"); s.State != "ready" {
		t.Fatalf("%+v", s)
	}
	if s := summarize(parse(`{"spec":{"apps":{"agent":{"intent":"stopped"}}},"status":{"apps":{"agent":{"state":"stopped"}}}}`), "/p"); s.State != "stopped" {
		t.Fatalf("%+v", s)
	}
	if s := summarize(parse(`{"spec":{"apps":{"agent":{"intent":"stopped"}}}}`), "/p"); s.State != "stopping" || s.Apps["agent"].State != "waiting" {
		t.Fatalf("%+v", s)
	}
}
