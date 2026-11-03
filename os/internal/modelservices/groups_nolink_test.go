package modelservices

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

// Modal Volumes v2 refuse hard links with EPERM: packages still publish
// under their digest, a second put of the same bytes is idempotent, and no
// temporary file is left behind.
func TestPackageStorePublishesWhereHardLinksAreRefused(t *testing.T) {
	saved := linkPackage
	linkPackage = func(string, string) error { return &os.LinkError{Op: "link", Err: syscall.EPERM} }
	defer func() { linkPackage = saved }()
	s, err := newPackageStore(filepath.Join(t.TempDir(), "packages"))
	if err != nil {
		t.Fatal(err)
	}
	sha, err := s.put("source", []byte("group source"))
	if err != nil {
		t.Fatal(err)
	}
	if again, err := s.put("source", []byte("group source")); err != nil || again != sha {
		t.Fatalf("idempotent put: %q %v", again, err)
	}
	if data, err := s.read("source", sha); err != nil || string(data) != "group source" {
		t.Fatalf("read back: %q %v", data, err)
	}
	if left, _ := filepath.Glob(filepath.Join(s.root, ".writing-*")); len(left) != 0 {
		t.Fatalf("temporary files left: %v", left)
	}
}
