package groupnetwork

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/netip"
	"sync"
)

// Forwarder exposes one declared port on node loopback, without adding a route
// or interface to the private namespace. The existing App gateway still owns
// remote authorization. The owner must close it before closing its namespace fd.
type Forwarder struct {
	listener    net.Listener
	ctx         context.Context
	cancel      context.CancelFunc
	mu          sync.Mutex
	closed      bool
	connections map[net.Conn]bool
	jobs        sync.WaitGroup
	slots       chan struct{}
}

func Forward(port int, dial func(context.Context, int) (net.Conn, error)) (*Forwarder, error) {
	return ForwardAt("127.0.0.1:0", port, dial)
}

// ForwardAt restores an original loopback listener on explicit lifecycle
// recovery. An occupied port is an error, never permission to replace its owner.
func ForwardAt(address string, port int, dial func(context.Context, int) (net.Conn, error)) (*Forwarder, error) {
	endpoint, err := netip.ParseAddrPort(address)
	if err != nil || endpoint.String() != address || endpoint.Addr().String() != "127.0.0.1" {
		return nil, fmt.Errorf("ingress must bind numeric IPv4 loopback")
	}
	if port < 1 || port > 65535 || dial == nil {
		return nil, fmt.Errorf("declare a container loopback port")
	}
	l, err := net.Listen("tcp4", address)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	f := &Forwarder{listener: l, ctx: ctx, cancel: cancel, connections: map[net.Conn]bool{}, slots: make(chan struct{}, 64)}
	f.jobs.Add(1)
	go func() {
		defer f.jobs.Done()
		for {
			client, err := l.Accept()
			if err != nil {
				return
			}
			select {
			case f.slots <- struct{}{}:
			default:
				client.Close()
				continue
			}
			if !f.track(client) {
				<-f.slots
				return
			}
			f.jobs.Add(1)
			go func() {
				defer f.jobs.Done()
				defer func() { <-f.slots }()
				defer f.release(client)
				server, err := dial(ctx, port)
				if err != nil {
					return
				}
				if !f.track(server) {
					return
				}
				defer f.release(server)
				done := make(chan struct{}, 2)
				copyHalf := func(dst, src net.Conn) {
					_, _ = io.Copy(dst, src)
					if tcp, ok := dst.(*net.TCPConn); ok {
						_ = tcp.CloseWrite()
					} else {
						dst.Close()
					}
					done <- struct{}{}
				}
				go copyHalf(server, client)
				go copyHalf(client, server)
				<-done
				<-done
			}()
		}
	}()
	return f, nil
}

func (f *Forwarder) Address() string { return "http://" + f.listener.Addr().String() }
func (f *Forwarder) track(c net.Conn) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.closed {
		c.Close()
		return false
	}
	f.connections[c] = true
	return true
}
func (f *Forwarder) release(c net.Conn) {
	c.Close()
	f.mu.Lock()
	delete(f.connections, c)
	f.mu.Unlock()
}
func (f *Forwarder) Close() error {
	f.mu.Lock()
	if !f.closed {
		f.closed = true
		f.cancel()
		f.listener.Close()
		for c := range f.connections {
			c.Close()
		}
	}
	f.mu.Unlock()
	f.jobs.Wait()
	return nil
}
