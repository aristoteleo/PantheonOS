// Package streamrtc carries owned-window media directly to an authenticated
// Atrium viewer. Fleet's existing gateway WebSocket owns signaling and lifetime.
// This subprocess has no listening HTTP port, node credentials or capture API.
package streamrtc

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"sync"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/rtp/codecs"
	"github.com/pion/webrtc/v4"
)

const MaxPacket = 16*1024*1024 + 32
const chunkSize = 12 * 1024

type command struct {
	Op         string                  `json:"op"`
	SDP        string                  `json:"sdp"`
	Windows    []uint32                `json:"windows"`
	Candidate  webrtc.ICECandidateInit `json:"candidate"`
	ICEServers []webrtc.ICEServer      `json:"iceServers"`
}
type video struct {
	track      *webrtc.TrackLocalStaticRTP
	packetizer rtp.Packetizer
	origin     uint64
	timestamp  uint32
	started    bool
}
type peer struct {
	pc     *webrtc.PeerConnection
	frames *webrtc.DataChannel
	videos map[uint32]*video
	mu     sync.Mutex
	seq    uint32
	emit   func(any)
}

func (p *peer) close() {
	if p.pc != nil {
		_ = p.pc.Close()
	}
}
func (p *peer) offer(c command) error {
	if p.pc != nil {
		return errors.New("one offer per media incarnation")
	}
	if len(c.SDP) > 60000 || len(c.Windows) > 16 || len(c.ICEServers) > 8 {
		return errors.New("media offer exceeds limits")
	}
	settings := webrtc.SettingEngine{}
	settings.SetIncludeLoopbackCandidate(true)
	settings.SetICEMulticastDNSMode(ice.MulticastDNSModeQueryOnly)
	settings.SetICETimeouts(5*time.Second, 10*time.Second, 2*time.Second)
	api := webrtc.NewAPI(webrtc.WithSettingEngine(settings))
	pc, err := api.NewPeerConnection(webrtc.Configuration{ICEServers: c.ICEServers})
	if err != nil {
		return err
	}
	p.pc = pc
	p.videos = make(map[uint32]*video)
	pc.OnICECandidate(func(c *webrtc.ICECandidate) {
		if c != nil {
			p.emit(map[string]any{"event": "candidate", "candidate": c.ToJSON()})
		}
	})
	pc.OnConnectionStateChange(func(s webrtc.PeerConnectionState) {
		p.emit(map[string]any{"event": "state", "state": s.String()})
		if s == webrtc.PeerConnectionStateConnected {
			for wid := range p.videos {
				p.emit(map[string]any{"event": "keyframe", "wid": wid})
			}
		}
	})
	pc.OnDataChannel(func(dc *webrtc.DataChannel) {
		if dc.Label() == "frames" {
			p.mu.Lock()
			p.frames = dc
			p.mu.Unlock()
			dc.OnOpen(func() { p.emit(map[string]any{"event": "media-ready"}) })
			return
		}
		if dc.Label() != "control" {
			_ = dc.Close()
			return
		}
		dc.OnOpen(func() { p.emit(map[string]any{"event": "control", "state": "open"}) })
		dc.OnClose(func() { p.emit(map[string]any{"event": "control", "state": "closed"}) })
		dc.OnMessage(func(m webrtc.DataChannelMessage) {
			if !m.IsString || len(m.Data) > 8192 {
				_ = dc.Close()
				return
			}
			var a map[string]any
			if json.Unmarshal(m.Data, &a) != nil {
				_ = dc.Close()
				return
			}
			p.emit(map[string]any{"event": "input", "value": a})
		})
	})
	for _, wid := range c.Windows {
		if wid == 0 || p.videos[wid] != nil {
			return errors.New("invalid video window")
		}
		track, e := webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeH264, ClockRate: 90000,
			SDPFmtpLine: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f"}, fmt.Sprint(wid), fmt.Sprintf("window-%d", wid))
		if e != nil {
			return e
		}
		sender, e := pc.AddTrack(track)
		if e != nil {
			return e
		}
		p.videos[wid] = &video{track: track, packetizer: rtp.NewPacketizer(1200, 0, 0, &codecs.H264Payloader{}, rtp.NewRandomSequencer(), 90000), timestamp: rand.Uint32()}
		go func(wid uint32) {
			for {
				packets, _, e := sender.ReadRTCP()
				if e != nil {
					return
				}
				for _, packet := range packets {
					switch packet.(type) {
					case *rtcp.PictureLossIndication, *rtcp.FullIntraRequest:
						p.emit(map[string]any{"event": "keyframe", "wid": wid})
					}
				}
			}
		}(wid)
	}
	if err = pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: c.SDP}); err != nil {
		return err
	}
	answer, err := pc.CreateAnswer(nil)
	if err != nil {
		return err
	}
	if err = pc.SetLocalDescription(answer); err != nil {
		return err
	}
	p.emit(map[string]any{"event": "answer", "sdp": answer.SDP})
	return nil
}
func (p *peer) frame(tag byte, data []byte) error {
	if len(data) < 5 {
		return errors.New("invalid media frame")
	}
	wid := binary.BigEndian.Uint32(data)
	if tag == 3 {
		if len(data) < 14 {
			return errors.New("invalid H264 frame")
		}
		v := p.videos[wid]
		if v == nil || p.pc.ConnectionState() != webrtc.PeerConnectionStateConnected {
			return nil
		}
		key := data[4] != 0
		stamp := binary.BigEndian.Uint64(data[5:13])
		if !v.started && !key {
			return nil
		}
		if !v.started {
			v.origin = stamp
			v.started = true
		}
		// ScreenCaptureKit may emit nothing for a static window. Preserve the
		// capture clock across idle gaps instead of assigning sequential 60 Hz
		// timestamps, which causes receiver jitter buffering on the next update.
		timestamp := v.timestamp + uint32((stamp-v.origin)*90/1000)
		for _, packet := range v.packetizer.Packetize(data[13:], 0) {
			packet.Timestamp = timestamp
			if err := v.track.WriteRTP(packet); err != nil {
				return err
			}
		}
		return nil
	}
	p.mu.Lock()
	dc := p.frames
	p.mu.Unlock()
	if dc == nil || dc.ReadyState() != webrtc.DataChannelStateOpen || dc.BufferedAmount() > 512*1024 {
		return nil
	}
	jpeg := data[4:]
	p.seq++
	// Unordered, non-retransmitted chunks prevent a lost JPEG from holding up
	// newer frames. Receivers bound both allocation and incomplete-frame lifetime.
	for offset := 0; offset < len(jpeg); offset += chunkSize {
		end := min(offset+chunkSize, len(jpeg))
		b := make([]byte, 16+end-offset)
		binary.BigEndian.PutUint32(b, wid)
		binary.BigEndian.PutUint32(b[4:], p.seq)
		binary.BigEndian.PutUint32(b[8:], uint32(offset))
		binary.BigEndian.PutUint32(b[12:], uint32(len(jpeg)))
		copy(b[16:], jpeg[offset:end])
		if err := dc.Send(b); err != nil {
			return nil
		}
	}
	return nil
}

