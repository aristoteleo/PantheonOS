package dataplane

import (
	"bytes"
	"crypto/rand"
	"io"
	"testing"

	"github.com/klauspost/compress/zstd"
)

// encodeBody mirrors the sender's body framing in Send: chunkWriter, optionally
// wrapped in a zstd encoder, terminated by chunkWriter.Close.
func encodeBody(t *testing.T, data []byte, compress string) []byte {
	t.Helper()
	var buf bytes.Buffer
	cw := &chunkWriter{w: &buf}
	var sink io.Writer = cw
	var enc *zstd.Encoder
	if compress == "zstd" {
		var err error
		if enc, err = zstd.NewWriter(cw); err != nil {
			t.Fatal(err)
		}
		sink = enc
	}
	if _, err := io.Copy(sink, bytes.NewReader(data)); err != nil {
		t.Fatal(err)
	}
	if enc != nil {
		if err := enc.Close(); err != nil {
			t.Fatal(err)
		}
	}
	if err := cw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// decodeBody mirrors the receiver's body read in handleIncoming, returning both
// the decoded payload and the bytes left on the reader after the body (which in
// the real protocol is the trailer frame).
func decodeBody(t *testing.T, wire []byte, compress string) (payload, rest []byte) {
	t.Helper()
	r := bytes.NewReader(wire)
	cr := &chunkReader{r: r}
	var src io.Reader = cr
	var dec *zstd.Decoder
	if compress == "zstd" {
		var err error
		if dec, err = zstd.NewReader(cr); err != nil {
			t.Fatal(err)
		}
		src = dec
	}
	out, err := io.ReadAll(src)
	if err != nil {
		t.Fatal(err)
	}
	if dec != nil {
		dec.Close()
	}
	rest, _ = io.ReadAll(r)
	return out, rest
}

func TestChunkedCodecRoundTrip(t *testing.T) {
	// Compressible payload (repeated text).
	data := bytes.Repeat([]byte("pantheon-fleet-0123456789-"), 100_000) // ~2.6 MB
	for _, compress := range []string{"", "zstd"} {
		wire := encodeBody(t, data, compress)
		out, rest := decodeBody(t, wire, compress)
		if !bytes.Equal(out, data) {
			t.Fatalf("compress=%q: round-trip mismatch (got %d bytes, want %d)", compress, len(out), len(data))
		}
		if len(rest) != 0 {
			t.Fatalf("compress=%q: chunkReader over-read; %d trailing bytes leaked past the body", compress, len(rest))
		}
	}
}

func TestZstdShrinksCompressibleWire(t *testing.T) {
	data := bytes.Repeat([]byte("pantheon-fleet-0123456789-"), 100_000)
	raw := encodeBody(t, data, "")
	z := encodeBody(t, data, "zstd")
	if len(z) >= len(raw) {
		t.Fatalf("zstd did not shrink compressible data: raw=%d zstd=%d", len(raw), len(z))
	}
	t.Logf("compressible: raw=%d zstd=%d (%.1fx smaller)", len(raw), len(z), float64(len(raw))/float64(len(z)))
}

func TestZstdRoundTripsIncompressible(t *testing.T) {
	// Random data won't shrink, but must still round-trip correctly.
	data := make([]byte, 1<<20)
	if _, err := rand.Read(data); err != nil {
		t.Fatal(err)
	}
	wire := encodeBody(t, data, "zstd")
	out, rest := decodeBody(t, wire, "zstd")
	if !bytes.Equal(out, data) {
		t.Fatal("zstd round-trip of random data mismatch")
	}
	if len(rest) != 0 {
		t.Fatalf("over-read: %d trailing bytes", len(rest))
	}
}

// TestChunkReaderStopsAtTrailer proves the body/trailer boundary: a trailer
// frame written right after the body must be readable intact afterwards.
func TestChunkReaderStopsAtTrailer(t *testing.T) {
	data := []byte("hello fleet")
	var buf bytes.Buffer
	cw := &chunkWriter{w: &buf}
	_, _ = cw.Write(data)
	_ = cw.Close()
	if err := writeFrame(&buf, trailer{SHA256: "deadbeef"}); err != nil {
		t.Fatal(err)
	}
	r := bytes.NewReader(buf.Bytes())
	cr := &chunkReader{r: r}
	out, _ := io.ReadAll(cr)
	if !bytes.Equal(out, data) {
		t.Fatalf("body mismatch: %q", out)
	}
	var tr trailer
	if err := readFrame(r, &tr); err != nil {
		t.Fatalf("trailer unreadable after body: %v", err)
	}
	if tr.SHA256 != "deadbeef" {
		t.Fatalf("trailer corrupted: %q", tr.SHA256)
	}
}
