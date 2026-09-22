package lifecycle

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadOnlyAppMountsStayWithinOwnedFiles(t *testing.T) {
	root := t.TempDir()
	cache := filepath.Join(root, "cache")
	model := filepath.Join(cache, "snapshots", "digest")
	if err := os.MkdirAll(model, 0700); err != nil {
		t.Fatal(err)
	}
	c := Component{Env: map[string]string{"PANTHEON_APP_CACHE": cache}, ReadOnlyMounts: map[string]string{"package": "/package", "cache/snapshots/digest": "/model"}}
	args, err := readOnlyAppMounts(c, Paths{Package: root})
	if err != nil || len(args) != 4 {
		t.Fatal(args, err)
	}
	for i := 1; i < len(args); i += 2 {
		if !strings.HasSuffix(args[i], ",readonly") {
			t.Fatal(args)
		}
	}
	for _, source := range []string{"cache/../escape", "cache/not-prepared", "/etc/passwd", "cache"} {
		c.ReadOnlyMounts = map[string]string{source: "/model"}
		if _, err := readOnlyAppMounts(c, Paths{Package: root}); err == nil {
			t.Fatal("invalid mount accepted", source)
		}
	}
	if err := os.Symlink(root, filepath.Join(cache, "escape")); err == nil {
		c.ReadOnlyMounts = map[string]string{"cache/escape": "/model"}
		if _, err := readOnlyAppMounts(c, Paths{Package: root}); err == nil {
			t.Fatal("symlink escape accepted")
		}
	}
}
