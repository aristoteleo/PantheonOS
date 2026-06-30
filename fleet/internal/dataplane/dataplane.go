// Package dataplane is the Pantheon-Fleet data plane: a go-libp2p host that
// moves bulk data directly between Nodes over QUIC, with NAT traversal
// (DCUtR hole-punching) and a Circuit Relay v2 fallback — no external VPN.
//
// Control/discovery still rides NATS: a Node advertises its multiaddrs into the
// Registry; a peer reads them and connects here. The transfer stream protocol
// is chunked and sha256-verified.
package dataplane

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/protocol"
	"github.com/multiformats/go-multiaddr"
)

// TransferProto is the libp2p stream protocol for a file Transfer.
const TransferProto protocol.ID = "/pantheon-fleet/transfer/1.0.0"

const maxFrame = 1 << 20 // 1 MiB cap on a JSON frame

// Plane is a running libp2p host.
type Plane struct {
	host host.Host
}

type header struct {
	DstPath string `json:"dst_path"`
	Size    int64  `json:"size"`
}
type trailer struct {
	SHA256 string `json:"sha256"`
}
type ack struct {
	OK    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
}

// New starts a libp2p host listening on the given QUIC port (0 = random).
// relayAddrs (if any) are the Fleet's relays used for AutoRelay + hole punching.
func New(ctx context.Context, relayAddrs []string, port int, forceRelay bool) (*Plane, error) {
	opts := []libp2p.Option{
		libp2p.ListenAddrStrings(
			fmt.Sprintf("/ip4/0.0.0.0/udp/%d/quic-v1", port),
			fmt.Sprintf("/ip6/::/udp/%d/quic-v1", port),
		),
		libp2p.EnableHolePunching(),
		libp2p.NATPortMap(),
	}
	if relays := parseRelays(relayAddrs); len(relays) > 0 {
		opts = append(opts, libp2p.EnableAutoRelayWithStaticRelays(relays))
	}
	// A Node that knows it's behind a strict NAT can force itself "private" so it
	// reserves a relay slot up front instead of waiting on AutoNAT detection.
	if forceRelay {
		opts = append(opts, libp2p.ForceReachabilityPrivate())
	}
	h, err := libp2p.New(opts...)
	if err != nil {
		return nil, err
	}
	p := &Plane{host: h}
	h.SetStreamHandler(TransferProto, p.handleIncoming)
	return p, nil
}

// ID is the libp2p peer id.
func (p *Plane) ID() string { return p.host.ID().String() }

// Multiaddrs returns the full /p2p addresses to advertise in the Registry.
func (p *Plane) Multiaddrs() []string {
	var out []string
	pid := "/p2p/" + p.host.ID().String()
	for _, a := range p.host.Addrs() {
		out = append(out, a.String()+pid)
	}
	return out
}

// Reachability is a coarse hint for the Registry: if the host has acquired a
// relayed (circuit) address it's behind NAT and reachable via a relay.
func (p *Plane) Reachability() string {
	for _, a := range p.host.Addrs() {
		if strings.Contains(a.String(), "p2p-circuit") {
			return "relay"
		}
	}
	return "direct"
}

// Close shuts the host down.
func (p *Plane) Close() error { return p.host.Close() }

