package lifecycle

// Moving an App's state to another node (docs/fleet-orchestration.md §8). The
// source Runner exports a stopped generation's data as a gzip tar archive,
// read in bounded chunks; the owner's controller relays the chunks to the
// target Runner (ImportStage) and submits import_data, which materializes the
// archive as the new instance's generation-0 state with the same receipt
// semantics as clone_data. Only regular files and directories are carried.

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"reflect"
	"strings"
)

// MaxMoveBytes bounds an exported (compressed) archive; larger state belongs
// in the workspace or the model cache, which are not moved.
const MaxMoveBytes = 256 << 20

// MaxMoveChunk keeps relayed messages well under the bus payload limit.
const MaxMoveChunk = 128 << 10

// ExportChunk is one read of an exported archive.
type ExportChunk struct {
	SHA256 string      `json:"sha256"`
	Size   int64       `json:"size"`
	Offset int64       `json:"offset"`
	Data   []byte      `json:"data"`
	AppID  string      `json:"app_id"`
	Schema *DataSchema `json:"schema,omitempty"`
}

// ExportData reads the archive of a stopped generation, building it once.
func (m *Manager) ExportData(ctx context.Context, digest, scope string, generation uint64, offset int64) (ExportChunk, error) {
	if !digestRE.MatchString(digest) || !nameRE.MatchString(scope) || generation == 0 || offset < 0 {
		return ExportChunk{}, fmt.Errorf("invalid state export")
	}
	m.mu.Lock()
	key := m.instanceID(digest, scope)
	in := clone(m.ledger.Instances[key])
	install := clone(m.ledger.Installations[digest])
	m.mu.Unlock()
	if in == nil || in.Generation != generation || in.State != "stopped" || len(in.Resources) != 0 || len(in.Reservations) != 0 {
		return ExportChunk{}, fmt.Errorf("export requires the exact stopped generation of this App")
	}
	if install == nil || install.State != "installed" {
		return ExportChunk{}, fmt.Errorf("export requires the source installation's data schema")
	}
	dir := filepath.Join(m.root, "exports")
	if err := os.MkdirAll(dir, 0700); err != nil {
		return ExportChunk{}, err
	}
	name := filepath.Join(dir, fmt.Sprintf("%s-%d.tar.gz", key, generation))
	sum, err := os.ReadFile(name + ".sha256")
	if err != nil {
		if sum, err = m.buildExport(ctx, key, name); err != nil {
			return ExportChunk{}, err
		}
	}
	f, err := os.Open(name)
	if err != nil {
		return ExportChunk{}, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return ExportChunk{}, err
	}
	if offset > st.Size() {
		return ExportChunk{}, fmt.Errorf("export offset beyond the archive")
	}
	data := make([]byte, min(int64(MaxMoveChunk), st.Size()-offset))
	if _, err := f.ReadAt(data, offset); err != nil && !errors.Is(err, io.EOF) {
		return ExportChunk{}, err
	}
	return ExportChunk{SHA256: string(sum), Size: st.Size(), Offset: offset, Data: data,
		AppID: install.Definition.AppID, Schema: install.Definition.DataSchema}, nil
}

