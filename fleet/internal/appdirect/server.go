// Package appdirect serves workload HTTP over authenticated libp2p QUIC.
// Grants arrive only over Fleet's authenticated control plane. The wire carries
// an opaque single-use token, never an arbitrary destination or port.
package appdirect

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/multiformats/go-multiaddr"
)

const MaxLifetime = 5 * time.Minute

// Invalid or rejected authority is not an availability failure. A caller must
// not turn either into permission to switch to another transport.
var ErrInvalidGrant = errors.New("invalid direct App grant")
var ErrGrantRejected = errors.New("direct App grant rejected")

type Request struct {
	apptransport.Binding
	Peer       string `json:"peer_id"`
	Credential string `json:"credential"`
	Expires    int64  `json:"expires"`
}

type Grant struct {
	Peer      string   `json:"peer_id"`
	Addresses []string `json:"addresses"`
	Token     string   `json:"access_token"`
	Expires   int64    `json:"expires"`
	Transport string   `json:"transport"`
}

// Acquire validates the exact live generation and holds its lifecycle use lease
// for one HTTP request. It must never resolve a different/replacement instance.
type Acquire func(apptransport.Binding) (endpoint string, release func(), err error)

type Server struct {
	ctx       context.Context
	plane     *dataplane.Plane
	acquire   Acquire
	available func() bool
	mu        sync.Mutex
	grants    map[[32]byte]Request
	slots     chan struct{}
}

func New(ctx context.Context, plane *dataplane.Plane, acquire Acquire, available func() bool) *Server {
	s := &Server{ctx: ctx, plane: plane, acquire: acquire, available: available, grants: make(map[[32]byte]Request), slots: make(chan struct{}, 64)}
	plane.HandleApp(s.serve)
	return s
}

func (s *Server) online() bool { return s.ctx.Err() == nil && s.available() }

func (s *Server) Issue(q Request) (Grant, error) {
	now := time.Now()
	if _, err := peer.Decode(q.Peer); err != nil || !q.Valid() || len(q.Credential) < 32 || len(q.Credential) > 8192 || q.Expires <= now.Unix() || q.Expires > now.Add(MaxLifetime).Unix() || !s.online() {
		return Grant{}, fmt.Errorf("invalid or unavailable direct App grant")
	}
	_, release, err := s.acquire(q.Binding)
	if err != nil {
		return Grant{}, fmt.Errorf("App generation is unavailable")
	}
	release()
	var secret [32]byte
	if _, err := rand.Read(secret[:]); err != nil {
		return Grant{}, err
	}
	token := hex.EncodeToString(secret[:])
	s.mu.Lock()
	defer s.mu.Unlock()
	for key, old := range s.grants {
		if old.Expires <= now.Unix() {
			delete(s.grants, key)
		}
	}
	if len(s.grants) >= 1024 {
		return Grant{}, fmt.Errorf("direct App grant capacity reached")
	}
	s.grants[sha256.Sum256([]byte(token))] = q
	return Grant{Peer: s.plane.ID(), Addresses: s.plane.Multiaddrs(), Token: token, Expires: q.Expires, Transport: "fleet_direct"}, nil
}

func (s *Server) consume(token, remotePeer string) (Request, bool) {
	key := sha256.Sum256([]byte(token))
	s.mu.Lock()
	defer s.mu.Unlock()
	q, ok := s.grants[key]
	if !ok {
		return Request{}, false
	}
	if q.Expires <= time.Now().Unix() {
		delete(s.grants, key)
		return Request{}, false
	}
	// Knowing a token is insufficient without the caller's QUIC private key.
	if q.Peer != remotePeer || !s.online() {
		return Request{}, false
	}
	delete(s.grants, key)
	return q, true
}

func (s *Server) serve(stream network.Stream) {
	if stream.Conn().Stat().Limited || strings.Contains(stream.Conn().RemoteMultiaddr().String(), "/p2p-circuit") {
		_ = stream.Reset()
		return
	}
	select {
	case s.slots <- struct{}{}:
	default:
		_ = stream.Reset()
		return
	}
	defer func() { <-s.slots }()
	defer stream.Close()
	_ = stream.SetDeadline(time.Now().Add(5 * time.Second))
	var token [64]byte
	if _, err := io.ReadFull(stream, token[:]); err != nil {
		return
	}
	q, ok := s.consume(string(token[:]), stream.Conn().RemotePeer().String())
	if !ok {
		_, _ = stream.Write([]byte{0})
		return
	}
	// Revalidate after the grant exchange, before acknowledging the connection.
	_, release, err := s.acquire(q.Binding)
	if err != nil {
		_, _ = stream.Write([]byte{0})
		return
	}
	release()
	if _, err := stream.Write([]byte{1}); err != nil {
		return
	}
	_ = stream.SetDeadline(time.Unix(q.Expires, 0))
	ctx, cancel := context.WithDeadline(s.ctx, time.Unix(q.Expires, 0))
	defer cancel()
	conn := newConn(stream)
	listener := &oneListener{conn: conn}
	server := &http.Server{Handler: s.handler(q), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 15 * time.Second, MaxHeaderBytes: 32 << 10,
		BaseContext: func(net.Listener) context.Context { return ctx }}
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		defer server.Close()
		defer listener.Close()
		for {
			select {
			case <-ctx.Done():
				return
			case <-conn.done:
				return
			case <-ticker.C:
				if !s.online() {
					return
				}
			}
		}
	}()
	_ = server.Serve(listener)
	cancel()
	<-done
}

