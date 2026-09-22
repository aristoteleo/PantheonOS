package appdirect

import (
	"bufio"
	"context"
	"encoding/binary"
	"encoding/json"
	"io"
	"net/http"
	"testing"
	"time"
)

func TestSessionBoundariesAndIdle(t *testing.T) {
	for _, mode := range []string{"idle", "oversized", "outside-stream", "rejected-reuse", "unread-barrier"} {
		t.Run(mode, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			input, writer := io.Pipe()
			reader, output := io.Pipe()
			defer writer.Close()
			defer reader.Close()
			ended := make(chan error, 1)
			go func() { ended <- runSession(ctx, input, output, 100*time.Millisecond) }()
			buffered := bufio.NewReader(reader)
			line, err := buffered.ReadBytes('\n')
			var hello struct {
				Protocol int    `json:"protocol"`
				Peer     string `json:"peer_id"`
			}
			if err != nil || json.Unmarshal(line, &hello) != nil || hello.Protocol != 2 || hello.Peer == "" {
				t.Fatal("invalid session hello")
			}
			switch mode {
			case "oversized":
				var header [5]byte
				header[0] = 'G'
				binary.BigEndian.PutUint32(header[1:], sessionFrameLimit+1)
				_, _ = writer.Write(header[:])
			case "outside-stream":
				_ = writeFrame(writer, 'D', []byte("not authorized"))
			case "rejected-reuse", "unread-barrier":
				for i := 0; i < 2; i++ {
					if writeFrame(writer, 'G', []byte(`{"secret":"must-not-appear-in-output"}`)) != nil {
						t.Fatal("grant failed")
					}
					kind, body, err := readFrame(buffered)
					if err != nil || kind != 'F' || string(body) != "grant_rejected" {
						t.Fatal("incorrect rejection")
					}
					if writeFrame(writer, 'X', nil) != nil {
						t.Fatal("close failed")
					}
					if mode == "unread-barrier" {
						break
					}
					kind, body, err = readFrame(buffered)
					if err != nil || kind != 'C' || len(body) != 0 {
						t.Fatal("missing stream barrier")
					}
				}
			}
			select {
			case <-ended:
			case <-time.After(2 * time.Second):
				t.Fatal("session not reclaimed")
			}
		})
	}
}

func TestSessionCancelUnreadResponseAndReauthorize(t *testing.T) {
	f := setup(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		chunk := make([]byte, sessionFrameLimit)
		for i := 0; i < 64; i++ {
			if _, err := w.Write(chunk); err != nil {
				return
			}
		}
	}))
	ctx, cancel := context.WithCancel(f.ctx)
	defer cancel()
	input, writer := io.Pipe()
	reader, output := io.Pipe()
	defer reader.Close()
	defer writer.Close()
	ended := make(chan error, 1)
	go func() { ended <- RunSession(ctx, input, output) }()
	buffered := bufio.NewReader(reader)
	line, _ := buffered.ReadBytes('\n')
	var hello struct {
		Peer string `json:"peer_id"`
	}
	if json.Unmarshal(line, &hello) != nil {
		t.Fatal("missing peer")
	}
	for i := 0; i < 3; i++ {
		q := f.request()
		q.Peer = hello.Peer
		grant, err := f.server.Issue(q)
		if err != nil {
			t.Fatal(err)
		}
		encoded, _ := json.Marshal(grant)
		if writeFrame(writer, 'G', encoded) != nil {
			t.Fatal("grant write failed")
		}
		kind, _, err := readFrame(buffered)
		if err != nil || kind != 'R' {
			t.Fatalf("not ready: %c %v", kind, err)
		}
		if writeFrame(writer, 'D', []byte("GET / HTTP/1.1\r\nHost: fleet-app.invalid\r\n\r\n")) != nil {
			t.Fatal("HTTP write failed")
		}
		read := 0
		for read < sessionWindow {
			kind, body, err := readFrame(buffered)
			if err != nil {
				t.Fatal(err)
			}
			if kind == 'D' {
				read += len(body)
			} else if kind != 'W' {
				t.Fatalf("unexpected %c", kind)
			}
		}
		if read != sessionWindow {
			t.Fatal("response exceeded credit")
		}
		// No read credit is returned. X must still be processed and C must
		// arrive only once both old pumps are done, permitting a fresh grant.
		if writeFrame(writer, 'X', nil) != nil {
			t.Fatal("cancel blocked")
		}
		for {
			kind, _, err = readFrame(buffered)
			if err != nil {
				t.Fatal(err)
			}
			if kind == 'C' {
				break
			}
			if kind != 'E' && kind != 'W' {
				t.Fatalf("unexpected output after cancel: %c", kind)
			}
		}
	}
	_ = writer.Close()
	select {
	case <-ended:
	case <-time.After(3 * time.Second):
		t.Fatal("session leaked after parent EOF")
	}
}
