package lifecycle

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/shirou/gopsutil/v4/disk"
)

const maxCloneBytes int64 = 64 << 30
const copyBufferBytes = 256 << 10

// StateCopyPolicy is a node-owner policy, never an App-controlled declaration.
// Zero fields use defaults; values are frozen when the Manager opens the node.
type StateCopyPolicy struct {
	MaxBytes     int64 `json:"max_bytes,omitempty"`
	MaxEntries   int   `json:"max_entries,omitempty"`
	ReserveBytes int64 `json:"reserve_bytes,omitempty"`
}

func stateCopyPolicy(value *StateCopyPolicy) (StateCopyPolicy, error) {
	p := StateCopyPolicy{MaxBytes: maxCloneBytes, MaxEntries: 1000000, ReserveBytes: 1 << 30}
	if value != nil {
		if value.MaxBytes != 0 {
			p.MaxBytes = value.MaxBytes
		}
		if value.MaxEntries != 0 {
			p.MaxEntries = value.MaxEntries
		}
		if value.ReserveBytes != 0 {
			p.ReserveBytes = value.ReserveBytes
		}
	}
	if p.MaxBytes < 1 || p.MaxBytes > 1<<50 || p.MaxEntries < 1 || p.MaxEntries > 10000000 || p.ReserveBytes < 1 || p.ReserveBytes > 1<<50 {
		return p, fmt.Errorf("invalid node state-copy policy")
	}
	return p, nil
}

// Read directories in batches, without sorting/materializing an entire large
// conversation directory. Depth and the number of simultaneously open handles
// are bounded independently of the number of files.
func walkAppState(ctx context.Context, root *os.Root, visit func(string, fs.FileInfo) error) error {
	var walk func(string, int) error
	walk = func(name string, depth int) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if depth > 128 {
			return fmt.Errorf("App state exceeds 128 directory levels")
		}
		before, err := root.Lstat(name)
		if err != nil {
			return err
		}
		if !before.IsDir() {
			return fmt.Errorf("App state directory changed or is a link")
		}
		dir, err := root.Open(name)
		if err != nil {
			return err
		}
		defer dir.Close()
		after, err := dir.Stat()
		if err != nil {
			return err
		}
		if !os.SameFile(before, after) {
			return fmt.Errorf("App state directory changed during copy")
		}
		for {
			if err := ctx.Err(); err != nil {
				return err
			}
			entries, err := dir.ReadDir(128)
			if err != nil && err != io.EOF {
				return err
			}
			for _, entry := range entries {
				path := filepath.Join(name, entry.Name())
				if path == importReceipt {
					continue
				}
				info, err := entry.Info()
				if err != nil {
					return err
				}
				if !info.IsDir() && !info.Mode().IsRegular() {
					return fmt.Errorf("App state contains a link or special file")
				}
				if err := visit(path, info); err != nil {
					return err
				}
				if info.IsDir() {
					if err := walk(path, depth+1); err != nil {
						return err
					}
				}
			}
			if err == io.EOF {
				return nil
			}
		}
	}
	return walk(".", 0)
}

type stateCopySize struct {
	bytes   int64
	entries int
}

func (s *stateCopySize) add(info fs.FileInfo, p StateCopyPolicy) error {
	s.entries++
	if s.entries > p.MaxEntries {
		return fmt.Errorf("App state exceeds node copy limit of %d entries", p.MaxEntries)
	}
	if info.Mode().IsRegular() {
		if info.Size() < 0 || info.Size() > p.MaxBytes-s.bytes {
			return fmt.Errorf("App state exceeds node copy limit of %d bytes", p.MaxBytes)
		}
		s.bytes += info.Size()
	}
	return nil
}

func checkCopySpace(free uint64, bytes, reserve int64) error {
	if free < uint64(reserve) || uint64(bytes) > free-uint64(reserve) {
		return fmt.Errorf("insufficient disk space for App state copy: need %d bytes plus %d reserved bytes", bytes, reserve)
	}
	return nil
}

type copyContextReader struct {
	context.Context
	io.Reader
}

func (r copyContextReader) Read(p []byte) (int, error) {
	if err := r.Err(); err != nil {
		return 0, err
	}
	return r.Reader.Read(p)
}

func copyStateFile(ctx context.Context, src, dst *os.Root, name string, expected fs.FileInfo, buffer []byte) (err error) {
	input, err := src.Open(name)
	if err != nil {
		return err
	}
	defer input.Close()
	before, err := input.Stat()
	if err != nil {
		return err
	}
	if !before.Mode().IsRegular() || !os.SameFile(expected, before) || before.Size() != expected.Size() || !before.ModTime().Equal(expected.ModTime()) {
		return fmt.Errorf("App state file changed before copy")
	}
	output, err := dst.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer func() {
		if closeErr := output.Close(); err == nil {
			err = closeErr
		}
	}()
	// Hide File.ReadFrom/WriteTo so cancellation and the shared buffer cannot be
	// bypassed by platform-specific whole-file fast paths.
	n, err := io.CopyBuffer(struct{ io.Writer }{output}, io.LimitReader(copyContextReader{ctx, input}, before.Size()+1), buffer)
	if err != nil {
		return err
	}
	after, err := input.Stat()
	if err != nil {
		return err
	}
	if n != before.Size() || after.Size() != before.Size() || !after.ModTime().Equal(before.ModTime()) {
		return fmt.Errorf("App state file changed during copy")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return output.Sync()
}

func copyAppState(ctx context.Context, src, dst *os.Root, source *DataSource, policy *StateCopyPolicy) error {
	p, err := stateCopyPolicy(policy)
	if err != nil {
		return err
	}
	var planned stateCopySize
	if err := walkAppState(ctx, src, func(_ string, info fs.FileInfo) error { return planned.add(info, p) }); err != nil {
		return err
	}
	space, err := disk.UsageWithContext(ctx, dst.Name())
	if err != nil {
		return fmt.Errorf("cannot inspect App state copy disk budget: %w", err)
	}
	if err := checkCopySpace(space.Free, planned.bytes, p.ReserveBytes); err != nil {
		return err
	}
	buffer := make([]byte, copyBufferBytes)
	var copied stateCopySize
	err = walkAppState(ctx, src, func(name string, info fs.FileInfo) error {
		if err := copied.add(info, p); err != nil {
			return err
		}
		if info.IsDir() {
			return dst.Mkdir(name, 0700)
		}
		return copyStateFile(ctx, src, dst, name, info, buffer)
	})
	if err != nil {
		return err
	}
	if copied != planned {
		return fmt.Errorf("App state changed between copy preflight and completion")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	raw, err := json.Marshal(source)
	if err != nil {
		return err
	}
	f, err := dst.OpenFile(importReceipt, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, err = f.Write(raw)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	return closeErr
}
