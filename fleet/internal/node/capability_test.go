package node

import (
	"errors"
	"runtime"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/nativecapture"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func has(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

func TestCaptureRuntimeGrantsAndRevocations(t *testing.T) {
	original := map[string]string{"fleet": "test", "python": "3.12"}
	status := nativecapture.Status{Available: true, Setup: true, ScreenRecording: true}
	missing := captureRuntimes(original, status, nil)
	if missing["native-capture-ready"] != "" || missing["native-capture-input"] != "0" || missing["native-capture-setup"] != "1" {
		t.Fatalf("missing permission incorrectly advertised: %v", missing)
	}
	status.Input = true
	ready := captureRuntimes(missing, status, nil)
	if ready["native-capture-ready"] != "1" || ready["native-capture-input"] != "1" {
		t.Fatalf("new grant not advertised: %v", ready)
	}
	status.ScreenRecording = false
	revoked := captureRuntimes(ready, status, nil)
	if revoked["native-capture-ready"] != "" || revoked["native-capture-screen-recording"] != "0" {
		t.Fatalf("revoked grant still advertised: %v", revoked)
	}
	if len(original) != 2 || missing["native-capture-input"] != "0" || ready["native-capture-ready"] != "1" {
		t.Fatal("refresh mutated an existing capability snapshot")
	}
	for _, tc := range []struct {
		name   string
		status nativecapture.Status
		err    error
	}{
		{"probe failed", status, errors.New("helper unavailable")},
		{"capture unavailable", nativecapture.Status{}, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := captureRuntimes(ready, tc.status, tc.err)
			if len(got) != 2 || got["fleet"] != "test" || got["python"] != "3.12" {
				t.Fatalf("stale capture capability retained or other runtimes lost: %v", got)
			}
		})
	}
}

func TestDefaultCapsByKind(t *testing.T) {
	cases := []struct {
		kind string
		must []string
		not  []string
	}{
		{proto.KindSandbox, []string{"proc", "fs:workspace", "display", "net"}, []string{"dom"}},
		{proto.KindPod, []string{"net"}, []string{"proc", "fs:workspace", "dom"}},
		{proto.KindFrontend, []string{"dom"}, []string{"proc", "net"}},
		{proto.KindMachine, []string{"proc", "net"}, []string{"fs:workspace", "dom"}},
	}
	for _, tc := range cases {
		caps := DefaultCaps(tc.kind, proto.Capability{})
		for _, want := range tc.must {
			if !has(caps, want) {
				t.Errorf("%s: missing %q in %v", tc.kind, want, caps)
			}
		}
		for _, bad := range tc.not {
			if has(caps, bad) {
				t.Errorf("%s: unexpected %q in %v", tc.kind, bad, caps)
			}
		}
	}
}

func TestMachineGainsGPUCap(t *testing.T) {
	caps := DefaultCaps(proto.KindMachine, proto.Capability{GPU: "2x H100"})
	if !has(caps, "gpu") {
		t.Errorf("machine with GPU should offer gpu cap, got %v", caps)
	}
}

func TestDetectCapabilityCarriesSystemInfo(t *testing.T) {
	c := DetectCapability(".")
	if c.OS != runtime.GOOS || c.Arch != runtime.GOARCH {
		t.Fatalf("os/arch: %s/%s", c.OS, c.Arch)
	}
	if c.Kernel == "" {
		t.Error("kernel should be detected on darwin/linux")
	}
	if _, ok := c.Runtimes["git"]; !ok {
		t.Errorf("git runtime expected on dev machine, got %v", c.Runtimes)
	}
}
