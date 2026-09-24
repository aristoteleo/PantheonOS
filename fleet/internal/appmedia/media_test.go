package appmedia

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/pion/webrtc/v4"
)

func mediaFixture(t *testing.T, variant string) (*Server, Request, []byte, *atomic.Int64, context.CancelFunc) {
	t.Helper()
	data := bytes.Repeat([]byte("model-media-binary\x00\xff"), 120000)
	sum := sha256.Sum256(data)
	q := Request{Binding: apptransport.Binding{Fleet: "fleet", Node: "node", Instance: "instance", Revision: strings.Repeat("a", 64), Generation: 1, Component: "backend", Port: "http"}, Offer: Offer{Artifact: strings.Repeat("b", 32), Config: strings.Repeat("c", 64), SHA256: hex.EncodeToString(sum[:]), Size: int64(len(data)), MIME: "video/mp4"}, Credential: strings.Repeat("credential", 8), Expires: time.Now().Add(time.Minute).Unix()}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/media/artifacts/"+q.Artifact+"/content" || r.Header.Get("X-Pantheon-App-Token") != q.Credential || r.Header.Get("X-Model-Config") != q.Config || r.Header.Get("Cookie") != "" || r.Header.Get("Authorization") != "" {
			t.Error("incorrect artifact authority")
			w.WriteHeader(403)
			return
		}
		if variant == "redirect" {
			http.Redirect(w, r, "http://127.0.0.1:1/foreign", 302)
			return
		}
		var first, last int
		if _, err := fmt.Sscanf(r.Header.Get("Range"), "bytes=%d-%d", &first, &last); err != nil || first < 0 || last >= len(data) || last-first >= chunkSize {
			w.WriteHeader(400)
			return
		}
		body := bytes.Clone(data[first : last+1])
		if variant == "corrupt" {
			body[0] ^= 255
		}
		w.Header().Set("Content-Type", q.MIME)
		w.Header().Set("Content-Length", fmt.Sprint(len(body)))
		w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", first, last, len(data)))
		w.Header().Set("ETag", `"`+q.SHA256+`"`)
		if variant == "identity" {
			w.Header().Set("ETag", `"foreign"`)
		}
		w.WriteHeader(206)
		if variant == "truncated" {
			body = body[:len(body)/2]
		}
		_, _ = w.Write(body)
	}))
	t.Cleanup(upstream.Close)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	uses := new(atomic.Int64)
	s := New(ctx, func(b apptransport.Binding) (string, func(), error) {
		if b != q.Binding {
			return "", nil, errors.New("generation changed")
		}
		uses.Add(1)
		return upstream.URL, func() { uses.Add(-1) }, nil
	}, func() bool { return true })
	t.Cleanup(func() {
		cancel()
		// Node-owned listeners outlive previews. Shutdown must physically close them,
		// including macOS socket closes; a released session alone is insufficient.
		select {
		case <-s.sockets.closed:
		case <-time.After(45 * time.Second):
			t.Error("node media sockets did not shut down")
		}
	})
	return s, q, data, uses, cancel
}

func peerFixture(t *testing.T, s *Server, q Request, label string, start bool) (*webrtc.PeerConnection, <-chan []byte, <-chan []byte) {
	t.Helper()
	settings := webrtc.SettingEngine{}
	settings.SetIncludeLoopbackCandidate(true)
	// The fixture peer stays on loopback; the production node still advertises all interfaces.
	settings.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() })
	client, err := webrtc.NewAPI(webrtc.WithSettingEngine(settings)).NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { client.Close() })
	dc, err := client.CreateDataChannel(label, nil)
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan []byte, 1)
	part := make(chan []byte, 1)
	var all []byte
	dc.OnOpen(func() {
		if start {
			_ = dc.SendText("start")
		}
	})
	dc.OnMessage(func(m webrtc.DataChannelMessage) {
		if m.IsString {
			select {
			case done <- append(append(bytes.Clone(m.Data), '\n'), all...):
			default:
			}
			return
		}
		all = append(all, m.Data...)
		select {
		case part <- []byte{1}:
		default:
		}
	})
	offer, err := client.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	gathered := webrtc.GatheringCompletePromise(client)
	if err = client.SetLocalDescription(offer); err != nil {
		t.Fatal(err)
	}
	select {
	case <-gathered:
	case <-time.After(5 * time.Second):
		t.Fatal("client gathering timeout")
	}
	q.SDP = client.LocalDescription().SDP
	answer, err := s.Offer(context.Background(), q)
	if err != nil {
		t.Fatal(err)
	}
	if answer.Transport != "fleet_browser_direct" || answer.Expires != q.Expires {
		t.Fatal("answer identity")
	}
	if err = client.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: answer.SDP}); err != nil {
		t.Fatal(err)
	}
	dc.OnClose(func() {
		select {
		case done <- []byte(`{"closed":true}`):
		default:
		}
	})
	return client, done, part
}

func awaitResult(t *testing.T, result <-chan []byte) []byte {
	t.Helper()
	select {
	case response := <-result:
		return response
	case <-time.After(5 * time.Second):
		t.Fatal("peer produced neither a result nor failure/close")
	}
	return nil
}

