package nodefiles

import (
	"archive/zip"
	"compress/flate"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
)

// Offers are hashed locally; bounded decoded offers also allow first-save reuse
// across ZIP encoders. Every copied span and the final output are SHA-256 checked.
type reusableRange struct {
	Offset         int64  `json:"offset"`
	Size           int64  `json:"size"`
	SHA            string `json:"sha256"`
	Encoding       string `json:"encoding,omitempty"`
	compressedSize int64
}

type reuseKey struct {
	offset   int64
	encoding string
}

func zipReuseRanges(root *sharedRoot, path string, decoded bool) []reusableRange {
	f, err := root.dir.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	info, err := regular(f)
	if err != nil {
		return nil
	}
	z, err := zip.NewReader(f, info.Size())
	if err != nil {
		return nil
	} // Non-ZIP documents use the ordinary upload.
	var ranges []reusableRange
	var scanned, expanded int64
	for _, entry := range z.File {
		if len(ranges) >= 512 {
			break
		}
		if entry.Flags&1 != 0 || entry.CompressedSize64 > uint64(info.Size()) || (entry.CompressedSize64 < 128*1024 && (!decoded || entry.UncompressedSize64 < 128*1024)) {
			continue
		}
		offset, err := entry.DataOffset()
		size := int64(entry.CompressedSize64)
		if err != nil || offset < 0 || offset > info.Size()-size || scanned > info.Size()-size {
			return nil
		}
		scanned += size // Bound work even for malicious overlapping entries.
		hash := sha256.New()
		n, err := io.Copy(hash, io.NewSectionReader(f, offset, size))
		if err != nil || n != size {
			return nil
		}
		if size >= 128*1024 {
			ranges = append(ranges, reusableRange{Offset: offset, Size: size, SHA: hex.EncodeToString(hash.Sum(nil))})
		}
		expandedSize := int64(entry.UncompressedSize64)
		if decoded && entry.Method == zip.Deflate && entry.UncompressedSize64 <= 128*1024*1024 && expandedSize >= 128*1024 && expanded <= 512*1024*1024-expandedSize && len(ranges) < 512 {
			expanded += expandedSize
			reader, err := entry.Open()
			if err != nil {
				return nil
			}
			hash.Reset()
			n, err := io.Copy(hash, io.LimitReader(reader, expandedSize+1))
			reader.Close()
			if err != nil || n != expandedSize {
				return nil
			}
			ranges = append(ranges, reusableRange{Offset: offset, Size: expandedSize, SHA: hex.EncodeToString(hash.Sum(nil)), Encoding: "inflate", compressedSize: size})
		}
	}
	return ranges
}

func (a *App) reuseOriginalRanges(h *handle, p map[string]any) (any, error) {
	u := h.update
	spans, ok := p["ranges"].([]any)
	if u == nil || !ok || len(spans) > 512 {
		return nil, errors.New("invalid reuse request")
	}
	type copySpan struct {
		source, target, size int64
		sha                  string
		encoding             string
		compressedSize       int64
	}
	selected := make([]copySpan, 0, len(spans))
	var total int64
	for _, raw := range spans {
		span, ok := raw.(map[string]any)
		if !ok {
			return nil, errors.New("invalid reuse range")
		}
		source, target, size := number(span, "source_offset", -1), number(span, "offset", -1), number(span, "size", -1)
		offered, found := u.reuse[reuseKey{source, str(span, "encoding")}]
		if !found || offered.Size != size || offered.SHA != str(span, "sha256") || target < 0 || size <= 0 || target > u.size-size || total > u.size-size {
			return nil, errors.New("reuse range was not offered or exceeds output bounds")
		}
		total += size
		selected = append(selected, copySpan{source, target, size, offered.SHA, offered.Encoding, offered.compressedSize})
	}
	f, err := u.root.dir.Open(u.target)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	if _, err := regular(f); err != nil {
		return nil, err
	}
	for _, span := range selected {
		hash := sha256.New()
		var reader io.Reader = io.NewSectionReader(f, span.source, span.size)
		var decoder io.ReadCloser
		if span.encoding == "inflate" {
			decoder = flate.NewReader(io.NewSectionReader(f, span.source, span.compressedSize))
			reader = decoder
		}
		n, err := io.Copy(io.NewOffsetWriter(h.file, span.target), io.TeeReader(io.LimitReader(reader, span.size+1), hash))
		if decoder != nil {
			decoder.Close()
		}
		if err != nil {
			return nil, err
		}
		if n != span.size || hex.EncodeToString(hash.Sum(nil)) != span.sha {
			return nil, errors.New("original file changed while reusing media; retry or download a copy")
		}
	}
	return map[string]any{"success": true, "bytes_written": total}, nil
}
