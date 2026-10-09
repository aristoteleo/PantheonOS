package reconciler

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func archive(t *testing.T, files map[string][]byte) ([]byte, string) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	tw.WriteHeader(&tar.Header{Name: "./", Typeflag: tar.TypeDir, Mode: 0o755})
	for name, body := range files {
		if err := tw.WriteHeader(&tar.Header{Name: name, Typeflag: tar.TypeReg, Mode: 0o644, Size: int64(len(body))}); err != nil {
			t.Fatal(err)
		}
		tw.Write(body)
	}
	tw.Close()
	gz.Close()
	sum := sha256.Sum256(buf.Bytes())
	return buf.Bytes(), hex.EncodeToString(sum[:])
}

func TestReleaseIsVerifiedCachedAndKeepsOnlyArtifacts(t *testing.T) {
	payload := []byte("an App artifact")
	sum := sha256.Sum256(payload)
	rev := hex.EncodeToString(sum[:])
	index, _ := json.Marshal(Index{Protocol: 2, Apps: map[string]map[string]Variant{"agent": {"linux-amd64": {
		Path: "agent", AppID: "pantheon-agent", Version: "1.0.0", Revision: rev, Bytes: int64(len(payload)), Artifact: "artifacts/" + rev, Requires: []string{"proc"}}}}})
	body, pin := archive(t, map[string][]byte{"./release-set.json": index, "./artifacts/" + rev: payload, "./agent/app.json": []byte("{}"), "./profile.json": []byte(`{"protocol":1}`)})
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hits++; w.Write(body) }))
	defer server.Close()
	releases, err := NewReleases(t.TempDir(), server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := releases.Get(context.Background(), server.URL, strings.Repeat("0", 64)); err == nil || !strings.Contains(err.Error(), "SHA-256") {
		t.Fatalf("a wrong pin is refused: %v", err)
	}
	r, err := releases.Get(context.Background(), server.URL, pin)
	if err != nil {
		t.Fatal(err)
	}
	got, err := r.Artifact(r.Index.Apps["agent"]["linux-amd64"])
	if err != nil || !bytes.Equal(got, payload) {
		t.Fatalf("artifact: %v %q", err, got)
	}
	if profile, err := r.Profile(); err != nil || string(profile) != `{"protocol":1}` {
		t.Fatalf("profile kept: %v %s", err, profile)
	}
	again, _ := NewReleases(releases.root, server.Client())
	if _, err := again.Get(context.Background(), server.URL, pin); err != nil || hits != 2 {
		t.Fatalf("a cached release is not downloaded again: %v (%d downloads)", err, hits)
	}
}

func TestReleaseWithoutArtifactsIsRefused(t *testing.T) {
	index := []byte(`{"protocol":1,"apps":{"agent":{"linux-amd64":{"path":"agent","app_id":"pantheon-agent","version":"1.0.0","revision":"` + strings.Repeat("a", 64) + `","bytes":3}}}}`)
	body, pin := archive(t, map[string][]byte{"release-set.json": index})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(body) }))
	defer server.Close()
	releases, _ := NewReleases(t.TempDir(), server.Client())
	if _, err := releases.Get(context.Background(), server.URL, pin); err == nil || !strings.Contains(err.Error(), "rebuild") {
		t.Fatalf("protocol 1 release sets need a rebuild: %v", err)
	}
}
