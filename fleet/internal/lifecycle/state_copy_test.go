package lifecycle

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func copyRoots(t *testing.T) (*os.Root, *os.Root) {
	t.Helper()
	src, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	dst, err := os.OpenRoot(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { src.Close(); dst.Close() })
	return src, dst
}

func seedLargeState(t *testing.T, root *os.Root) {
	t.Helper()
	f, err := root.Create("history.db")
	if err != nil {
		t.Fatal(err)
	}
	block := bytes.Repeat([]byte("agent-history-"), 8192)
	for n := 0; n < 1024; n++ {
		if _, err := f.Write(block); err != nil {
			t.Fatal(err)
		}
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
}

func stateHash(t *testing.T, root *os.Root) []byte {
	t.Helper()
	f, err := root.Open("history.db")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		t.Fatal(err)
	}
	return h.Sum(nil)
}

func TestStateCopyLargeHistoryUsesBoundedAllocation(t *testing.T) {
	src, dst := copyRoots(t)
	seedLargeState(t, src) // 112 MiB, well above the previous 64 MiB limit.
	expected := stateHash(t, src)
	runtime.GC()
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)
	if err := copyAppState(context.Background(), src, dst, &DataSource{"source", 2}, nil); err != nil {
		t.Fatal(err)
	}
	runtime.ReadMemStats(&after)
	allocated := after.TotalAlloc - before.TotalAlloc
	if allocated > 16<<20 {
		t.Fatalf("copy allocated %d bytes for a 112 MiB history", allocated)
	}
	if !bytes.Equal(expected, stateHash(t, dst)) || !bytes.Equal(expected, stateHash(t, src)) {
		t.Fatal("history changed")
	}
	t.Logf("112 MiB state copied with %d bytes total Go allocation", allocated)
}

func TestStateCopyManyConversationsAndExplicitLimits(t *testing.T) {
	src, dst := copyRoots(t)
	for n := 0; n < 10005; n++ {
		if err := src.WriteFile(fmt.Sprintf("chat-%05d.json", n), nil, 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := copyAppState(context.Background(), src, dst, &DataSource{"source", 2}, &StateCopyPolicy{MaxEntries: 10000}); err == nil {
		t.Fatal("entry limit ignored")
	}
	entries, err := os.ReadDir(dst.Name())
	if err != nil || len(entries) != 0 {
		t.Fatal("preflight wrote data", err)
	}
	if err := copyAppState(context.Background(), src, dst, &DataSource{"source", 2}, nil); err != nil {
		t.Fatal(err)
	}
	entries, err = os.ReadDir(dst.Name())
	if err != nil || len(entries) != 10006 {
		t.Fatal("missing conversation state", len(entries), err)
	}
}

type cancelCopyContext struct {
	context.Context
	checks int
}

func (c *cancelCopyContext) Err() error {
	c.checks++
	if c.checks >= 20 {
		return context.Canceled
	}
	return nil
}

func TestStateCopyCancellationDuringLargeFileDoesNotPublishReceipt(t *testing.T) {
	src, dst := copyRoots(t)
	seedLargeState(t, src)
	ctx := &cancelCopyContext{Context: context.Background()}
	if err := copyAppState(ctx, src, dst, &DataSource{"source", 2}, nil); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if _, err := dst.Stat(importReceipt); !os.IsNotExist(err) {
		t.Fatal("cancelled copy published receipt", err)
	}
	partial, err := dst.Stat("history.db")
	if err != nil {
		t.Fatal(err)
	}
	whole, err := src.Stat("history.db")
	if err != nil {
		t.Fatal(err)
	}
	if partial.Size() <= 0 || partial.Size() >= whole.Size() {
		t.Fatal("did not interrupt mid-file")
	}
}

func TestStateCopyPolicyAndDiskPreflight(t *testing.T) {
	for _, value := range []StateCopyPolicy{{MaxBytes: -1}, {MaxEntries: -1}, {ReserveBytes: -1}, {MaxBytes: 1 << 51}, {MaxEntries: 10000001}} {
		if _, err := stateCopyPolicy(&value); err == nil {
			t.Fatal("invalid policy accepted", value)
		}
	}
	for _, free := range []uint64{0, 1023, 1024, 2047} {
		if err := checkCopySpace(free, 1024, 1024); err == nil {
			t.Fatal("insufficient space accepted")
		}
	}
	if err := checkCopySpace(2048, 1024, 1024); err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "resource-policy.json"), []byte(`{"state_copy":{"max_bytes":1073741824,"max_entries":20000,"reserve_bytes":536870912}}`), 0600); err != nil {
		t.Fatal(err)
	}
	m, err := Open(root, "owner", "node", proto.Capability{}, &fakeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	p := m.ResourceStatus().Policy.StateCopy
	if p == nil || p.MaxBytes != 1<<30 || p.MaxEntries != 20000 || p.ReserveBytes != 512<<20 {
		t.Fatal("owner policy lost", p)
	}
}
