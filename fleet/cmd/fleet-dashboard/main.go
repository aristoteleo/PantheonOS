// Command fleet-dashboard is a small human-facing live view of a Fleet. It
// reads the Registry (JetStream KV) and watches transfer progress over NATS,
// and serves a single self-refreshing page plus a JSON API at /api/state.
//
//	fleet-dashboard --nats nats://host:4222 --fleet f_xxx [--creds fleet.creds] [--addr :8088]
//
// With --creds it connects with scoped credentials and the per-fleet inbox,
// exactly like a Runner; without, it connects to a dev (unauthenticated) NATS.
package main

import (
	_ "embed"
	"context"
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"sort"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

//go:embed index.html
var indexHTML []byte

type state struct {
	mu        sync.RWMutex
	nodes     []proto.Node
	transfers map[string]proto.TransferProgress
}

func main() {
	natsURL := flag.String("nats", nats.DefaultURL, "NATS url")
	fleet := flag.String("fleet", "", "fleet id")
	creds := flag.String("creds", "", "path to a fleet .creds file (scoped auth); empty = dev/no-auth")
	addr := flag.String("addr", ":8088", "HTTP listen address")
	flag.Parse()
	if *fleet == "" {
		log.Fatal("--fleet is required")
	}

	opts := []nats.Option{nats.Name("fleet-dashboard")}
	if *creds != "" {
		opts = append(opts, nats.UserCredentials(*creds), nats.CustomInboxPrefix("_INBOX_"+*fleet))
	}
	nc, err := nats.Connect(*natsURL, opts...)
	if err != nil {
		log.Fatalf("nats connect: %v", err)
	}
	defer nc.Drain() //nolint:errcheck
	js, err := jetstream.New(nc)
	if err != nil {
		log.Fatal(err)
	}

	st := &state{transfers: map[string]proto.TransferProgress{}}

	// Watch transfer progress live.
	_, err = nc.Subscribe("fleet."+*fleet+".transfer.*.progress", func(m *nats.Msg) {
		var p proto.TransferProgress
		if json.Unmarshal(m.Data, &p) != nil {
			return
		}
		st.mu.Lock()
		st.transfers[p.TransferID] = p
		st.mu.Unlock()
	})
	if err != nil {
		log.Fatalf("subscribe progress: %v", err)
	}

	// Poll the Registry for nodes.
	go func() {
		for {
			if nodes := readNodes(js, *fleet); nodes != nil {
				st.mu.Lock()
				st.nodes = nodes
				st.mu.Unlock()
			}
			time.Sleep(2 * time.Second)
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(indexHTML) //nolint:errcheck
	})
	mux.HandleFunc("/api/state", func(w http.ResponseWriter, _ *http.Request) {
		st.mu.RLock()
		nodes := st.nodes
		active := make([]proto.TransferProgress, 0, len(st.transfers))
		for _, p := range st.transfers {
			active = append(active, p)
		}
		st.mu.RUnlock()
		sort.Slice(active, func(i, j int) bool { return active[i].TransferID < active[j].TransferID })
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{ //nolint:errcheck
			"fleet":     *fleet,
			"nodes":     nodes,
			"transfers": active,
			"ts":        time.Now().Format(time.RFC3339),
		})
	})

	log.Printf("fleet-dashboard on http://localhost%s (fleet=%s, auth=%v)", *addr, *fleet, *creds != "")
	log.Fatal(http.ListenAndServe(*addr, mux))
}

func readNodes(js jetstream.JetStream, fleet string) []proto.Node {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	kv, err := js.KeyValue(ctx, proto.RegistryBucket(fleet))
	if err != nil {
		return nil
	}
	keys, err := kv.Keys(ctx)
	if err != nil {
		return []proto.Node{} // empty bucket
	}
	out := make([]proto.Node, 0, len(keys))
	for _, k := range keys {
		e, err := kv.Get(ctx, k)
		if err != nil {
			continue
		}
		var n proto.Node
		if json.Unmarshal(e.Value(), &n) == nil {
			out = append(out, n)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}
