// Command fleetctl is a small operator/debug CLI for a Fleet — the seed of what
// the Agent's Fleet toolset will do. It reads the Registry (JetStream KV) and
// drives Nodes over the control plane.
//
//	fleetctl nodes --nats <url> --fleet <id>
//	fleetctl run   --nats <url> --fleet <id> --node <id> --code "<code>" [--kind shell|python]
//	fleetctl ping  --nats <url> --fleet <id> --node <id>
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/google/uuid"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: fleetctl <nodes|run|ping> [flags]")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "nodes":
		cmdNodes(os.Args[2:])
	case "run":
		cmdRun(os.Args[2:])
	case "ping":
		cmdPing(os.Args[2:])
	case "transfer":
		cmdTransfer(os.Args[2:])
	default:
		fmt.Fprintln(os.Stderr, "unknown command:", os.Args[1])
		os.Exit(2)
	}
}

func cmdTransfer(args []string) {
	fs := flag.NewFlagSet("transfer", flag.ExitOnError)
	natsURL := fs.String("nats", nats.DefaultURL, "NATS url")
	fleet := fs.String("fleet", "", "fleet id")
	src := fs.String("src", "", "source node id")
	dst := fs.String("dst", "", "destination node id")
	srcPath := fs.String("src-path", "", "source path (on src node)")
	dstPath := fs.String("dst-path", "", "destination path (on dst node)")
	timeout := fs.Int("timeout", 600, "timeout seconds")
	_ = fs.Parse(args)

	nc := dial(*natsURL)
	defer nc.Drain() //nolint:errcheck

	tid := "x_" + uuid.NewString()[:8]
	sub, err := nc.Subscribe(proto.SubjTransferProgress(*fleet, tid), func(m *nats.Msg) {
		var p proto.TransferProgress
		if json.Unmarshal(m.Data, &p) == nil && p.State == "transferring" {
			fmt.Printf("\rtransferring %d/%d bytes (%.1f MB/s)      ",
				p.BytesDone, p.BytesTotal, float64(p.RateBps)/1e6)
		}
	})
	must(err)
	defer sub.Unsubscribe() //nolint:errcheck

	cmd := proto.Command{Type: "transfer", Transfer: &proto.TransferRequest{
		TransferID: tid, SrcNode: *src, DstNode: *dst,
		SrcPath: *srcPath, DstPath: *dstPath,
		Options: proto.TransferOptions{Verify: "sha256"},
	}}
	b, _ := json.Marshal(cmd)
	msg, err := nc.Request(proto.SubjNodeCmd(*fleet, *src), b, time.Duration(*timeout)*time.Second)
	must(err)

	var res proto.TransferProgress
	must(json.Unmarshal(msg.Data, &res))
	fmt.Println()
	if res.State == "done" {
		fmt.Printf("done -> %s  (sha256=%s)\n", *dstPath, res.SHA256)
		return
	}
	fmt.Fprintln(os.Stderr, "transfer failed:", res.Error)
	os.Exit(1)
}

func cmdNodes(args []string) {
	fs := flag.NewFlagSet("nodes", flag.ExitOnError)
	natsURL := fs.String("nats", nats.DefaultURL, "NATS url")
	fleet := fs.String("fleet", "", "fleet id")
	_ = fs.Parse(args)

	nc := dial(*natsURL)
	defer nc.Drain() //nolint:errcheck
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	js, err := jetstream.New(nc)
	must(err)
	kv, err := js.KeyValue(ctx, proto.RegistryBucket(*fleet))
	must(err)
	keys, err := kv.Keys(ctx)
	if err != nil {
		fmt.Println("(no nodes)")
		return
	}
	for _, k := range keys {
		e, err := kv.Get(ctx, k)
		if err != nil {
			continue
		}
		var n proto.Node
		if json.Unmarshal(e.Value(), &n) != nil {
			continue
		}
		fmt.Printf("%-22s %-16s %-7s %s/%s  %dc %.0fGB gpu=%q  reach=%s  seen=%s\n",
			n.NodeID, n.Name, n.State.Status, n.Capability.OS, n.Capability.Arch,
			n.Capability.CPUCores, n.Capability.RAMGB, n.Capability.GPU,
			n.Net.Reachability, n.LastSeen.Format(time.Kitchen))
	}
}

func cmdRun(args []string) {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	natsURL := fs.String("nats", nats.DefaultURL, "NATS url")
	fleet := fs.String("fleet", "", "fleet id")
	node := fs.String("node", "", "target node id")
	code := fs.String("code", "", "code/command to run")
	kind := fs.String("kind", proto.TaskShell, "shell | python")
	timeout := fs.Int("timeout", 60, "task timeout seconds")
	_ = fs.Parse(args)

	nc := dial(*natsURL)
	defer nc.Drain() //nolint:errcheck

	cmd := proto.Command{Type: "run_task", Task: &proto.Task{
		TaskID: "t_" + uuid.NewString()[:8], Kind: *kind, Code: *code, TimeoutS: *timeout,
	}}
	b, _ := json.Marshal(cmd)
	msg, err := nc.Request(proto.SubjNodeCmd(*fleet, *node), b, time.Duration(*timeout+5)*time.Second)
	must(err)

	var res proto.TaskResult
	must(json.Unmarshal(msg.Data, &res))
	if res.Stdout != "" {
		fmt.Print(res.Stdout)
	}
	if res.Stderr != "" {
		fmt.Fprint(os.Stderr, res.Stderr)
	}
	if res.Error != "" {
		fmt.Fprintln(os.Stderr, "error:", res.Error)
	}
	os.Exit(res.ExitCode)
}

func cmdPing(args []string) {
	fs := flag.NewFlagSet("ping", flag.ExitOnError)
	natsURL := fs.String("nats", nats.DefaultURL, "NATS url")
	fleet := fs.String("fleet", "", "fleet id")
	node := fs.String("node", "", "target node id")
	_ = fs.Parse(args)

	nc := dial(*natsURL)
	defer nc.Drain() //nolint:errcheck
	b, _ := json.Marshal(proto.Command{Type: "ping"})
	msg, err := nc.Request(proto.SubjNodeCmd(*fleet, *node), b, 3*time.Second)
	must(err)
	fmt.Println(string(msg.Data))
}

func dial(url string) *nats.Conn {
	nc, err := nats.Connect(url, nats.Name("fleetctl"))
	must(err)
	return nc
}

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
