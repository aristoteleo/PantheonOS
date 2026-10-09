package main

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// natsNodes is the reconciler's view of each fleet: its registry and its
// nodes' App lifecycle protocol, over the controller's own fleet credentials.
type natsNodes struct {
	authority *auth.Authority
	url       string
	mu        sync.Mutex
	conns     map[string]natsConn
}

type natsConn struct {
	nc      *nats.Conn
	expires time.Time
}

func newNATSNodes(authority *auth.Authority, url string) *natsNodes {
	return &natsNodes{authority: authority, url: url, conns: map[string]natsConn{}}
}

func (n *natsNodes) conn(fleet string) (*nats.Conn, error) {
	n.mu.Lock()
	defer n.mu.Unlock()
	if c, ok := n.conns[fleet]; ok && c.expires.After(time.Now()) && !c.nc.IsClosed() {
		return c.nc, nil
	} else if ok {
		c.nc.Close()
		delete(n.conns, fleet)
	}
	if len(n.conns) >= 256 {
		return nil, fmt.Errorf("reconciler connection capacity reached")
	}
	nc, err := connectAppGateway(n.authority, fleet, n.url)
	if err != nil {
		return nil, err
	}
	n.conns[fleet] = natsConn{nc, time.Now().Add(auth.AccessTTL / 2)}
	return nc, nil
}

func (n *natsNodes) Nodes(ctx context.Context, fleet string) ([]proto.Node, error) {
	nc, err := n.conn(fleet)
	if err != nil {
		return nil, err
	}
	js, err := jetstream.New(nc)
	if err != nil {
		return nil, err
	}
	return readNodes(js, fleet), nil
}

func (n *natsNodes) Call(ctx context.Context, fleet, node string, command map[string]any, out any) error {
	nc, err := n.conn(fleet)
	if err != nil {
		return err
	}
	command["type"], command["protocol"] = "app_lifecycle", 1
	data, err := json.Marshal(command)
	if err != nil {
		return err
	}
	response, err := nc.RequestWithContext(ctx, proto.SubjNodeCmd(fleet, node), data)
	if err != nil {
		return err
	}
	var failure struct {
		Error string `json:"error"`
	}
	if json.Unmarshal(response.Data, &failure) == nil && failure.Error != "" {
		return fmt.Errorf("%s", failure.Error)
	}
	return json.Unmarshal(response.Data, out)
}