// Run accepts length-prefixed trusted local packets: tag 1 JSON, 2 JPEG, 3 H264.
// Only the adapter may feed it; remote viewers never select a PID or local path.
func Run(input io.Reader, output io.Writer) error {
	events := make(chan any, 128)
	done := make(chan struct{})
	defer close(done)
	failed := make(chan error, 1)
	go func() {
		encoder := json.NewEncoder(output)
		for {
			select {
			case <-done:
				return
			case e := <-events:
				if err := encoder.Encode(e); err != nil {
					select {
					case failed <- err:
					default:
					}
					return
				}
			}
		}
	}()
	p := &peer{emit: func(e any) {
		select {
		case events <- e:
		case <-done:
		default:
			select {
			case failed <- errors.New("media peer event overflow"):
			default:
			}
		}
	}}
	defer p.close()
	reader := bufio.NewReader(input)
	for {
		select {
		case err := <-failed:
			return err
		default:
		}
		var size uint32
		if err := binary.Read(reader, binary.BigEndian, &size); err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		if size < 2 || size > MaxPacket {
			return errors.New("invalid media packet size")
		}
		packet := make([]byte, size)
		if _, err := io.ReadFull(reader, packet); err != nil {
			return err
		}
		var err error
		switch packet[0] {
		case 1:
			if size > 65536 {
				return errors.New("media command exceeds limit")
			}
			var c command
			err = json.Unmarshal(packet[1:], &c)
			if err == nil {
				switch c.Op {
				case "offer":
					err = p.offer(c)
				case "candidate":
					if p.pc == nil {
						err = errors.New("missing offer")
					} else {
						err = p.pc.AddICECandidate(c.Candidate)
					}
				default:
					err = errors.New("unknown media operation")
				}
			}
		case 2, 3:
			err = p.frame(packet[0], packet[1:])
		default:
			err = errors.New("unknown media packet")
		}
		if err != nil {
			return err
		}
	}
}
