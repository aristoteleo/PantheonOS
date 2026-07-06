// Package registry stores a Node's live record in the Fleet's JetStream KV
// bucket and keeps it fresh with a heartbeat. A missed heartbeat lets the
// record's TTL expire, which is how a Node becomes "offline" — no extra
// bookkeeping required.
package registry

import (
	"context"
	"encoding/json"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// Registry is bound to one Fleet bucket and one Node key.
type Registry struct {
	kv   jetstream.KeyValue
	node string
}

// Open ensures the Fleet's KV bucket exists (with a liveness TTL) and returns a
// Registry bound to this Node.
func Open(ctx context.Context, nc *nats.Conn, fleet, node string, ttl time.Duration) (*Registry, error) {
	js, err := jetstream.New(nc)
	if err != nil {
		return nil, err
	}
	kv, err := js.CreateOrUpdateKeyValue(ctx, jetstream.KeyValueConfig{
		Bucket:      proto.RegistryBucket(fleet),
		Description: "Pantheon-Fleet node registry",
		TTL:         ttl,
	})
	if err != nil {
		return nil, err
	}
	return &Registry{kv: kv, node: node}, nil
}

// Put writes the current Node record (stamping LastSeen).
func (r *Registry) Put(ctx context.Context, n proto.Node) error {
	n.LastSeen = time.Now().UTC()
	b, err := json.Marshal(n)
	if err != nil {
		return err
	}
	_, err = r.kv.Put(ctx, r.node, b)
	return err
}

// Delete removes this Node's record (graceful shutdown).
func (r *Registry) Delete(ctx context.Context) error {
	return r.kv.Delete(ctx, r.node)
}

// Get reads another Node's record (e.g. to find a transfer peer's multiaddrs).
func (r *Registry) Get(ctx context.Context, node string) (proto.Node, error) {
	var n proto.Node
	e, err := r.kv.Get(ctx, node)
	if err != nil {
		return n, err
	}
	if err := json.Unmarshal(e.Value(), &n); err != nil {
		return n, err
	}
	return n, nil
}