func (m *Manager) buildExport(ctx context.Context, key, name string) ([]byte, error) {
	root, err := os.OpenRoot(filepath.Join(m.root, "data"))
	if err != nil {
		return nil, err
	}
	defer root.Close()
	tmp, err := os.CreateTemp(filepath.Dir(name), ".export-")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	hash := sha256.New()
	counter := &limitWriter{w: io.MultiWriter(tmp, hash), left: MaxMoveBytes}
	gz := gzip.NewWriter(counter)
	tw := tar.NewWriter(gz)
	if _, err := root.Lstat(key); err == nil {
		src, err := root.OpenRoot(key)
		if err != nil {
			tmp.Close()
			return nil, err
		}
		err = walkAppState(ctx, src, func(rel string, info fs.FileInfo) error {
			h := &tar.Header{Name: filepath.ToSlash(rel), Mode: 0o600, ModTime: info.ModTime()}
			if info.IsDir() {
				h.Typeflag, h.Name, h.Mode = tar.TypeDir, h.Name+"/", 0o700
				return tw.WriteHeader(h)
			}
			h.Typeflag, h.Size = tar.TypeReg, info.Size()
			if err := tw.WriteHeader(h); err != nil {
				return err
			}
			f, err := src.Open(rel)
			if err != nil {
				return err
			}
			defer f.Close()
			n, err := io.Copy(tw, io.LimitReader(f, info.Size()))
			if err == nil && n != info.Size() {
				err = fmt.Errorf("App state file changed during export")
			}
			return err
		})
		src.Close()
		if err != nil {
			tmp.Close()
			return nil, err
		}
	}
	err = errors.Join(tw.Close(), gz.Close(), tmp.Sync(), tmp.Close())
	if counter.left < 0 {
		return nil, fmt.Errorf("App state exceeds the %d MiB move limit; keep large data in the workspace", MaxMoveBytes>>20)
	}
	if err != nil {
		return nil, err
	}
	if err := os.Rename(tmp.Name(), name); err != nil {
		return nil, err
	}
	sum := []byte(hex.EncodeToString(hash.Sum(nil)))
	return sum, os.WriteFile(name+".sha256", sum, 0o600)
}

type limitWriter struct {
	w    io.Writer
	left int64
}

func (l *limitWriter) Write(p []byte) (int, error) {
	l.left -= int64(len(p))
	if l.left < 0 {
		return 0, fmt.Errorf("App state exceeds the move limit")
	}
	return l.w.Write(p)
}

// ImportStage accepts relayed chunks of an archive, resumable like Stage.
func (m *Manager) ImportStage(sum string, offset int64, data []byte) (int64, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if !digestRE.MatchString(sum) || offset < 0 || len(data) > MaxMoveChunk || offset+int64(len(data)) > MaxMoveBytes {
		return 0, fmt.Errorf("invalid state import chunk")
	}
	if err := os.MkdirAll(filepath.Join(m.root, "imports"), 0700); err != nil {
		return 0, err
	}
	f, err := os.OpenFile(filepath.Join(m.root, "imports", sum+".tar.gz"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return 0, err
	}
	if offset < st.Size() {
		return st.Size(), nil // already received (a retried relay)
	}
	if offset != st.Size() {
		return st.Size(), fmt.Errorf("state import offset mismatch")
	}
	if _, err := f.WriteAt(data, offset); err != nil {
		return st.Size(), err
	}
	return offset + int64(len(data)), f.Sync()
}

func (m *Manager) importData(ctx context.Context, op *Operation, install *Installation, target *Instance) error {
	req := op.Request
	if install == nil || install.State != "installed" {
		return fmt.Errorf("install the destination artifact before importing state")
	}
	if target != nil && (target.Generation != 0 || target.State != "stopped" || len(target.Resources) != 0 ||
		len(target.Reservations) != 0 || !reflect.DeepEqual(target.DataSource, req.DataSource)) {
		return fmt.Errorf("destination state has already been used; it will not be overwritten")
	}
	if err := compatibleDataSchemas(req.DataSource.Schema, install.Definition.DataSchema); err != nil {
		return err
	}
	key := m.instanceID(req.Digest, req.Scope)
	if err := m.step(op, "import_app_state", func() (Receipt, error) {
		data, err := os.OpenRoot(filepath.Join(m.root, "data"))
		if err != nil {
			return Receipt{}, err
		}
		defer data.Close()
		receiptPath := filepath.Join(key, importReceipt)
		if raw, err := data.ReadFile(receiptPath); err == nil {
			var existing DataSource
			if json.Unmarshal(raw, &existing) != nil || !reflect.DeepEqual(&existing, req.DataSource) {
				return Receipt{}, fmt.Errorf("destination state has a different source")
			}
			return Receipt{Status: "succeeded"}, nil
		}
		if _, err := data.Lstat(key); !os.IsNotExist(err) {
			return Receipt{}, fmt.Errorf("destination data exists without a matching import receipt")
		}
		archive := filepath.Join(m.root, "imports", req.DataSource.Archive+".tar.gz")
		if err := verifySHA256(archive, req.DataSource.Archive); err != nil {
			return Receipt{}, err
		}
		nonce := make([]byte, 12)
		if _, err := rand.Read(nonce); err != nil {
			return Receipt{}, err
		}
		temporary := ".import-" + hex.EncodeToString(nonce)
		if err := data.Mkdir(temporary, 0700); err != nil {
			return Receipt{}, err
		}
		defer data.RemoveAll(temporary)
		dst, err := data.OpenRoot(temporary)
		if err != nil {
			return Receipt{}, err
		}
		err = extractState(ctx, archive, dst, req.DataSource, m.resourcePolicy.StateCopy)
		if closeErr := dst.Close(); err == nil {
			err = closeErr
		}
		if err != nil {
			return Receipt{}, err
		}
		if err := data.Rename(temporary, key); err != nil {
			return Receipt{}, err
		}
		_ = os.Remove(archive)
		return Receipt{Status: "succeeded"}, nil
	}); err != nil {
		return err
	}
	return m.update(func() {
		m.ledger.Instances[key] = &Instance{ID: key, AppID: install.Definition.AppID,
			Version: install.Definition.Version, Digest: req.Digest, Scope: req.Scope,
			State: "stopped", DataSource: clone(req.DataSource), Resources: []Resource{}}
	})
}

func verifySHA256(name, want string) error {
	f, err := os.Open(name)
	if err != nil {
		return fmt.Errorf("the state archive has not been staged on this node")
	}
	defer f.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, io.LimitReader(f, MaxMoveBytes+1)); err != nil {
		return err
	}
	if hex.EncodeToString(hash.Sum(nil)) != want {
		return fmt.Errorf("the staged state archive is incomplete or differs from its export")
	}
	return nil
}

