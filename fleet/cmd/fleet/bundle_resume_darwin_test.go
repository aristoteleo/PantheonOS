package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestBundleResumeKeepsNodeOptionsWithoutJoinCredentials(t *testing.T) {
	args := []string{"--state-dir", "/tmp/node with spaces", "--key", "secret", "--controller=https://fleet.example", "-join-token=one-use", "--name", "My Mac", "--no-files"}
	want := []string{"up", "--state-dir", "/tmp/node with spaces", "--controller=https://fleet.example", "--name", "My Mac", "--no-files"}
	if got := resumableArgs(args); !reflect.DeepEqual(got, want) {
		t.Fatalf("resume arguments: %v", got)
	}
	if got := resumableArgs([]string{"--join-token", "secret", "-key=secret"}); !reflect.DeepEqual(got, []string{"up"}) {
		t.Fatalf("credentials retained: %v", got)
	}
}

func TestBundleResumeRestoresWorkingDirectoryAndSavedStateSelection(t *testing.T) {
	home := t.TempDir()
	path := filepath.Join(home, "macos-launch.json")
	resume, err := readBundleResume(path, home)
	if err != nil || resume.Dir != home || !reflect.DeepEqual(resume.Args, []string{"up"}) {
		t.Fatalf("first reopen must use saved default node from home: %+v %v", resume, err)
	}
	want := bundleResume{Args: []string{"up", "--state-dir", "/custom state", "--workdir", "tasks"}, Dir: home}
	data, _ := json.Marshal(want)
	if err := writePrivateFile(path, data); err != nil {
		t.Fatal(err)
	}
	got, err := readBundleResume(path, "/")
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("saved invocation lost: %+v %v", got, err)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatal("reopen settings must be private")
	}
	for _, invalid := range []string{`{`, `{"Args":["capture","permissions"],"Dir":"/"}`, `{"Args":["up"],"Dir":"relative"}`} {
		if err := os.WriteFile(path, []byte(invalid), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := readBundleResume(path, home); err == nil {
			t.Fatalf("invalid settings accepted: %s", invalid)
		}
	}
}
