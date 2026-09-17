package nodefiles

import (
	"archive/zip"
	"bytes"
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
)

func zipped(t *testing.T, text string, compressed ...bool) []byte {
	t.Helper()
	var buf bytes.Buffer
	z := zip.NewWriter(&buf)
	xml, _ := z.CreateHeader(&zip.FileHeader{Name: "doc.xml", Method: zip.Store})
	xml.Write([]byte(text))
	method := uint16(zip.Store)
	if len(compressed) > 0 && compressed[0] {
		method = zip.Deflate
	}
	media, _ := z.CreateHeader(&zip.FileHeader{Name: "media.bin", Method: method})
	media.Write(bytes.Repeat([]byte("large unchanged media"), 20000))
	if err := z.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestZipReuseAndVerifiedCommit(t *testing.T) {
	for _, encoded := range []bool{false, true} {
		for _, mode := range []string{"success", "modified source", "unoffered range", "wrong encoding"} {
			t.Run(mode, func(t *testing.T) {
				dir := t.TempDir()
				path := filepath.Join(dir, "deck.pptx")
				before, after := zipped(t, "old", encoded), zipped(t, "new XML changes entry positions")
				os.WriteFile(path, before, 0600)
				a, _ := New([]string{dir}, "node")
				defer a.Close()
				opened := transferCall(t, a, "begin_atomic_write", map[string]any{"file_path": path, "expected_sha256": digest(before), "sha256": digest(after), "total_size": len(after), "reuse_zip": true, "reuse_decoded": encoded})
				offered := opened["reusable_ranges"].([]reusableRange)
				if len(offered) != 1 {
					t.Fatal("expected large resource")
				}
				z, _ := zip.NewReader(bytes.NewReader(after), int64(len(after)))
				target, _ := z.File[1].DataOffset()
				source := offered[0]
				span := map[string]any{"source_offset": source.Offset, "offset": target, "size": source.Size, "sha256": source.SHA, "encoding": source.Encoding}
				if mode == "modified source" {
					if encoded {
						before[source.Offset+source.compressedSize/2] ^= 255
					} else {
						before[source.Offset] ^= 1
					}
					os.WriteFile(path, before, 0600)
				}
				if mode == "wrong encoding" {
					span["encoding"] = "unsupported"
				}
				if mode == "unoffered range" {
					span["source_offset"] = source.Offset + 1
				}
				result, err := a.transfer("reuse_original_ranges", map[string]any{"handle_id": opened["handle_id"], "ranges": []any{span}})
				if mode != "success" {
					if err == nil {
						t.Fatal("unsafe reuse accepted")
					}
					transferCall(t, a, "close_file", map[string]any{"handle_id": opened["handle_id"]})
					actual, _ := os.ReadFile(path)
					if !bytes.Equal(actual, before) {
						t.Fatal("original damaged")
					}
					return
				}
				if err != nil || result.(map[string]any)["bytes_written"] != source.Size {
					t.Fatal(result, err)
				}
				// Upload the ZIP metadata/XML around the unchanged compressed payload.
				for _, span := range [][2]int64{{0, target}, {target + source.Size, int64(len(after))}} {
					transferCall(t, a, "write_chunk_at", map[string]any{"handle_id": opened["handle_id"], "offset": span[0], "data": base64.StdEncoding.EncodeToString(after[span[0]:span[1]])})
				}
				transferCall(t, a, "commit_atomic_write", map[string]any{"handle_id": opened["handle_id"]})
				actual, _ := os.ReadFile(path)
				if !bytes.Equal(actual, after) {
					t.Fatal("reused save differs from editor output")
				}
			})
		}
	}

}

func TestDecodedZipReuseRequiresNegotiation(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "deck.pptx")
	before, after := zipped(t, "old", true), zipped(t, "new")
	os.WriteFile(path, before, 0600)
	a, _ := New([]string{dir}, "node")
	defer a.Close()
	opened := transferCall(t, a, "begin_atomic_write", map[string]any{"file_path": path, "expected_sha256": digest(before), "sha256": digest(after), "total_size": len(after), "reuse_zip": true})
	for _, offer := range opened["reusable_ranges"].([]reusableRange) {
		if offer.Encoding != "" {
			t.Fatal("decoded offer leaked to old client")
		}
	}
}
