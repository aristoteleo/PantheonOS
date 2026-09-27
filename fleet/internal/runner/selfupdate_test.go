package runner

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/selfupdate"
)

func TestSelfUpdateWaitsForWorkThenRestartsIntoTheNewBinary(t *testing.T) {
	const tag = "fleet-v0.5.0-model.6"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/" + tag + "/SHA256SUMS":
			fmt.Fprintf(w, "%x  fleet-linux-amd64\n", sha256.Sum256([]byte("new")))
		case "/" + tag + "/fleet-linux-amd64":
			w.Write([]byte("new"))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	old := selfupdate.ReleaseBase
	selfupdate.ReleaseBase = server.URL + "/"
	defer func() { selfupdate.ReleaseBase = old }()

	exe := filepath.Join(t.TempDir(), "fleet")
	os.WriteFile(exe, []byte("old"), 0o755)
	r := &Runner{}
	var restarted []string
	r.EnableSelfUpdate(&selfupdate.Updater{
		Current: "0.5.0-model.5",
		Locate: func() (selfupdate.Target, error) {
			return selfupdate.Target{Executable: exe, Asset: "fleet-linux-amd64"}, nil
		},
	}, func(path string) error { restarted = append(restarted, path); return nil })

	apply := func() selfupdate.Result {
		var got selfupdate.Result
		r.ApplyUpdate(context.Background(), tag, func(res selfupdate.Result, err error) {
			if err != nil {
				t.Fatal(err)
			}
			got = res
		})
		return got
	}
	r.active.Add(1) // a task is running
	if res := apply(); res.Status != "deferred" || len(restarted) != 0 {
		t.Fatalf("busy node updated: %+v %v", res, restarted)
	}
	if b, _ := os.ReadFile(exe); string(b) != "old" {
		t.Fatal("binary replaced while busy")
	}
	r.active.Add(-1)
	if res := apply(); res.Status != "updated" || len(restarted) != 1 || restarted[0] != exe {
		t.Fatalf("idle node: %+v %v", res, restarted)
	}
}

func TestSelfUpdateIsRefusedWhenTurnedOff(t *testing.T) {
	var err error
	(&Runner{}).ApplyUpdate(context.Background(), "fleet-v0.5.0-model.6", func(_ selfupdate.Result, e error) { err = e })
	if err == nil {
		t.Fatal("a node without self-update accepted one")
	}
}
