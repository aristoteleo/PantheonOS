package pyruntime

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func archive(t *testing.T, name string) []byte {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	body := []byte("#!/bin/sh\necho fake uv\n")
	if err := tw.WriteHeader(&tar.Header{Name: "uv-x/" + name, Mode: 0o755, Size: int64(len(body)), Typeflag: tar.TypeReg}); err != nil {
		t.Fatal(err)
	}
	tw.Write(body)
	tw.Close()
	gz.Close()
	return buf.Bytes()
}

// The binary runs only the uv whose archive matches the published digest.
func TestUVIsInstalledOnlyWhenItsChecksumMatches(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("tar archive")
	}
	data := archive(t, "uv")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(data) }))
	defer server.Close()
	base, digests := downloadBase, uvSHA256
	defer func() { downloadBase, uvSHA256 = base, digests }()
	downloadBase = server.URL
	tgt, _ := target()
	uvSHA256 = map[string]string{tgt: strings.Repeat("0", 64)}
	root := t.TempDir()
	if _, err := ensureUV(context.Background(), root, func(string) {}); err == nil || !strings.Contains(err.Error(), "checksum") {
		t.Fatalf("a tampered uv was accepted: %v", err)
	}
	sum := sha256.Sum256(data)
	uvSHA256 = map[string]string{tgt: hex.EncodeToString(sum[:])}
	path, err := ensureUV(context.Background(), root, func(string) {})
	if err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(path).Output(); err != nil || strings.TrimSpace(string(out)) != "fake uv" {
		t.Fatal(string(out), err)
	}
	// Once installed it is not downloaded again.
	server.Close()
	if again, err := ensureUV(context.Background(), root, nil); err != nil || again != path {
		t.Fatal(again, err)
	}
}

// PANTHEON_RUNTIME_E2E=1: install the real uv and Python and build an
// environment from hash-pinned requirements.
func TestManagedPythonAndEnvironment(t *testing.T) {
	if os.Getenv("PANTHEON_RUNTIME_E2E") != "1" {
		t.Skip("set PANTHEON_RUNTIME_E2E=1")
	}
	root := t.TempDir()
	r, err := Ensure(context.Background(), root, func(s string) { t.Log(s) })
	if err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command(r.Python, "-c", "import sys; print(sys.version_info[:2])").Output()
	if err != nil || strings.TrimSpace(string(out)) != "(3, 12)" {
		t.Fatal(string(out), err)
	}
	if !strings.HasPrefix(r.Python, root) {
		t.Fatalf("not the managed Python: %s", r.Python)
	}
	requirements := filepath.Join(root, "requirements.txt")
	os.WriteFile(requirements, []byte("six==1.17.0 --hash=sha256:4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274\n"), 0o644)
	python, err := r.Environment(context.Background(), filepath.Join(root, "env"), requirements, nil)
	if err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(python, "-c", "import six; print(six.__version__)").Output(); err != nil || strings.TrimSpace(string(out)) != "1.17.0" {
		t.Fatal(string(out), err)
	}
}
