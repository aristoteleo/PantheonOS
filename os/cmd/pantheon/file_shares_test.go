package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func testHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	if runtime.GOOS == "windows" {
		t.Setenv("USERPROFILE", home)
	} else {
		t.Setenv("HOME", home)
	}
	resolved, err := filepath.EvalSymlinks(home)
	if err != nil {
		t.Fatal(err)
	}
	return resolved
}

func TestShareConfiguration(t *testing.T) {
	home := testHome(t)
	state := t.TempDir()
	folder := t.TempDir()
	roots, err := configureShares(state, nil, false)
	if err != nil || len(roots) != 1 || roots[0] != home {
		t.Fatal(roots, err)
	}
	saved, err := configureShares(state, nil, false)
	if err != nil || len(saved) != 1 || saved[0] != home {
		t.Fatal(saved, err)
	}
	roots, err = configureShares(state, []string{folder, folder}, false)
	if err != nil || len(roots) != 1 {
		t.Fatal(roots, err)
	}
	saved, err = configureShares(state, nil, false)
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
	roots, err = configureShares(state, nil, false)
	if err != nil || len(roots) != 0 {
		t.Fatal("saved opt-out was not respected", roots, err)
	}
	roots, err = configureShares(state, []string{"~"}, false)
	if err != nil || len(roots) != 1 || roots[0] != home {
		t.Fatal("could not re-enable home sharing", roots, err)
	}
}

func TestFirstStartCanOptOut(t *testing.T) {
	testHome(t)
	state := t.TempDir()
	for _, disabled := range []bool{true, false} {
		roots, err := configureShares(state, nil, disabled)
		if err != nil || len(roots) != 0 {
			t.Fatal(roots, err)
		}
	}
}

func TestPrimerRespectsUpFlagSyntax(t *testing.T) {
	testHome(t)
	for _, flag := range []string{"--no-files", "--no-files=true", "-no-files=1"} {
		state := t.TempDir()
		dir, paths, disabled, err := primeShareOptions([]string{"--controller", "https://fleet.example", "--state-dir=" + state, flag})
		if err != nil || dir != state || !disabled || len(paths) != 0 {
			t.Fatal(dir, paths, disabled, err)
		}
		roots, err := configureShares(dir, paths, disabled)
		if err != nil || len(roots) != 0 {
			t.Fatal(roots, err)
		}
	}
	_, paths, disabled, err := primeShareOptions([]string{"--no-files=false", "--share-dir=~"})
	if err != nil || disabled || len(paths) != 1 || paths[0] != "~" {
		t.Fatal(paths, disabled, err)
	}
	for _, args := range [][]string{{"--share-dir"}, {"--state-dir"}, {"--no-files=wrong"}} {
		if _, _, _, err := primeShareOptions(args); err == nil {
			t.Fatal("invalid primer flags accepted", args)
		}
	}
}

func TestExistingShareConfigurationIsNeverWidened(t *testing.T) {
	testHome(t)
	for _, data := range []string{"[]", "null", "invalid json"} {
		t.Run(data, func(t *testing.T) {
			state := t.TempDir()
			config := filepath.Join(state, "file-shares.json")
			if err := os.WriteFile(config, []byte(data), 0600); err != nil {
				t.Fatal(err)
			}
			roots, err := configureShares(state, nil, false)
			if len(roots) != 0 || (err != nil) != (data == "invalid json") {
				t.Fatal(roots, err)
			}
			after, _ := os.ReadFile(config)
			if string(after) != data {
				t.Fatal("existing configuration was overwritten")
			}
		})
	}
}

func TestUnavailableHomeDoesNotCreateShares(t *testing.T) {
	home := testHome(t)
	if err := os.Remove(home); err != nil {
		t.Fatal(err)
	}
	state := t.TempDir()
	if roots, err := configureShares(state, nil, false); err == nil || len(roots) != 0 {
		t.Fatal(roots, err)
	}
	if _, err := os.Stat(filepath.Join(state, "file-shares.json")); !os.IsNotExist(err) {
		t.Fatal("invalid defaults must not be persisted", err)
	}
}
