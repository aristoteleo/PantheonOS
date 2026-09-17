package nodefiles

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
)

// Hash decoded entries on the source machine. Only a verified, read-only
// snapshot handle can offer hashes; no paths or network destinations are added.
// Batches bound both inflated work and the RPC response size.
func hashZipEntries(h *handle, args map[string]any) (any, error) {
	names, ok := args["names"].([]any)
	if h.write || h.snapshot == nil || !ok || len(names) == 0 || len(names) > 64 {
		return nil, errors.New("ZIP hashes require a snapshot and 1–64 entry names")
	}
	unchanged := func() bool {
		info, err := h.file.Stat()
		return err == nil && info.Size() == h.snapshot.Size() && info.ModTime().Equal(h.snapshot.ModTime())
	}
	if !unchanged() {
		return nil, errors.New("source file changed during snapshot read")
	}
	archive, err := zip.NewReader(h.file, h.snapshot.Size())
	if err != nil {
		return nil, err
	}
	if len(archive.File) > 20000 {
		return nil, errors.New("too many ZIP entries")
	}
	entries := make(map[string]*zip.File)
	for _, entry := range archive.File {
		if _, duplicate := entries[entry.Name]; duplicate {
			return nil, errors.New("duplicate ZIP entry")
		}
		entries[entry.Name] = entry
	}
	var total uint64
	selected := make([]*zip.File, 0, len(names))
	seen := make(map[string]bool)
	for _, value := range names {
		name, valid := value.(string)
		entry := entries[name]
		if !valid || len(name) > 1024 || entry == nil || seen[name] || entry.Flags&1 != 0 || entry.UncompressedSize64 > 128*1024*1024 {
			return nil, errors.New("invalid ZIP hash entry")
		}
		seen[name] = true
		total += entry.UncompressedSize64
		if total > 512*1024*1024 {
			return nil, errors.New("ZIP hash batch exceeds 512 MiB")
		}
		selected = append(selected, entry)
	}
	result := make([]map[string]any, 0, len(selected))
	for _, entry := range selected {
		reader, err := entry.Open()
		if err != nil {
			return nil, err
		}
		digest := sha256.New()
		n, err := io.Copy(digest, io.LimitReader(reader, int64(entry.UncompressedSize64)+1))
		reader.Close()
		if err != nil || n != int64(entry.UncompressedSize64) {
			return nil, errors.New("ZIP content failed its integrity check")
		}
		result = append(result, map[string]any{"name": entry.Name, "size": n, "crc32": fmt.Sprintf("%08x", entry.CRC32), "sha256": hex.EncodeToString(digest.Sum(nil))})
	}
	if !unchanged() {
		return nil, errors.New("source file changed while hashing ZIP entries")
	}
	return map[string]any{"success": true, "entries": result}, nil
}
