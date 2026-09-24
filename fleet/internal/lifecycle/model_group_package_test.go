package lifecycle

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

type rejectGroupPackageEffects struct {
	fakeDriver
	preparations int
}

func (d *rejectGroupPackageEffects) PrepareDependencies(context.Context, Dependencies) (Receipt, error) {
	d.preparations++
	return Receipt{}, fmt.Errorf("dependency preparation crossed network admission")
}

// Consumes the production Python compiler's exact tar. It does not grant the
// future private-network capability or pretend a fake driver proves GPU support.
func TestCompiledGroupPackageRequiresPrivateNetworkAdmission(t *testing.T) {
	path := os.Getenv("PANTHEON_GROUP_PACKAGE_OUTPUT")
	if path == "" {
		t.Skip("opt-in production Python package boundary")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	archive := tar.NewReader(bytes.NewReader(data))
	var definition Definition
	found := false
	for {
		entry, err := archive.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if entry.Name == "fleet.json" {
			body, err := io.ReadAll(io.LimitReader(archive, 65537))
			if err != nil || len(body) > 65536 {
				t.Fatal("invalid generated manifest", err)
			}
			if err = StrictDecode(body, &definition); err != nil {
				t.Fatal(err)
			}
			if err = definition.Validate(); err != nil {
				t.Fatal(err)
			}
			found = true
		}
	}
	if !found || !definition.Components[0].GroupPeer || !definition.Components[0].GroupNetwork || !definition.Components[0].RunAsOwner {
		t.Fatal("missing protected group component")
	}
	driver := &rejectGroupPackageEffects{fakeDriver: fakeDriver{alive: map[string]bool{}}}
	m, err := Open(t.TempDir(), "f_aaaaaaaaaaaaaaaa", "n_0",
		proto.Capability{OS: "linux", Arch: "amd64", Caps: []string{"proc"}}, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	sha := sha256.Sum256(data)
	digest := hex.EncodeToString(sha[:])
	if _, err = m.Stage(digest, 0, data); err != nil {
		t.Fatal(err)
	}
	op := submit(t, m, digest, "group-install", "install", "group", 0)
	if op.State != "failed" || !strings.Contains(op.Error, "model-group-private-network") {
		t.Fatalf("expected explicit network admission failure, got %s: %s", op.State, op.Error)
	}
	if driver.preparations != 0 || driver.starts != 0 {
		t.Fatal("engine dependency/start effects happened before network admission")
	}
}
