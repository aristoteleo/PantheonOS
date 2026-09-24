// Package appmedia sends one immutable model artifact to an authenticated
// browser over WebRTC. Fleet's control plane carries only the offer/answer;
// neither media bytes nor workload credentials are returned to the gateway.
package appmedia

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/webrtc/v4"
)

const MaxSize = 64 * 1024 * 1024
const MaxLifetime = 180 * time.Second
const Channel = "fleet-model-media-v1"
const chunkSize = 1024 * 1024

var digestRE = regexp.MustCompile(`^[a-f0-9]{64}$`)
var artifactRE = regexp.MustCompile(`^[a-f0-9]{32}$`)

// Offer is supplied by the browser. It cannot choose a destination, binding,
// credential, ICE server, lifetime, HTTP method or arbitrary path.
type Offer struct {
	SDP      string `json:"sdp"`
	Artifact string `json:"artifact_id"`
	Config   string `json:"config_revision"`
	SHA256   string `json:"sha256"`
	Size     int64  `json:"size"`
	MIME     string `json:"mime"`
}

func (q Offer) Valid() bool {
	if len(q.SDP) < 16 || len(q.SDP) > 60000 || !artifactRE.MatchString(q.Artifact) || !digestRE.MatchString(q.Config) || !digestRE.MatchString(q.SHA256) || q.Size <= 0 || q.Size > MaxSize {
		return false
	}
	switch q.MIME {
	case "audio/wav", "audio/mpeg", "image/png", "video/mp4":
		return true
	}
	return false
}

// Request is constructed only by the authenticated App gateway, never trusted
// from browser JSON. Credential is the existing instance-scoped App credential.
type Request struct {
	apptransport.Binding
	Offer
	Credential string `json:"credential"`
	Expires    int64  `json:"expires"`
}

type Answer struct {
	SDP       string `json:"sdp"`
	Transport string `json:"transport"`
	Expires   int64  `json:"expires"`
}

type Acquire func(apptransport.Binding) (string, func(), error)
type Server struct {
	ctx       context.Context
	stop      context.CancelFunc
	acquire   Acquire
	available func() bool
	slots     chan struct{}
	sockets   *socketPool
}

func New(ctx context.Context, acquire Acquire, available func() bool) *Server {
	ctx, stop := context.WithCancel(ctx)
	return &Server{ctx: ctx, stop: stop, acquire: acquire, available: available, slots: make(chan struct{}, 16), sockets: newSocketPool(ctx)}
}

// Shutdown stops accepting media offers and waits for the node's owned sockets.
// It never reports completion while physical socket cleanup is still pending.
func (s *Server) Shutdown(ctx context.Context) error {
	s.stop()
	select {
	case <-s.sockets.closed:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// A full gathered offer/answer avoids a second, reusable signaling authority.
// Host candidates (including loopback and LAN) are supported. No TURN server or
// relay candidate is accepted; networks without direct reachability fail closed.
func (s *Server) Offer(ctx context.Context, q Request) (answer Answer, err error) {
	now := time.Now()
	if !q.Offer.Valid() || !q.Binding.Valid() || len(q.Credential) < 32 || len(q.Credential) > 8192 || q.Expires <= now.Unix() || q.Expires > now.Add(MaxLifetime).Unix() || !s.available() || s.ctx.Err() != nil {
		return answer, errors.New("invalid or unavailable browser media offer")
	}
	for _, line := range strings.Split(q.SDP, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "a=candidate:") {
			candidate, parseErr := ice.UnmarshalCandidate(strings.TrimPrefix(line, "a="))
			if parseErr != nil || candidate.Type() == ice.CandidateTypeRelay {
				return answer, errors.New("invalid or relayed direct media candidate")
			}
		}
		if strings.HasPrefix(line, "m=") && !strings.HasPrefix(line, "m=application ") {
			return answer, errors.New("model artifacts require a data-only peer")
		}
	}
	_, release, err := s.acquire(q.Binding)
	if err != nil {
		return answer, errors.New("App generation unavailable")
	}
	release()
	select {
	case s.slots <- struct{}{}:
	default:
		return answer, errors.New("browser media capacity reached")
	}
	mux, releaseSockets, err := s.sockets.acquire()
	if err != nil {
		<-s.slots
		return answer, err
	}
	settings := webrtc.SettingEngine{}
	settings.SetICEUDPMux(mux)
	settings.SetIncludeLoopbackCandidate(true)
	settings.SetICEMulticastDNSMode(ice.MulticastDNSModeQueryOnly)
	settings.SetICETimeouts(5*time.Second, 10*time.Second, 2*time.Second)
	pc, err := webrtc.NewAPI(webrtc.WithSettingEngine(settings), webrtc.WithMediaEngine(&webrtc.MediaEngine{}), webrtc.WithInterceptorRegistry(&interceptor.Registry{})).NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		releaseSockets()
		<-s.slots
		return answer, err
	}
	life, cancel := context.WithDeadline(s.ctx, time.Unix(q.Expires, 0))
	go func() {
		defer func() { _ = pc.Close(); releaseSockets(); <-s.slots }()
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		defer cancel()
		for {
			select {
			case <-life.Done():
				return
			case <-ticker.C:
				if !s.available() || (time.Since(now) > 12*time.Second && pc.ConnectionState() != webrtc.PeerConnectionStateConnected) {
					return
				}
				_, release, err := s.acquire(q.Binding)
				if err != nil {
					return
				}
				release()
			}
		}
	}()
	defer func() {
		if err != nil {
			cancel()
		}
	}()
	pc.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		if state == webrtc.PeerConnectionStateFailed || state == webrtc.PeerConnectionStateClosed || state == webrtc.PeerConnectionStateDisconnected {
			cancel()
		}
	})
	var channelTaken, started atomic.Bool
	pc.OnDataChannel(func(dc *webrtc.DataChannel) {
		if dc.Label() != Channel || !dc.Ordered() || dc.MaxRetransmits() != nil || dc.MaxPacketLifeTime() != nil || !channelTaken.CompareAndSwap(false, true) {
			cancel()
			return
		}
		dc.OnClose(cancel)
		dc.OnMessage(func(message webrtc.DataChannelMessage) {
			if !message.IsString || string(message.Data) != "start" || !started.CompareAndSwap(false, true) {
				cancel()
				return
			}
			go func() {
				if err := s.send(life, q, dc); err != nil {
					_ = dc.SendText(`{"error":"Direct media transfer failed; retry the original service."}`)
					cancel()
				}
			}()
		})
	})
	if err = pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: q.SDP}); err != nil {
		return answer, err
	}
	var local webrtc.SessionDescription
	if local, err = pc.CreateAnswer(nil); err != nil {
		return answer, err
	}
	gathered := webrtc.GatheringCompletePromise(pc)
	if err = pc.SetLocalDescription(local); err != nil {
		return answer, err
	}
	select {
	case <-gathered:
	case <-ctx.Done():
		return answer, ctx.Err()
	case <-life.Done():
		return answer, life.Err()
	case <-time.After(5 * time.Second):
		return answer, errors.New("direct media ICE gathering timed out")
	}
	return Answer{SDP: pc.LocalDescription().SDP, Expires: q.Expires, Transport: "fleet_browser_direct"}, nil
}

