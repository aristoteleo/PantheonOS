package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestPrivateJoinKey(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "key")
	for _, value := range []string{"", "one\ntwo", strings.Repeat("x", 8195)} {
		if err := os.WriteFile(path, []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := readJoinKey(path); err == nil {
			t.Fatal("accepted invalid key")
		}
	}
	if err := os.WriteFile(path, []byte("local-test-key\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if value, err := readJoinKey(path); err != nil || value != "local-test-key" {
		t.Fatal(value, err)
	}
	if _, err := readJoinKey(root); err == nil {
		t.Fatal("accepted directory")
	}
	if runtime.GOOS != "windows" {
		link := filepath.Join(root, "link")
		if err := os.Symlink(path, link); err != nil {
			t.Fatal(err)
		}
		if _, err := readJoinKey(link); err == nil {
			t.Fatal("accepted symlink")
		}
		if err := os.Chmod(path, 0644); err != nil {
			t.Fatal(err)
		}
		if _, err := readJoinKey(path); err == nil {
			t.Fatal("accepted public key file")
		}
	}
}
