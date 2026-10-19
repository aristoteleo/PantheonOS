package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
)

func TestBundleLaunchCommands(t *testing.T) {
	for _, args := range [][]string{{"up"}, {"up", "--no-files"}, {"capture", "doctor"}, {"capture", "permissions"}} {
		if !needsBundleLaunch(args) {
			t.Fatalf("must launch in bundle: %v", args)
		}
	}
	for _, args := range [][]string{nil, {"version"}, {"prime"}, {"capture", "peer"}, {"__app_launch", "/tmp/test"}} {
		if needsBundleLaunch(args) {
			t.Fatalf("must not wrap or recurse: %v", args)
		}
	}
}

func TestFleetBundleResolvesInstalledSymlinkOnly(t *testing.T) {
	root := t.TempDir()
	bundle := filepath.Join(root, "Fleet.app")
	executable := filepath.Join(bundle, "Contents", "MacOS", "fleet")
	if err := os.MkdirAll(filepath.Dir(executable), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(executable, nil, 0700); err != nil {
		t.Fatal(err)
	}
	if got := fleetBundle(executable); got != "" {
		t.Fatalf("unbundled executable accepted: %s", got)
	}
	if err := os.WriteFile(filepath.Join(bundle, "Contents", "Info.plist"), nil, 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "fleet")
	if err := os.Symlink(executable, link); err != nil {
		t.Fatal(err)
	}
	want, _ := filepath.EvalSymlinks(bundle)
	if got := fleetBundle(link); got != want {
		t.Fatalf("bundle = %q, want %q", got, want)
	}
	if got := fleetBundle(os.Args[0]); got != "" {
		t.Fatalf("test executable would relaunch: %s", got)
	}
}

func TestBundleLaunchRestoresInvocationWithoutLauncherIdentity(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	work := t.TempDir()
	args := []string{"up", "--name", "Node with spaces $(literal)"}
	data, _ := json.Marshal(bundleLaunch{Args: args, Dir: work, Env: []string{
		"FLEET_LAUNCH_TEST_VALUE=preserved", "__CFBundleIdentifier=com.old.launcher", "XPC_SERVICE_NAME=old-launcher",
	}})
	payload := filepath.Join(dir, "launch.json")
	if err := os.WriteFile(payload, data, 0600); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestBundleLaunchChild$")
	cmd.Env = append(os.Environ(), "FLEET_TEST_LAUNCH_DIR="+dir, "__CFBundleIdentifier=org.pantheonos.fleet", "XPC_SERVICE_NAME=fleet-launch-services")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("child: %v: %s", err, out)
	}
	var got struct {
		Args                      []string
		Dir, Value, Identity, XPC string
	}
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	resolvedWork, _ := filepath.EvalSymlinks(work)
	resolvedGot, _ := filepath.EvalSymlinks(got.Dir)
	if !reflect.DeepEqual(got.Args, args) || resolvedGot != resolvedWork || got.Value != "preserved" || got.Identity != "org.pantheonos.fleet" || got.XPC != "fleet-launch-services" {
		t.Fatalf("invocation was changed: %+v", got)
	}
	if _, err := os.Stat(payload); !os.IsNotExist(err) {
		t.Fatal("launch arguments/environment were not removed")
	}
	if code, _ := os.ReadFile(filepath.Join(dir, "exit")); string(code) != "7" {
		t.Fatalf("exit code: %s", code)
	}
	if pid, _ := os.ReadFile(filepath.Join(dir, "pid")); len(pid) == 0 {
		t.Fatal("missing child PID for signal forwarding")
	}
}

func TestBundleLaunchChild(t *testing.T) {
	dir := os.Getenv("FLEET_TEST_LAUNCH_DIR")
	if dir == "" {
		return
	}
	if err := acceptBundleLaunch(dir); err != nil {
		t.Fatal(err)
	}
	cwd, _ := os.Getwd()
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"Args": os.Args[1:], "Dir": cwd,
		"Value": os.Getenv("FLEET_LAUNCH_TEST_VALUE"), "Identity": os.Getenv("__CFBundleIdentifier"), "XPC": os.Getenv("XPC_SERVICE_NAME")})
	finishAppLaunch(7)
	os.Exit(0)
}

func TestBundleLaunchRejectsSharedDirectory(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := acceptBundleLaunch(dir); err == nil {
		t.Fatal("public launch directory accepted")
	}
}
