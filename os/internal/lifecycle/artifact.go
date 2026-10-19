package lifecycle

import (
	"archive/tar"
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

const MaxArtifact = 32 << 20
const MaxUnpackedArtifact = 128 << 20
const MaxChunk = 192 << 10

// Both extraction and manifest inspection bound the entire decoded stream,
// including tar metadata and padding, without buffering it in memory.
type artifactStream struct {
	reader    io.Reader
	closer    io.Closer
	remaining int64
}

func (r *artifactStream) Read(p []byte) (int, error) {
	if r.remaining == 0 {
		return 0, fmt.Errorf("unpacked App artifact exceeds limit")
	}
	if int64(len(p)) > r.remaining {
		p = p[:r.remaining]
	}
	n, err := r.reader.Read(p)
	r.remaining -= int64(n)
	return n, err
}
func (r *artifactStream) Close() error { return r.closer.Close() }

func openArtifact(src io.Reader) (io.ReadCloser, error) {
	buffer := bufio.NewReader(src)
	header, err := buffer.Peek(2)
	if err != nil {
		return nil, err
	}
	var reader io.ReadCloser = io.NopCloser(buffer)
	if bytes.Equal(header, []byte{0x1f, 0x8b}) {
		reader, err = gzip.NewReader(buffer)
		if err != nil {
			return nil, err
		}
	}
	return &artifactStream{reader: reader, closer: reader, remaining: MaxUnpackedArtifact}, nil
}

// ArtifactBytes exports only a complete staged code package, never an arbitrary
// node path. Used when forwarding an unchanged artifact to a scheduler job.
func (m *Manager) ArtifactBytes(digest string) ([]byte, error) {
	if !digestRE.MatchString(digest) {
		return nil, fmt.Errorf("invalid artifact digest")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	f, err := os.Open(filepath.Join(m.root, "artifacts", digest+".tar"))
	if err != nil {
		return nil, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, MaxArtifact+1))
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(b)
	if len(b) > MaxArtifact || hex.EncodeToString(sum[:]) != digest {
		return nil, fmt.Errorf("artifact is incomplete or has a different digest")
	}
	return b, nil
}

// Stage accepts bounded, retryable chunks on the authenticated Fleet bus.
// offset zero never truncates another caller's upload of the same digest.
func (m *Manager) Stage(digest string, offset int64, data []byte) (int64, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if !digestRE.MatchString(digest) || offset < 0 || len(data) > MaxChunk || offset+int64(len(data)) > MaxArtifact {
		return 0, fmt.Errorf("invalid artifact chunk")
	}
	f, err := os.OpenFile(filepath.Join(m.root, "artifacts", digest+".tar"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return 0, err
	}
	if offset < st.Size() {
		old := make([]byte, len(data))
		n, _ := f.ReadAt(old, offset)
		if n != len(data) || !bytes.Equal(old, data) {
			return st.Size(), fmt.Errorf("artifact offset conflict")
		}
		return st.Size(), nil
	}
	if offset != st.Size() {
		return st.Size(), fmt.Errorf("artifact offset mismatch")
	}
	if _, err = f.WriteAt(data, offset); err != nil {
		return st.Size(), err
	}
	err = f.Sync()
	return offset + int64(len(data)), err
}

func (m *Manager) unpack(digest string) (Definition, error) {
	var def Definition
	src, err := os.Open(filepath.Join(m.root, "artifacts", digest+".tar"))
	if err != nil {
		return def, err
	}
	defer src.Close()
	hash := sha256.New()
	if _, err = io.Copy(hash, io.LimitReader(src, MaxArtifact+1)); err != nil {
		return def, err
	}
	if hex.EncodeToString(hash.Sum(nil)) != digest {
		return def, fmt.Errorf("artifact SHA-256 mismatch")
	}
	if _, err = src.Seek(0, 0); err != nil {
		return def, err
	}
	// Only a freshly created directory is extracted. Links, special files and
	// duplicate entries are rejected, so extraction cannot follow a tar symlink.
	dest := filepath.Join(m.root, "packages", digest)
	if _, err = os.Stat(dest); err == nil {
		b, e := os.ReadFile(filepath.Join(dest, "fleet.json"))
		if e != nil {
			return def, e
		}
		e = StrictDecode(b, &def)
		if e != nil {
			return def, e
		}
		return def, def.Validate()
	}
	stage, err := os.MkdirTemp(filepath.Join(m.root, "packages"), ".stage-")
	if err != nil {
		return def, err
	}
	defer os.RemoveAll(stage)
	decoded, err := openArtifact(src)
	if err != nil {
		return def, err
	}
	defer decoded.Close()
	tr := tar.NewReader(decoded)
	seen := map[string]bool{}
	var total int64
	for {
		h, e := tr.Next()
		if e == io.EOF {
			break
		}
		if e != nil {
			return def, e
		}
		if !relative(h.Name) || seen[h.Name] || len(seen) >= 10000 {
			return def, fmt.Errorf("unsafe/duplicate archive path")
		}
		seen[h.Name] = true
		p := filepath.Join(stage, h.Name)
		switch h.Typeflag {
		case tar.TypeDir:
			if e = os.MkdirAll(p, 0700); e != nil {
				return def, e
			}
		case tar.TypeReg:
			total += h.Size
			if h.Size < 0 || total > MaxUnpackedArtifact {
				return def, fmt.Errorf("artifact too large")
			}
			if e = os.MkdirAll(filepath.Dir(p), 0700); e != nil {
				return def, e
			}
			f, e := os.OpenFile(p, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
			if e != nil {
				return def, e
			}
			_, e = io.Copy(f, tr)
			if e == nil {
				e = f.Sync()
			}
			closeErr := f.Close()
			if e != nil {
				return def, e
			}
			if closeErr != nil {
				return def, closeErr
			}
			mode := os.FileMode(0400)
			if h.Mode&0111 != 0 {
				mode = 0500
			}
			if e = os.Chmod(p, mode); e != nil {
				return def, e
			}
		default:
			return def, fmt.Errorf("links and special files are not allowed in App packages")
		}
	}
	// tar EOF can precede gzip's checksum. Verify its trailer before publishing.
	if _, err = io.Copy(io.Discard, decoded); err != nil {
		return def, err
	}
	b, err := os.ReadFile(filepath.Join(stage, "fleet.json"))
	if err != nil {
		return def, err
	}
	if err = StrictDecode(b, &def); err != nil {
		return def, err
	}
	if err = def.Validate(); err != nil {
		return def, err
	}
	err = os.Rename(stage, dest)
	return def, err
}
