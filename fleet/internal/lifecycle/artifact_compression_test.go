package lifecycle

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func gzipArtifact(t *testing.T, data []byte) []byte {
	t.Helper()
	var out bytes.Buffer
	w := gzip.NewWriter(&out)
	if _, err := w.Write(data); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return out.Bytes()
}

func stageCompressed(t *testing.T, m *Manager, data []byte) string {
	t.Helper()
	sum := sha256.Sum256(data)
	digest := hex.EncodeToString(sum[:])
	for offset := 0; offset < len(data); offset += MaxChunk {
		if _, err := m.Stage(digest, int64(offset), data[offset:min(offset+MaxChunk, len(data))]); err != nil {
			t.Fatal(err)
		}
	}
	return digest
}

func TestCompressedArtifactInstallAndManifest(t *testing.T) {
	m, _, _ := setup(t)
	raw, _ := bundle(t, definition(), map[string]string{
		"app.json":          `{"id":"example","version":"1.0.0"}`,
		"frontend/index.js": "export const hello = true;",
	})
	data := gzipArtifact(t, raw)
	digest := stageCompressed(t, m, data)
	if op := submit(t, m, digest, "compressed-install", "install", "app", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if _, err := m.InstalledManifest(digest); err != nil {
		t.Fatal(err)
	}
	exported, err := m.ArtifactBytes(digest)
	if err != nil || !bytes.Equal(exported, data) {
		t.Fatal("forwarding changed immutable artifact", err)
	}
	if m.Snapshot().ArtifactCompression != "gzip-v1" {
		t.Fatal("missing live compression capability")
	}
}

func TestCompressedArtifactRejectsInvalidStream(t *testing.T) {
	raw, _ := bundle(t, definition(), nil)
	valid := gzipArtifact(t, raw)
	badCRC := bytes.Clone(valid)
	badCRC[len(badCRC)-8] ^= 1
	var traversal bytes.Buffer
	w := tar.NewWriter(&traversal)
	_ = w.WriteHeader(&tar.Header{Name: "../outside", Size: 0, Mode: 0600})
	_ = w.Close()
	for name, data := range map[string][]byte{"checksum": badCRC, "truncated": valid[:len(valid)-3], "traversal": gzipArtifact(t, traversal.Bytes())} {
		t.Run(name, func(t *testing.T) {
			m, _, _ := setup(t)
			digest := stageCompressed(t, m, data)
			if _, err := m.unpack(digest); err == nil {
				t.Fatal("accepted invalid compressed artifact")
			}
			if _, err := os.Stat(filepath.Join(m.root, "packages", digest)); !os.IsNotExist(err) {
				t.Fatal("published incomplete extraction")
			}
		})
	}
}

func TestCompressedArtifactBoundsPaddingAndExpansion(t *testing.T) {
	var data bytes.Buffer
	gz := gzip.NewWriter(&data)
	block := make([]byte, 1<<20)
	for i := 0; i < 129; i++ {
		if _, err := gz.Write(block); err != nil {
			t.Fatal(err)
		}
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err := openArtifact(bytes.NewReader(data.Bytes()))
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	n, err := io.Copy(io.Discard, reader)
	if err == nil || n != MaxUnpackedArtifact {
		t.Fatal("unbounded decoded bytes", n, err)
	}
	m, _, _ := setup(t)
	digest := stageCompressed(t, m, data.Bytes())
	// An early tar EOF must not bypass the decoded-stream limit.
	if _, err := m.unpack(digest); err == nil {
		t.Fatal("accepted oversized tar padding")
	}
}

// Opt-in delivery acceptance for the actual paired Agent artifact. Uses the
// production native install hook, stage protocol and immutable manifest query.
// Python/browser tests separately exercise this same package's Agent runtime.
func TestInstallAgentReleaseArtifact(t *testing.T) {
	path := os.Getenv("FLEET_TEST_AGENT_ARTIFACT")
	if path == "" {
		t.Skip("supply the built Agent release artifact")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	driver := NativeDriver{}
	if cache := os.Getenv("FLEET_TEST_AGENT_CACHE"); cache != "" {
		driver.Environment = []string{"PANTHEON_PYTHON_CACHE=" + cache}
	}
	m, err := Open(t.TempDir(), "agent-release", "isolated-node", proto.Capability{OS: runtime.GOOS, Arch: runtime.GOARCH, Caps: []string{"proc"}}, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	digest := stageCompressed(t, m, data)
	op, err := m.Submit(Request{Protocol: 1, OperationID: "agent-install", Action: "install", Digest: digest, Scope: "candidate"})
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Minute)
	for op.State == "queued" || op.State == "running" {
		if time.Now().After(deadline) {
			t.Fatal("Agent release install timed out")
		}
		time.Sleep(50 * time.Millisecond)
		op = *m.Snapshot().Operations["agent-install"]
	}
	if op.State != "succeeded" {
		log, _ := os.ReadFile(filepath.Join(m.root, "installations", digest, "dependencies.log"))
		t.Log(string(log))
		t.Fatal(op)
	}
	if _, err := m.InstalledManifest(digest); err != nil {
		t.Fatal(err)
	}
	manifest, err := os.ReadFile(filepath.Join(m.root, "packages", digest, "release.json"))
	if err != nil || !bytes.Contains(manifest, []byte(`"frontend/index.js"`)) {
		t.Fatal("missing paired GUI release", err)
	}
}
