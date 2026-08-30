package node

import (
	"runtime"
	"testing"

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