func endpointURL(endpoint, artifact string) (string, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "http" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || u.Port() == "" {
		return "", errors.New("invalid model endpoint")
	}
	ip := net.ParseIP(u.Hostname())
	if ip == nil || !ip.IsLoopback() {
		return "", errors.New("model endpoint must be literal loopback")
	}
	u.Path = "/media/artifacts/" + artifact + "/content"
	return u.String(), nil
}

func (s *Server) send(ctx context.Context, q Request, dc *webrtc.DataChannel) error {
	endpoint, release, err := s.acquire(q.Binding)
	if err != nil {
		return err
	}
	defer release()
	target, err := endpointURL(endpoint, q.Artifact)
	if err != nil {
		return err
	}
	transport := &http.Transport{Proxy: nil, DisableKeepAlives: true, ResponseHeaderTimeout: 15 * time.Second}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	ready := make(chan struct{}, 1)
	dc.SetBufferedAmountLowThreshold(128 * 1024)
	dc.OnBufferedAmountLow(func() {
		select {
		case ready <- struct{}{}:
		default:
		}
	})
	send := func(data []byte) error {
		for dc.BufferedAmount() > 256*1024 {
			select {
			case <-ready:
			case <-ctx.Done():
				return ctx.Err()
			}
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		return dc.Send(data)
	}
	hash := sha256.New()
	buf := make([]byte, 16*1024)
	for offset := int64(0); offset < q.Size; {
		end := min(offset+chunkSize, q.Size) - 1
		r, err := http.NewRequestWithContext(ctx, "GET", target, nil)
		if err != nil {
			return err
		}
		r.Header.Set("X-Pantheon-App-Token", q.Credential)
		r.Header.Set("X-Model-Config", q.Config)
		r.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", offset, end))
		r.Header.Set("Accept-Encoding", "identity")
		res, err := client.Do(r)
		if err != nil {
			return err
		}
		valid := res.StatusCode == 206 && res.Header.Get("Content-Range") == fmt.Sprintf("bytes %d-%d/%d", offset, end, q.Size) && res.Header.Get("ETag") == `"`+q.SHA256+`"` && res.Header.Get("Content-Type") == q.MIME && res.Header.Get("Content-Length") == strconv.FormatInt(end-offset+1, 10) && (res.Header.Get("Content-Encoding") == "" || res.Header.Get("Content-Encoding") == "identity")
		if !valid {
			res.Body.Close()
			return errors.New("model artifact identity changed")
		}
		err = func() error {
			defer res.Body.Close()
			remaining := end - offset + 1
			for remaining > 0 {
				n, err := io.ReadFull(res.Body, buf[:min(int64(len(buf)), remaining)])
				if err != nil {
					return err
				}
				hash.Write(buf[:n])
				if err = send(buf[:n]); err != nil {
					return err
				}
				remaining -= int64(n)
			}
			var extra [1]byte
			if n, err := res.Body.Read(extra[:]); n != 0 || err != io.EOF {
				return errors.New("invalid artifact range boundary")
			}
			return nil
		}()
		if err != nil {
			return err
		}
		offset = end + 1
	}
	if hex.EncodeToString(hash.Sum(nil)) != q.SHA256 {
		return errors.New("artifact checksum changed")
	}
	complete, _ := json.Marshal(map[string]any{"complete": true, "size": q.Size, "sha256": q.SHA256})
	return dc.SendText(string(complete))
}