// extractState writes regular files and directories only, inside dst, within
// the node's state-copy policy, then the import receipt.
func extractState(ctx context.Context, archive string, dst *os.Root, source *DataSource, policy *StateCopyPolicy) error {
	p, err := stateCopyPolicy(policy)
	if err != nil {
		return err
	}
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("invalid state archive")
	}
	tr := tar.NewReader(gz)
	var bytes int64
	entries := 0
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return fmt.Errorf("invalid state archive")
		}
		name := path.Clean(h.Name)
		if name == "." || strings.HasPrefix(name, "../") || name == ".." || path.IsAbs(name) || name == importReceipt || !relative(name) {
			return fmt.Errorf("state archive contains an unsafe path")
		}
		if entries++; entries > p.MaxEntries {
			return fmt.Errorf("App state exceeds node copy limit of %d entries", p.MaxEntries)
		}
		switch h.Typeflag {
		case tar.TypeDir:
			if err := dst.MkdirAll(filepath.FromSlash(name), 0700); err != nil {
				return err
			}
		case tar.TypeReg:
			if h.Size < 0 || h.Size > p.MaxBytes-bytes {
				return fmt.Errorf("App state exceeds node copy limit of %d bytes", p.MaxBytes)
			}
			bytes += h.Size
			if dir := path.Dir(name); dir != "." {
				if err := dst.MkdirAll(filepath.FromSlash(dir), 0700); err != nil {
					return err
				}
			}
			out, err := dst.OpenFile(filepath.FromSlash(name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
			if err != nil {
				return err
			}
			n, err := io.Copy(out, io.LimitReader(tr, h.Size))
			if err == nil && n != h.Size {
				err = fmt.Errorf("truncated state archive")
			}
			if err == nil {
				err = out.Sync()
			}
			if closeErr := out.Close(); err == nil {
				err = closeErr
			}
			if err != nil {
				return err
			}
		default:
			return fmt.Errorf("state archive contains a link or special file")
		}
	}
	raw, err := json.Marshal(source)
	if err != nil {
		return err
	}
	out, err := dst.OpenFile(importReceipt, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, err = out.Write(raw)
	if err == nil {
		err = out.Sync()
	}
	return errors.Join(err, out.Close())
}
