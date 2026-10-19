package groupnetwork

import (
	"context"
	"io"
	"net"
	"strings"
	"testing"
	"time"
)

func TestForwarderHalfCloseAndCancellation(t *testing.T) {
	upstream, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer upstream.Close()
	finished := make(chan error, 1)
	go func() {
		c, e := upstream.Accept()
		if e != nil {
			finished <- e
			return
		}
		defer c.Close()
		data, e := io.ReadAll(c)
		if e == nil && string(data) != "request" {
			e = io.ErrUnexpectedEOF
		}
		if e == nil {
			_, e = c.Write([]byte("response"))
		}
		finished <- e
	}()
	f, err := Forward(1234, func(ctx context.Context, port int) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp4", upstream.Addr().String())
	})
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	client, err := net.Dial("tcp4", strings.TrimPrefix(f.Address(), "http://"))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	client.SetDeadline(time.Now().Add(3 * time.Second))
	client.Write([]byte("request"))
	client.(*net.TCPConn).CloseWrite()
	data, err := io.ReadAll(client)
	if err != nil || string(data) != "response" {
		t.Fatal(string(data), err)
	}
	if err = <-finished; err != nil {
		t.Fatal(err)
	}
	entered := make(chan struct{})
	blocked, err := Forward(1234, func(ctx context.Context, port int) (net.Conn, error) {
		close(entered)
		<-ctx.Done()
		return nil, ctx.Err()
	})
	if err != nil {
		t.Fatal(err)
	}
	defer blocked.Close()
	c, err := net.Dial("tcp4", strings.TrimPrefix(blocked.Address(), "http://"))
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("dial did not start")
	}
	done := make(chan struct{})
	go func() { blocked.Close(); close(done) }()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("close retained pending dial")
	}
	if again, err := net.DialTimeout("tcp4", strings.TrimPrefix(blocked.Address(), "http://"), time.Second); err == nil {
		again.Close()
		t.Fatal("listener survived close")
	}
}

func TestForwarderBoundsLiveConnectionsAndClosesBothEnds(t *testing.T) {
	// Hold every admitted upstream open: the 65th connection must not allocate
	// another namespace dial, and shutdown must reap all established streams.
	peers := make(chan net.Conn, 65)
	f, err := Forward(1234, func(ctx context.Context, port int) (net.Conn, error) {
		server, peer := net.Pipe()
		peers <- peer
		return server, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var clients, upstreams []net.Conn
	t.Cleanup(func() {
		for _, c := range append(clients, upstreams...) {
			c.Close()
		}
	})
	for i := 0; i < 64; i++ {
		client, e := net.DialTimeout("tcp4", strings.TrimPrefix(f.Address(), "http://"), time.Second)
		if e != nil {
			t.Fatal(e)
		}
		clients = append(clients, client)
		select {
		case peer := <-peers:
			upstreams = append(upstreams, peer)
		case <-time.After(3 * time.Second):
			t.Fatal("admitted dial did not arrive")
		}
	}
	extra, err := net.DialTimeout("tcp4", strings.TrimPrefix(f.Address(), "http://"), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer extra.Close()
	extra.SetReadDeadline(time.Now().Add(3 * time.Second))
	if _, err := extra.Read(make([]byte, 1)); err != io.EOF {
		t.Fatalf("saturated ingress did not reject new connection: %v", err)
	}
	select {
	case p := <-peers:
		p.Close()
		t.Fatal("saturated ingress allocated an upstream")
	default:
	}
	done := make(chan struct{})
	go func() { f.Close(); close(done) }()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("shutdown retained established streams")
	}
	for _, c := range append(clients, upstreams...) {
		c.SetReadDeadline(time.Now().Add(time.Second))
		if _, err := c.Read(make([]byte, 1)); err != io.EOF {
			t.Fatalf("shutdown did not close both sides: %v", err)
		}
	}
}