func awaitClean(t *testing.T, s *Server, uses *atomic.Int64) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if len(s.slots) == 0 && uses.Load() == 0 {
			s.sockets.mu.Lock()
			holders := 0
			for g := range s.sockets.live {
				holders += g.users
			}
			s.sockets.mu.Unlock()
			if holders != 0 {
				t.Fatalf("media session retained %d socket leases", holders)
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("media cleanup exceeded 3s: slots=%d uses=%d", len(s.slots), uses.Load())
}

func TestDirectMediaTransfersExactArtifactAndReleasesSession(t *testing.T) {
	s, q, want, uses, cancel := mediaFixture(t, "")
	client, result, _ := peerFixture(t, s, q, Channel, true)
	response := awaitResult(t, result)
	parts := bytes.SplitN(response, []byte{'\n'}, 2)
	if len(parts) != 2 {
		t.Fatal("media completion timeout")
	}
	var end struct {
		Complete bool   `json:"complete"`
		Size     int64  `json:"size"`
		SHA256   string `json:"sha256"`
	}
	if json.Unmarshal(parts[0], &end) != nil || !end.Complete || end.Size != q.Size || end.SHA256 != q.SHA256 || !bytes.Equal(parts[1], want) {
		t.Fatalf("incorrect media completion: %s, bytes=%d", parts[0], len(parts[1]))
	}
	client.Close()
	cancel()
	awaitClean(t, s, uses)
}

func TestDirectMediaRejectsChangedOrIncompleteArtifacts(t *testing.T) {
	for _, kind := range []string{"identity", "redirect", "truncated", "corrupt"} {
		t.Run(kind, func(t *testing.T) {
			s, q, _, uses, cancel := mediaFixture(t, kind)
			_, result, _ := peerFixture(t, s, q, Channel, true)
			response := awaitResult(t, result)
			if !bytes.Contains(response, []byte(`"error":`)) && !bytes.Contains(response, []byte(`"closed":true`)) {
				t.Fatal("unverified media completed")
			}
			cancel()
			awaitClean(t, s, uses)
		})
	}
}

func TestDirectMediaCancellationReleasesLeaseAndCapacity(t *testing.T) {
	s, q, _, uses, cancel := mediaFixture(t, "")
	client, _, part := peerFixture(t, s, q, Channel, true)
	select {
	case <-part:
	case <-time.After(5 * time.Second):
		t.Fatal("no transfer")
	}
	client.Close()
	cancel()
	awaitClean(t, s, uses)
}

func TestDirectMediaRejectsAuthorityAndRelayBeforePeerAllocation(t *testing.T) {
	s, q, _, uses, cancel := mediaFixture(t, "")
	q.SDP = "v=0\r\na=candidate:1 1 udp 1 127.0.0.1 9000 typ host\r\n"
	for _, change := range []func(*Request){
		func(r *Request) { r.Node = "other" }, func(r *Request) { r.Size = MaxSize + 1 },
		func(r *Request) { r.Expires = time.Now().Add(time.Hour).Unix() }, func(r *Request) { r.Credential = "" },
		func(r *Request) { r.Artifact = "../secret" }, func(r *Request) { r.MIME = "text/html" },
		func(r *Request) { r.SDP = strings.ReplaceAll(r.SDP, "typ host", "typ relay") },
		func(r *Request) { r.SDP += "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n" },
		func(r *Request) { r.SDP = strings.ReplaceAll(r.SDP, "typ host", "typ   relay") },
	} {
		copy := q
		change(&copy)
		if _, err := s.Offer(context.Background(), copy); err == nil {
			t.Fatal("invalid media offer accepted")
		}
	}
	cancel()
	awaitClean(t, s, uses)
}

func TestEndpointMustBeOriginalLiteralLoopback(t *testing.T) {
	for _, u := range []string{"http://example.com:80", "http://localhost:8000", "https://127.0.0.1:80", "http://user@127.0.0.1:80", "http://127.0.0.1:80/other", "http://127.0.0.1:80?x=1"} {
		if _, err := endpointURL(u, strings.Repeat("a", 32)); err == nil {
			t.Fatal("unsafe endpoint", u)
		}
	}
}

func TestRepeatedMediaFailuresReuseNodeSockets(t *testing.T) {
	s, q, _, uses, _ := mediaFixture(t, "identity")
	var original *socketGeneration
	for i := 0; i < 20; i++ {
		client, result, _ := peerFixture(t, s, q, Channel, true)
		response := awaitResult(t, result)
		if !bytes.Contains(response, []byte(`"error":`)) && !bytes.Contains(response, []byte(`"closed":true`)) {
			t.Fatal("failed artifact was not rejected")
		}
		awaitClean(t, s, uses)
		client.Close()
		s.sockets.mu.Lock()
		current, count := s.sockets.current, len(s.sockets.live)
		s.sockets.mu.Unlock()
		if original == nil {
			original = current
		}
		if current != original || count != 1 {
			t.Fatal("repeated preview allocated another socket generation")
		}
	}
}
