package appmedia

import (
	"context"
	"errors"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/pion/ice/v4"
)

type blockedMux struct {
	entered, allow, closed chan struct{}
	once                   sync.Once
}

func newBlockedMux() *blockedMux {
	return &blockedMux{entered: make(chan struct{}), allow: make(chan struct{}), closed: make(chan struct{})}
}
func (m *blockedMux) Close() error {
	m.once.Do(func() { close(m.entered); <-m.allow; close(m.closed) })
	return nil
}
func (m *blockedMux) GetConn(string, net.Addr) (net.PacketConn, error) {
	return nil, errors.New("not a packet fixture")
}
func (m *blockedMux) RemoveConnByUfrag(string)       {}
func (m *blockedMux) GetListenAddresses() []net.Addr { return nil }
func channelReady(t *testing.T, ch <-chan struct{}) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(time.Second):
		t.Fatal("socket lifecycle did not finish")
	}
}

func TestSocketPoolReusesAddressesAndDrainsOldGeneration(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p := newSocketPool(ctx)
	signature := "network-a"
	p.fingerprint = func() (string, error) { return signature, nil }
	var opened []*blockedMux
	p.listen = func() (ice.UDPMux, error) { m := newBlockedMux(); opened = append(opened, m); return m, nil }
	first, release1, err := p.acquire()
	if err != nil {
		t.Fatal(err)
	}
	same, release2, err := p.acquire()
	if err != nil || first != same || len(opened) != 1 {
		t.Fatal("reopened sockets for same network")
	}
	release1()
	release1() // Idempotent session cleanup must not consume another reference.
	signature = "network-b"
	second, release3, err := p.acquire()
	if err != nil || second == first || len(opened) != 2 {
		t.Fatal("new network not reflected")
	}
	select {
	case <-opened[0].entered:
		t.Fatal("retired sockets closed under existing peer")
	default:
	}
	release2()
	channelReady(t, opened[0].entered)
	release3()
	select {
	case <-opened[1].entered:
		t.Fatal("idle current sockets not retained for reuse")
	default:
	}
	cancel()
	channelReady(t, opened[1].entered)
	select {
	case <-p.closed:
		t.Fatal("reported shutdown before physical sockets closed")
	default:
	}
	close(opened[0].allow)
	close(opened[1].allow)
	channelReady(t, p.closed)
	if _, _, err := p.acquire(); err == nil {
		t.Fatal("acquired sockets after shutdown")
	}
}

func TestSocketPoolCountsClosingGenerationsAgainstCapacity(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p := newSocketPool(ctx)
	signature := ""
	p.fingerprint = func() (string, error) { return signature, nil }
	var opened []*blockedMux
	p.listen = func() (ice.UDPMux, error) { m := newBlockedMux(); opened = append(opened, m); return m, nil }
	for i := 0; i < maxSocketGenerations; i++ {
		signature += "x"
		_, release, err := p.acquire()
		if err != nil {
			t.Fatal(err)
		}
		release()
	}
	signature += "x"
	if _, _, err := p.acquire(); err == nil {
		t.Fatal("slow physical closes bypassed capacity")
	}
	if len(opened) != maxSocketGenerations {
		t.Fatal("allocated sockets before checking capacity")
	}
	// Physical closure, not a queued close, enables the next network generation.
	close(opened[0].allow)
	channelReady(t, opened[0].closed)
	deadline := time.Now().Add(time.Second)
	for {
		p.mu.Lock()
		n := len(p.live)
		p.mu.Unlock()
		if n < maxSocketGenerations {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("closed generation retained")
		}
		time.Sleep(time.Millisecond)
	}
	_, release, err := p.acquire()
	if err != nil {
		t.Fatal(err)
	}
	release()
	cancel()
	for _, m := range opened[1:] {
		close(m.allow)
	}
	channelReady(t, p.closed)
}

func TestSocketPoolListenFailurePreservesExistingPeers(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p := newSocketPool(ctx)
	signature := "a"
	p.fingerprint = func() (string, error) { return signature, nil }
	m := newBlockedMux()
	p.listen = func() (ice.UDPMux, error) { return m, nil }
	first, release, err := p.acquire()
	if err != nil {
		t.Fatal(err)
	}
	signature = "b"
	p.listen = func() (ice.UDPMux, error) { return nil, errors.New("network unavailable") }
	if _, _, err := p.acquire(); err == nil {
		t.Fatal("accepted failed listener")
	}
	select {
	case <-m.entered:
		t.Fatal("failed new network closed active old peers")
	default:
	}
	signature = "a"
	existing, release2, err := p.acquire()
	if err != nil || existing != first {
		t.Fatal("original transport lost")
	}
	release()
	release2()
	cancel()
	close(m.allow)
	channelReady(t, p.closed)
}
