package streamrtc

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
	"testing"
	"time"

	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

func TestPacketBounds(t *testing.T) {
	for _, size := range []uint32{0, 1, MaxPacket + 1} {
		var in bytes.Buffer
		_ = binary.Write(&in, binary.BigEndian, size)
		if Run(&in, io.Discard) == nil {
			t.Fatalf("accepted length %d", size)
		}
	}
}
func TestDirectPeer(t *testing.T) {
	client, err := webrtc.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	videoPackets := make(chan *rtp.Packet, 4)
	client.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		for {
			packet, _, err := track.ReadRTP()
			if err != nil {
				return
			}
			videoPackets <- packet
		}
	})
	control, err := client.CreateDataChannel("control", nil)
	if err != nil {
		t.Fatal(err)
	}
	ordered := false
	retries := uint16(0)
	frames, err := client.CreateDataChannel("frames", &webrtc.DataChannelInit{Ordered: &ordered, MaxRetransmits: &retries})
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly})
	if err != nil {
		t.Fatal(err)
	}
	events := make(chan map[string]any, 128)
	server := &peer{emit: func(value any) { events <- value.(map[string]any) }}
	defer server.close()
	gathered := webrtc.GatheringCompletePromise(client)
	offer, err := client.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = client.SetLocalDescription(offer); err != nil {
		t.Fatal(err)
	}
	<-gathered
	if err = server.offer(command{SDP: client.LocalDescription().SDP, Windows: []uint32{7}}); err != nil {
		t.Fatal(err)
	}
	candidates := []webrtc.ICECandidateInit{}
	answer := false
	ready := make(chan struct{}, 1)
	got := make(chan []byte, 4)
	frames.OnOpen(func() { ready <- struct{}{} })
	frames.OnMessage(func(m webrtc.DataChannelMessage) { got <- m.Data })
	deadline := time.After(10 * time.Second)
	for !answer {
		select {
		case event := <-events:
			switch event["event"] {
			case "candidate":
				candidates = append(candidates, event["candidate"].(webrtc.ICECandidateInit))
			case "answer":
				if err = client.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: event["sdp"].(string)}); err != nil {
					t.Fatal(err)
				}
				for _, c := range candidates {
					if err = client.AddICECandidate(c); err != nil {
						t.Fatal(err)
					}
				}
				answer = true
			}
		case <-deadline:
			t.Fatal("answer timeout")
		}
	}
	connected := false
	for !connected {
		select {
		case <-ready:
			connected = true
		case event := <-events:
			if event["event"] == "candidate" {
				if err = client.AddICECandidate(event["candidate"].(webrtc.ICECandidateInit)); err != nil {
					t.Fatal(err)
				}
			}
		case <-deadline:
			t.Fatal("direct ICE timeout")
		}
	}
	input := map[string]any{"op": "input", "wid": 7, "kind": "text", "text": "fixture"}
	raw, _ := json.Marshal(input)
	if err = control.SendText(string(raw)); err != nil {
		t.Fatal(err)
	}
	sawInput := false
	for !sawInput {
		select {
		case event := <-events:
			if event["event"] == "input" {
				if event["value"].(map[string]any)["text"] != "fixture" {
					t.Fatal(event)
				}
				sawInput = true
			}
		case <-deadline:
			t.Fatal("input timeout")
		}
	}
	data := make([]byte, 4+14000)
	binary.BigEndian.PutUint32(data, 7)
	if err = server.frame(2, data); err != nil {
		t.Fatal(err)
	}
	total := 0
	for total < 14000 {
		select {
		case chunk := <-got:
			if binary.BigEndian.Uint32(chunk) != 7 || binary.BigEndian.Uint32(chunk[12:]) != 14000 {
				t.Fatal("bad frame header")
			}
			total += len(chunk) - 16
		case <-deadline:
			t.Fatal("JPEG chunk timeout")
		}
	}
	// A static native window can idle for seconds between frames. The RTP
	// timestamp must advance by that gap, not by an assumed fixed frame rate.
	for _, stamp := range []uint64{10_000_000, 15_000_000} {
		frame := make([]byte, 13)
		binary.BigEndian.PutUint32(frame, 7)
		frame[4] = 1
		binary.BigEndian.PutUint64(frame[5:], stamp)
		frame = append(frame, 0, 0, 0, 1, 0x65, 0x88, 0x84)
		if err = server.frame(3, frame); err != nil {
			t.Fatal(err)
		}
	}
	stamps := []uint32{}
	for len(stamps) < 2 {
		select {
		case packet := <-videoPackets:
			stamps = append(stamps, packet.Timestamp)
		case <-deadline:
			t.Fatal("H264 RTP timeout")
		}
	}
	if stamps[1]-stamps[0] != 450000 {
		t.Fatalf("capture clock lost: %v", stamps)
	}
	if err = server.offer(command{SDP: offer.SDP}); err == nil {
		t.Fatal("accepted replacement offer into old incarnation")
	}
}
