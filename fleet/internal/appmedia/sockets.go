package appmedia

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"sort"
	"strings"
	"sync"

	"github.com/pion/ice/v4"
)

// Socket ownership is node-scoped. Each peer still has its own ICE credentials,
// DTLS identity and data channel, but no longer opens one UDP socket per address.
// On network changes, new peers use a new generation; old peers retain theirs.
// Even retiring generations count toward the limit until physical close returns.
const maxSocketGenerations = 4

type socketGeneration struct {
	mux               ice.UDPMux
	signature         string
	users             int
	retiring, closing bool
	closeErr          error
}

type socketPool struct {
	ctx         context.Context
	mu          sync.Mutex
	current     *socketGeneration
	live        map[*socketGeneration]struct{}
	stopping    bool
	closed      chan struct{}
	fingerprint func() (string, error)
	listen      func() (ice.UDPMux, error)
}

func newSocketPool(ctx context.Context) *socketPool {
	p := &socketPool{ctx: ctx, live: make(map[*socketGeneration]struct{}), closed: make(chan struct{}), fingerprint: networkFingerprint, listen: func() (ice.UDPMux, error) { return ice.NewMultiUDPMuxFromPort(0, ice.UDPMuxFromPortWithLoopback()) }}
	go func() { <-ctx.Done(); p.shutdown() }()
	return p
}

func networkFingerprint() (string, error) {
	interfaces, err := net.Interfaces()
	if err != nil {
		return "", err
	}
	var keys []string
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			return "", err
		}
		for _, addr := range addrs {
			keys = append(keys, fmt.Sprintf("%s/%d/%d/%s", iface.Name, iface.Index, iface.Flags, addr.String()))
		}
	}
	sort.Strings(keys)
	sum := sha256.Sum256([]byte(strings.Join(keys, "\n")))
	return hex.EncodeToString(sum[:]), nil
}

func (p *socketPool) acquire() (ice.UDPMux, func(), error) {
	if err := p.ctx.Err(); err != nil {
		return nil, nil, err
	}
	signature, err := p.fingerprint()
	if err != nil {
		return nil, nil, err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.stopping || p.ctx.Err() != nil {
		return nil, nil, errors.New("media sockets stopped")
	}
	current := p.current
	if current == nil || current.signature != signature {
		if len(p.live) >= maxSocketGenerations {
			return nil, nil, errors.New("media network generations still draining")
		}
		mux, err := p.listen()
		if err != nil {
			return nil, nil, err
		}
		if current != nil {
			current.retiring = true
			p.retireLocked(current)
		}
		current = &socketGeneration{mux: mux, signature: signature}
		p.live[current] = struct{}{}
		p.current = current
	}
	current.users++
	var once sync.Once
	return current.mux, func() {
		once.Do(func() {
			p.mu.Lock()
			defer p.mu.Unlock()
			current.users--
			p.retireLocked(current)
		})
	}, nil
}

func (p *socketPool) retireLocked(g *socketGeneration) {
	if !g.retiring || g.users != 0 || g.closing {
		return
	}
	g.closing = true
	go func() {
		err := g.mux.Close()
		p.mu.Lock()
		defer p.mu.Unlock()
		g.closeErr = err
		// A failed physical close must not authorize unbounded replacement sockets.
		if err == nil {
			delete(p.live, g)
		}
		p.finishLocked()
	}()
}

func (p *socketPool) shutdown() {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.stopping {
		return
	}
	p.stopping = true
	p.current = nil
	for g := range p.live {
		g.retiring = true
		p.retireLocked(g)
	}
	p.finishLocked()
}

func (p *socketPool) finishLocked() {
	if p.stopping && len(p.live) == 0 {
		select {
		case <-p.closed:
		default:
			close(p.closed)
		}
	}
}
