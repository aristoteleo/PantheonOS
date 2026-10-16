package localmode

import (
	"archive/tar"
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// archive packs a bundle directory as the release does (tar.gz).
func archive(t *testing.T, dir string, extra ...*tar.Header) []byte {
	t.Helper()
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		rel, _ := filepath.Rel(dir, path)
		if rel == "." {
			return nil
		}
		h, _ := tar.FileInfoHeader(info, "")
		h.Name = filepath.ToSlash(rel)
		tw.WriteHeader(h)
		if info.Mode().IsRegular() {
			data, _ := os.ReadFile(path)
			tw.Write(data)
		}
		return nil
	})
	for _, h := range extra {
		tw.WriteHeader(h)
	}
	tw.Close()
	gz.Close()
	return buf.Bytes()
}

// publish serves a manifest and a bundle archive as the GitHub releases do.
func publish(t *testing.T, data []byte, sum string) {
	t.Helper()
	platform := runtime.GOOS + "-" + runtime.GOARCH
	if sum == "" {
		s := sha256.Sum256(data)
		sum = hex.EncodeToString(s[:])
	}
	manifest, _ := json.Marshal(Manifest{Protocol: 1, Version: "0.9.75", Tag: "pantheon-local-v0.9.75",
		Store: "https://store.example.test", Bundles: map[string]ManifestBundle{
			platform: {Asset: "pantheon-local-" + platform + ".tar.gz", SHA256: sum, Bytes: int64(len(data))}}})
	mux := http.NewServeMux()
	mux.HandleFunc("/"+manifestAsset, func(w http.ResponseWriter, r *http.Request) { w.Write(manifest) })
	mux.HandleFunc("/pantheon-local-v0.9.75/", func(w http.ResponseWriter, r *http.Request) { w.Write(data) })
	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)
	saved := releaseBase
	releaseBase = server.URL + "/"
	t.Cleanup(func() { releaseBase = saved })
}

func TestTheBundleIsDownloadedOnceAndChecked(t *testing.T) {
	src := bundle(t)
	os.WriteFile(filepath.Join(src, "owner", "tool"), []byte("#!/bin/sh\n"), 0o755)
	data := archive(t, src)
	t.Setenv("PANTHEON_LOCAL_HOME", filepath.Join(t.TempDir(), "local"))
	publish(t, data, "")
	dir, m, err := downloadBundle(context.Background(), func(string) {})
	if err != nil || m.Store != "https://store.example.test" {
		t.Fatal(err, m)
	}
	if info, err := os.Stat(filepath.Join(dir, "owner", "tool")); err != nil || info.Mode().Perm()&0o100 == 0 {
		t.Fatal("the executable bit was lost", err)
	}
	if _, err := ownerOf(dir); err != nil {
		t.Fatal(err)
	}
	// Kept per version: not fetched again.
	releaseBase = "http://127.0.0.1:1/"
	if _, _, err := downloadBundle(context.Background(), func(string) {}); err == nil {
		t.Fatal("the manifest is read each start")
	}
}

func TestADamagedOrHostileBundleIsRefused(t *testing.T) {
	src := bundle(t)
	t.Setenv("PANTHEON_LOCAL_HOME", filepath.Join(t.TempDir(), "local"))
	publish(t, archive(t, src), strings.Repeat("0", 64))
	if _, _, err := downloadBundle(context.Background(), func(string) {}); err == nil || !strings.Contains(err.Error(), "checksum") {
		t.Fatal(err)
	}
	for _, h := range []*tar.Header{
		{Name: "../escape", Typeflag: tar.TypeReg, Mode: 0o644},
		{Name: "owner/link", Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd"},
	} {
		if err := extract(bytes.NewReader(archive(t, src, h)), t.TempDir()); err == nil {
			t.Fatalf("%s was extracted", h.Name)
		}
	}
}

// The first start asks where to work and which model to use, saves the setup
// with the product's own local-setup, then starts it.
func TestTheFirstStartSetsUpAndStarts(t *testing.T) {
	fakeRuntime(t)
	src := bundle(t)
	publish(t, archive(t, src), "")
	home := filepath.Join(t.TempDir(), "local")
	t.Setenv("PANTHEON_LOCAL_HOME", home)
	calls := filepath.Join(t.TempDir(), "calls")
	t.Setenv("CALLS", calls)
	t.Setenv("RECORD", filepath.Join(t.TempDir(), "record.json"))
	workspace := t.TempDir()
	savedInteractive, savedEngines := interactive, localEngines
	interactive = func(io.Reader) bool { return true }
	localEngines = func(context.Context) []engine {
		return []engine{{"ollama", "Ollama on this machine", "http://127.0.0.1:11434", []string{"gemma4:latest", "qwen3:8b"}}}
	}
	t.Cleanup(func() { interactive, localEngines = savedInteractive, savedEngines })

	var stderr bytes.Buffer
	answers := strings.NewReader(workspace + "\n1\n2\n")
	code := Main(context.Background(), []string{"up"}, IO{Stdin: answers, Stdout: &bytes.Buffer{}, Stderr: &stderr})
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}
	f, _ := os.Open(calls)
	defer f.Close()
	var runs []record
	for s := bufio.NewScanner(f); s.Scan(); {
		var r record
		json.Unmarshal(s.Bytes(), &r)
		runs = append(runs, r)
	}
	if len(runs) != 2 || runs[0].Argv[1] != "local-setup" || runs[1].Argv[1] != "local" {
		t.Fatalf("%+v", runs)
	}
	var choices map[string]any
	json.Unmarshal([]byte(runs[0].Stdin), &choices)
	tiers, _ := choices["tiers"].(map[string]any)
	if choices["engine"] != "ollama" || choices["endpoint"] != "http://127.0.0.1:11434" || tiers["normal"] != "qwen3:8b" ||
		choices["store_origin"] != "https://store.example.test" || choices["project_name"] != filepath.Base(workspace) {
		t.Fatalf("%v", choices)
	}
	if want := []string{"pantheon", "local", "--launch", filepath.Join(home, "launch.json")}; strings.Join(runs[1].Argv, " ") != strings.Join(want, " ") {
		t.Fatal(runs[1].Argv)
	}
}
