package nodefiles

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Optional real-document benchmark. Always operates on a temporary copy.
func TestFirstSaveLargeDocument(t *testing.T) {
	original, output := os.Getenv("OFFICE_REUSE_ORIGINAL"), os.Getenv("OFFICE_REUSE_OUTPUT")
	if original == "" || output == "" {
		t.Skip("set OFFICE_REUSE_ORIGINAL and OFFICE_REUSE_OUTPUT for the large-file benchmark")
	}
	before, err := os.ReadFile(original)
	if err != nil {
		t.Fatal(err)
	}
	after, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "copy.pptx")
	os.WriteFile(path, before, 0600)
	a, _ := New([]string{dir}, "test")
	defer a.Close()
	started := time.Now()
	opened := transferCall(t, a, "begin_atomic_write", map[string]any{"file_path": path, "expected_sha256": digest(before), "sha256": digest(after), "total_size": len(after), "reuse_zip": true, "reuse_decoded": true})
	offers := opened["reusable_ranges"].([]reusableRange)
	z, _ := zip.NewReader(bytes.NewReader(after), int64(len(after)))
	var ranges []any
	type gap struct{ start, end int64 }
	var gaps []gap
	cursor := int64(0)
	var reused int64
	for _, entry := range z.File {
		if entry.CompressedSize64 < 128*1024 {
			continue
		}
		offset, _ := entry.DataOffset()
		size := int64(entry.CompressedSize64)
		hash := sha256.Sum256(after[offset : offset+size])
		sha := hex.EncodeToString(hash[:])
		for _, offer := range offers {
			if offer.Size != size || offer.SHA != sha {
				continue
			}
			ranges = append(ranges, map[string]any{"source_offset": offer.Offset, "offset": offset, "size": size, "sha256": sha, "encoding": offer.Encoding})
			if cursor < offset {
				gaps = append(gaps, gap{cursor, offset})
			}
			cursor = offset + size
			reused += size
			break
		}
	}
	if cursor < int64(len(after)) {
		gaps = append(gaps, gap{cursor, int64(len(after))})
	}
	transferCall(t, a, "reuse_original_ranges", map[string]any{"handle_id": opened["handle_id"], "ranges": ranges})
	for _, g := range gaps {
		for offset := g.start; offset < g.end; {
			end := offset + 128*1024
			if end > g.end {
				end = g.end
			}
			transferCall(t, a, "write_chunk_at", map[string]any{"handle_id": opened["handle_id"], "offset": offset, "data": base64.StdEncoding.EncodeToString(after[offset:end])})
			offset = end
		}
	}
	transferCall(t, a, "commit_atomic_write", map[string]any{"handle_id": opened["handle_id"]})
	actual, _ := os.ReadFile(path)
	if !bytes.Equal(actual, after) {
		t.Fatal("saved bytes differ")
	}
	t.Logf("original=%d output=%d reused=%d uploaded=%d elapsed=%s", len(before), len(after), reused, int64(len(after))-reused, time.Since(started))
	if reused < int64(len(after))*9/10 {
		t.Fatal("expected at least 90 percent media reuse in this benchmark fixture")
	}
}