func (s *Server) handler(q Request) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		// This is a workload protocol, not a browser endpoint or CONNECT proxy.
		if r.Method == "CONNECT" || r.URL.IsAbs() || r.Header.Get("Origin") != "" || r.Header.Get("Sec-Fetch-Site") != "" || r.Header.Get("Upgrade") != "" {
			http.Error(w, "workload HTTP required", 403)
			return
		}
		if q.Expires <= time.Now().Unix() || !s.online() {
			http.Error(w, "App grant expired or node disconnected", 401)
			return
		}
		endpoint, release, err := s.acquire(q.Binding)
		if err != nil {
			http.Error(w, "App generation unavailable", 409)
			return
		}
		defer release()
		u, err := url.Parse(endpoint)
		if err != nil || u.Scheme != "http" || u.User != nil || u.Host == "" {
			http.Error(w, "invalid App endpoint", 502)
			return
		}
		// No inherited proxy environment, keepalive replay or redirects. Host and
		// identity headers come from the grant, never from caller-supplied values.
		transport := &http.Transport{DisableKeepAlives: true, ResponseHeaderTimeout: 120 * time.Second,
			DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext}
		defer transport.CloseIdleConnections()
		proxy := &httputil.ReverseProxy{Transport: transport, FlushInterval: -1,
			Rewrite: func(p *httputil.ProxyRequest) {
				p.SetURL(u)
				p.Out.Host = u.Host
				for key := range p.Out.Header {
					k := strings.ToLower(key)
					if k == "authorization" || k == "cookie" || k == "forwarded" || strings.HasPrefix(k, "x-forwarded-") || strings.HasPrefix(k, "x-fleet-") || strings.HasPrefix(k, "x-pantheon-") {
						p.Out.Header.Del(key)
					}
				}
				p.Out.Header.Set("X-Pantheon-App-Token", q.Credential)
			},
			ModifyResponse: func(res *http.Response) error {
				res.Header.Del("Set-Cookie")
				res.Header.Del("Access-Control-Allow-Origin")
				res.Header.Del("Access-Control-Allow-Credentials")
				return nil
			},
			ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
				http.Error(w, "App connection unavailable", 502)
			},
		}
		proxy.ServeHTTP(w, r)
	})
}

// Dial completes authorization before returning a usable HTTP connection. Its
// context owns the connection lifetime, so cancellation releases both peers.
func Dial(ctx context.Context, plane *dataplane.Plane, g Grant) (net.Conn, error) {
	if g.Transport != "fleet_direct" || len(g.Token) != 64 || g.Expires <= time.Now().Unix() || g.Expires > time.Now().Add(MaxLifetime).Unix() {
		return nil, ErrInvalidGrant
	}
	if _, err := hex.DecodeString(g.Token); err != nil {
		return nil, ErrInvalidGrant
	}
	if _, err := peer.Decode(g.Peer); err != nil {
		return nil, ErrInvalidGrant
	}
	if len(g.Addresses) == 0 || len(g.Addresses) > 32 {
		return nil, ErrInvalidGrant
	}
	for _, address := range g.Addresses {
		if len(address) > 1024 || !strings.HasSuffix(address, "/p2p/"+g.Peer) {
			return nil, ErrInvalidGrant
		}
		ma, err := multiaddr.NewMultiaddr(address)
		if err != nil {
			return nil, ErrInvalidGrant
		}
		if info, err := peer.AddrInfoFromP2pAddr(ma); err != nil || info.ID.String() != g.Peer {
			return nil, ErrInvalidGrant
		}
	}
	stream, err := plane.OpenAppStream(ctx, g.Addresses)
	if err != nil {
		return nil, err
	}
	conn := newConn(stream)
	go func() {
		timer := time.NewTimer(time.Until(time.Unix(g.Expires, 0)))
		defer timer.Stop()
		select {
		case <-ctx.Done():
			_ = conn.Close()
		case <-timer.C:
			_ = conn.Close()
		case <-conn.done:
		}
	}()
	deadline := time.Now().Add(5 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	_ = conn.SetDeadline(deadline)
	if _, err = io.WriteString(conn, g.Token); err == nil {
		var ack [1]byte
		_, err = io.ReadFull(conn, ack[:])
		if err == nil && ack[0] != 1 {
			err = ErrGrantRejected
		}
	}
	if err != nil {
		_ = conn.Close()
		return nil, err
	}
	_ = conn.SetDeadline(time.Unix(g.Expires, 0))
	return conn, nil
}

type streamConn struct {
	network.Stream
	done chan struct{}
	once sync.Once
}

func newConn(s network.Stream) *streamConn { return &streamConn{Stream: s, done: make(chan struct{})} }
func (c *streamConn) Close() error {
	err := c.Stream.Close()
	c.once.Do(func() { close(c.done) })
	return err
}
func (c *streamConn) LocalAddr() net.Addr  { return peerAddr(c.Conn().LocalPeer().String()) }
func (c *streamConn) RemoteAddr() net.Addr { return peerAddr(c.Conn().RemotePeer().String()) }

type peerAddr string

func (a peerAddr) Network() string { return "quic" }
func (a peerAddr) String() string  { return string(a) }

type oneListener struct {
	conn     *streamConn
	accepted bool
}

func (l *oneListener) Accept() (net.Conn, error) {
	if !l.accepted {
		l.accepted = true
		return l.conn, nil
	}
	<-l.conn.done
	return nil, net.ErrClosed
}
func (l *oneListener) Close() error   { return l.conn.Close() }
func (l *oneListener) Addr() net.Addr { return l.conn.LocalAddr() }
