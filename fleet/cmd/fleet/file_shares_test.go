package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestShareConfiguration(t *testing.T) {
	state := t.TempDir()
	folder := t.TempDir()
	roots, err := configureShares(state, nil, false)
	if err != nil || len(roots) != 0 {
		t.Fatal(roots, err)
	}
	roots, err = configureShares(state, []string{folder, folder}, false)
	if err != nil || len(roots) != 1 {
		t.Fatal(roots, err)
	}
	saved, err := configureShares(state, nil, false)
	if err != nil || len(saved) != 1 || saved[0] != roots[0] {
		t.Fatal(saved, err)
	}
	if _, err = configureShares(state, []string{folder}, true); err == nil {
		t.Fatal("conflicting options")
	}
	if _, err = configureShares(state, []string{filepath.Join(state, "absent")}, false); err == nil {
		t.Fatal("missing folder")
	}
	roots, err = configureShares(state, nil, true)
	if err != nil || len(roots) != 0 {
		t.Fatal(roots, err)
	}
	data, _ := os.ReadFile(filepath.Join(state, "file-shares.json"))
	if string(data) != "[]" {
		t.Fatal(string(data))
	}
}