// Send connects to dst (by its advertised multiaddrs) and streams srcPath; the
// receiver writes it to dstPath. onProgress is called periodically with bytes
// done / total. Returns the verified sha256 and whether the connection went
// through a relay (vs a direct connection).
func (p *Plane) Send(ctx context.Context, dstAddrs []string, srcPath, dstPath string, onProgress func(done, total int64)) (sum string, viaRelay bool, err error) {
	ai, err := addrInfo(dstAddrs)
	if err != nil {
		return "", false, err
	}
	if err := p.host.Connect(ctx, ai); err != nil {
		return "", false, fmt.Errorf("connect %s: %w", ai.ID, err)
	}
	// A relay (circuit) connection is a *limited* connection; libp2p refuses to
	// open a stream over it unless we explicitly allow it. Without this the relay
	// fallback can't carry a Transfer at all — it would silently work only when
	// DCUtR manages to upgrade to a direct connection, defeating the relay's
	// whole purpose for peers that genuinely can't hole-punch.
	sctx := network.WithAllowLimitedConn(ctx, "pantheon-fleet-transfer")
	s, err := p.host.NewStream(sctx, ai.ID, TransferProto)
	if err != nil {
		return "", false, fmt.Errorf("open stream: %w", err)
	}
	defer s.Close()
	viaRelay = strings.Contains(s.Conn().RemoteMultiaddr().String(), "p2p-circuit")

	f, err := os.Open(srcPath)
	if err != nil {
		return "", false, err
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil {
		return "", false, err
	}
	total := fi.Size()

	if err := writeFrame(s, header{DstPath: dstPath, Size: total}); err != nil {
		return "", false, err
	}

	hasher := sha256.New()
	pw := &progressWriter{w: s, total: total, cb: onProgress, last: time.Now()}
	if _, err := io.CopyBuffer(io.MultiWriter(pw, hasher), io.LimitReader(f, total), make([]byte, 256<<10)); err != nil {
		return "", false, err
	}
	if onProgress != nil {
		onProgress(total, total)
	}
	sum = hex.EncodeToString(hasher.Sum(nil))
	if err := writeFrame(s, trailer{SHA256: sum}); err != nil {
		return "", false, err
	}

	var a ack
	if err := readFrame(s, &a); err != nil {
		return "", false, err
	}
	if !a.OK {
		return "", false, fmt.Errorf("receiver rejected transfer: %s", a.Error)
	}
	return sum, viaRelay, nil
}

// handleIncoming is the receiver side of a Transfer.
func (p *Plane) handleIncoming(s network.Stream) {
	defer s.Close()
	var h header
	if err := readFrame(s, &h); err != nil {
		_ = writeFrame(s, ack{Error: "bad header: " + err.Error()})
		return
	}
	if err := os.MkdirAll(filepath.Dir(h.DstPath), 0o755); err != nil {
		_ = writeFrame(s, ack{Error: err.Error()})
		return
	}
	f, err := os.Create(h.DstPath)
	if err != nil {
		_ = writeFrame(s, ack{Error: err.Error()})
		return
	}
	hasher := sha256.New()
	if _, err := io.CopyN(io.MultiWriter(f, hasher), s, h.Size); err != nil {
		f.Close()
		_ = writeFrame(s, ack{Error: "copy: " + err.Error()})
		return
	}
	if err := f.Close(); err != nil {
		_ = writeFrame(s, ack{Error: err.Error()})
		return
	}
	var tr trailer
	if err := readFrame(s, &tr); err != nil {
		_ = writeFrame(s, ack{Error: "bad trailer: " + err.Error()})
		return
	}
	got := hex.EncodeToString(hasher.Sum(nil))
	if got != tr.SHA256 {
		_ = writeFrame(s, ack{Error: "sha256 mismatch"})
		return
	}
	_ = writeFrame(s, ack{OK: true})
}

// --- helpers ---

func addrInfo(addrs []string) (peer.AddrInfo, error) {
	var info *peer.AddrInfo
	for _, s := range addrs {
		ma, err := multiaddr.NewMultiaddr(s)
		if err != nil {
			continue
		}
		ai, err := peer.AddrInfoFromP2pAddr(ma)
		if err != nil {
			continue
		}
		if info == nil {
			info = ai
		} else if info.ID == ai.ID {
			info.Addrs = append(info.Addrs, ai.Addrs...)
		}
	}
	if info == nil {
		return peer.AddrInfo{}, errors.New("no usable multiaddrs for peer")
	}
	return *info, nil
}

func parseRelays(addrs []string) []peer.AddrInfo {
	var out []peer.AddrInfo
	for _, s := range addrs {
		ma, err := multiaddr.NewMultiaddr(s)
		if err != nil {
			continue
		}
		if ai, err := peer.AddrInfoFromP2pAddr(ma); err == nil {
			out = append(out, *ai)
		}
	}
	return out
}

func writeFrame(w io.Writer, v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	if len(b) > maxFrame {
		return errors.New("frame too large")
	}
	var lp [4]byte
	binary.BigEndian.PutUint32(lp[:], uint32(len(b)))
	if _, err := w.Write(lp[:]); err != nil {
		return err
	}
	_, err = w.Write(b)
	return err
}

func readFrame(r io.Reader, v any) error {
	var lp [4]byte
	if _, err := io.ReadFull(r, lp[:]); err != nil {
		return err
	}
	n := binary.BigEndian.Uint32(lp[:])
	if n > maxFrame {
		return errors.New("frame too large")
	}
	b := make([]byte, n)
	if _, err := io.ReadFull(r, b); err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}

// progressWriter throttles onProgress callbacks to ~3/s.
type progressWriter struct {
	w     io.Writer
	total int64
	done  int64
	cb    func(done, total int64)
	last  time.Time
}

func (p *progressWriter) Write(b []byte) (int, error) {
	n, err := p.w.Write(b)
	p.done += int64(n)
	if p.cb != nil && time.Since(p.last) > 300*time.Millisecond {
		p.last = time.Now()
		p.cb(p.done, p.total)
	}
	return n, err
}
