package main

import (
	"archive/tar"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/nats-io/nats.go"
)

// Exercises two isolated Runner states via real owner-authenticated NATS. The
// leader CA survives Runner and owner connection replacement. Neither Apps nor
// a GPU engine is launched; this is control-plane recovery, not cluster serving.
func TestGroupAuthorityThroughAuthenticatedRunners(t *testing.T) {
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		t.Skip("Unix protected state")
	}
	const owner = "f_aaaaaaaaaaaaaaaa"
	dir := t.TempDir()
	a, err := auth.Bootstrap(filepath.Join(dir, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	url := recoveryServer(t, a)
	connect := func(fleet, node string, denied chan error) *nats.Conn {
		var creds []byte
		var err error
		if node == "" {
			creds, err = a.MintFleetUser(fleet)
		} else {
			creds, err = a.MintFleetNode(fleet, node)
		}
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(dir, fleet+node+".creds")
		if err := os.WriteFile(path, creds, 0600); err != nil {
			t.Fatal(err)
		}
		opts := []nats.Option{nats.UserCredentials(path), nats.CustomInboxPrefix("_INBOX_" + fleet)}
		if denied != nil {
			opts = append(opts, nats.ErrorHandler(func(_ *nats.Conn, _ *nats.Subscription, err error) {
				select {
				case denied <- err:
				default:
				}
			}))
		}
		var nc *nats.Conn
		waitRecovery(t, "NATS unavailable", func() bool { nc, err = nats.Connect(url, opts...); return err == nil })
		t.Cleanup(nc.Close)
		return nc
	}

	agent := connect(owner, "", nil)
	nodes := []string{"authority-leader", "authority-peer"}
	runners := make([]*runner.Runner, 2)
	conns := make([]*nats.Conn, 2)
	startRunner := func(rank int) {
		nc := connect(owner, nodes[rank], nil)
		rec := &proto.Node{NodeID: nodes[rank]}
		r := runner.New(nc, owner, nodes[rank], nil, nil, rec)
		if err := r.EnableLifecycle(filepath.Join(dir, nodes[rank])); err != nil {
			t.Fatal(err)
		}
		if rec.Capability.Runtimes["model-group-authority"] != "1" {
			t.Fatal("missing authority capability")
		}
		if _, err := r.Serve(); err != nil {
			t.Fatal(err)
		}
		if err := nc.Flush(); err != nil {
			t.Fatal(err)
		}
		conns[rank], runners[rank] = nc, r
		t.Cleanup(func() { r.CloseLifecycle() })
	}
	startRunner(0)
	startRunner(1)
	rpc := func(rank int, method string, data map[string]any) map[string]json.RawMessage {
		if data == nil {
			data = map[string]any{}
		}
		data["protocol"], data["type"], data["method"] = 1, "app_lifecycle", method
		payload, _ := json.Marshal(data)
		msg, err := agent.Request(proto.SubjNodeCmd(owner, nodes[rank]), payload, 5*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		var out map[string]json.RawMessage
		if err := json.Unmarshal(msg.Data, &out); err != nil {
			t.Fatal(err)
		}
		return out
	}
	requireOK := func(out map[string]json.RawMessage) map[string]json.RawMessage {
		if out["error"] != nil {
			t.Fatal(string(out["error"]))
		}
		return out
	}
	topology, err := groupcredentials.ParseTopology([]byte(fmt.Sprintf(`{"protocol":1,"owner":%q,"group_id":"durable","model_sha256":%q,"launch_sha256":%q,"members":[{"rank":0,"node_id":%q,"generation":2,"address":"10.10.0.1","port":18400},{"rank":1,"node_id":%q,"generation":2,"address":"10.10.0.2","port":18400}]}`, owner, strings.Repeat("b", 64), strings.Repeat("c", 64), nodes[0], nodes[1])))
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(requireOK(rpc(0, "group_authority_prepare", map[string]any{"group_topology": topology})))
	var authority groupcredentials.AuthorityStatus
	if err := json.Unmarshal(data, &authority); err != nil || authority.CAHash == "" || authority.State != "open" {
		t.Fatal("missing pinned authority", err)
	}
	if out := rpc(1, "group_authority_prepare", map[string]any{"group_topology": topology}); out["error"] == nil {
		t.Fatal("non-leader accepted authority")
	}
	authorityArgs := func() map[string]any {
		return map[string]any{"group_id": topology.Group, "topology_sha256": authority.TopologyHash}
	}
	digests, instances := make([]string, 2), make([]string, 2)
	bindings := make([]map[string]any, 2)
	claims := make([]groupcredentials.Claim, 2)
	issues := make([]groupcredentials.Issuance, 2)
	submit := func(rank int, id, action string, generation uint64) {
		requireOK(rpc(rank, "submit", map[string]any{"request": lifecycle.Request{Protocol: 1, OperationID: id, Action: action, Digest: digests[rank], Scope: "group", Generation: generation}}))
		waitRecovery(t, "operation did not finish", func() bool {
			out := requireOK(rpc(rank, "status", nil))
			var ops map[string]lifecycle.Operation
			json.Unmarshal(out["operations"], &ops)
			op := ops[id]
			if op.State == "failed" {
				t.Fatal(op.Error)
			}
			return op.State == "succeeded"
		})
	}
	for rank := 0; rank < 2; rank++ {
		manifest := groupcredentials.Manifest{Protocol: 1, Rank: &rank, Topology: topology, CAHash: authority.CAHash}
		manifestBytes, _ := json.Marshal(manifest)
		def := lifecycle.Definition{Protocol: 1, AppID: "model-service", Version: "test", Components: []lifecycle.Component{{Name: "engine", Runtime: "process", Argv: []string{"true"}, Readiness: lifecycle.Probe{Argv: []string{"true"}, TimeoutSeconds: 1}, Resources: &lifecycle.ResourceRequest{MemoryBytes: 1 << 20}}}}
		defBytes, _ := json.Marshal(def)
		var archive bytes.Buffer
		tw := tar.NewWriter(&archive)
		for name, body := range map[string][]byte{"fleet.json": defBytes, "group-peer.json": manifestBytes} {
			if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0400, Size: int64(len(body))}); err != nil {
				t.Fatal(err)
			}
			if _, err := tw.Write(body); err != nil {
				t.Fatal(err)
			}
		}
		if err := tw.Close(); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(archive.Bytes())
		digests[rank] = hex.EncodeToString(digest[:])
		requireOK(rpc(rank, "stage", map[string]any{"digest": digests[rank], "offset": 0, "data": archive.Bytes()}))
		submit(rank, "install", "install", 0)
		submit(rank, "prepare", "prepare_start", 0)
		status := requireOK(rpc(rank, "status", nil))
		var rows map[string]lifecycle.Instance
		json.Unmarshal(status["instances"], &rows)
		if len(rows) != 1 {
			t.Fatal("expected one preparation")
		}
		for id := range rows {
			instances[rank] = id
		}
		bindings[rank] = map[string]any{"instance_id": instances[rank], "revision": digests[rank], "generation": 1}
		data, _ := json.Marshal(requireOK(rpc(rank, "group_peer_enroll", bindings[rank])))
		var enrollment groupcredentials.Enrollment
		if err := json.Unmarshal(data, &enrollment); err != nil || enrollment.CSR == "" {
			t.Fatal("invalid enrollment", err)
		}
		claims[rank] = groupcredentials.Claim{Rank: &rank, Node: nodes[rank], Instance: instances[rank], Revision: digests[rank], Scope: "group", Generation: 2, Preparation: "prepare", CSR: enrollment.CSR}
		args := authorityArgs()
		args["group_claim"] = claims[rank]
		data, _ = json.Marshal(requireOK(rpc(0, "group_authority_issue", args)))
		if err := json.Unmarshal(data, &issues[rank]); err != nil || issues[rank].Certificate == "" {
			t.Fatal("invalid issued certificate", err)
		}
	}
	// Replace the leader and owner connection after persisted issuance, before
	// certificate install, as if both issue acknowledgements had been lost.
	conns[0].Close()
	if err := runners[0].CloseLifecycle(); err != nil {
		t.Fatal(err)
	}
	agent.Close()
	agent = connect(owner, "", nil)
	startRunner(0)
	out := requireOK(rpc(0, "group_authority_prepare", map[string]any{"group_topology": topology}))
	if string(out["ca_pem"]) != string(mustJSON(authority.CA)) {
		t.Fatal("leader restart rotated trust")
	}
	for rank := 0; rank < 2; rank++ {
		args := authorityArgs()
		args["group_claim"] = claims[rank]
		out := requireOK(rpc(0, "group_authority_issue", args))
		if string(out["certificate_pem"]) != string(mustJSON(issues[rank].Certificate)) {
			t.Fatal("retry reissued a certificate")
		}
		// Enrollment retry also uses the existing private key after leader restart.
		out = requireOK(rpc(rank, "group_peer_enroll", bindings[rank]))
		if string(out["csr_pem"]) != string(mustJSON(claims[rank].CSR)) {
			t.Fatal("enrollment rotated key")
		}
		bindings[rank]["certificate_pem"], bindings[rank]["ca_pem"] = issues[rank].Certificate, authority.CA
		if out := requireOK(rpc(rank, "group_peer_install", bindings[rank])); string(out["certificate_installed"]) != "true" {
			t.Fatal("certificate was not installed")
		}
	}
	// A narrow node identity (even in this Fleet) cannot command the leader CA.
	denied := make(chan error, 8)
	peer := connect(owner, "narrow-peer", denied)
	peer.Publish(proto.SubjNodeCmd(owner, nodes[0]), []byte(`{"type":"app_lifecycle","protocol":1,"method":"group_authority_status"}`))
	peer.Flush()
	select {
	case err := <-denied:
		if !strings.Contains(strings.ToLower(err.Error()), "permission") {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("node credentials could command authority")
	}
	out = requireOK(rpc(0, "group_authority_close", authorityArgs()))
	if string(out["state"]) != `"closed"` {
		t.Fatal("close failed")
	}
	args := authorityArgs()
	args["group_claim"] = claims[0]
	if out := rpc(0, "group_authority_issue", args); out["error"] == nil {
		t.Fatal("closed authority issued")
	}
	if out := requireOK(rpc(0, "group_authority_prepare", map[string]any{"group_topology": topology})); string(out["state"]) != `"closed"` {
		t.Fatal("closed authority reopened")
	}
	for rank := 0; rank < 2; rank++ {
		submit(rank, "cancel", "stop", 1)
		if out := rpc(rank, "group_peer_install", bindings[rank]); out["error"] == nil {
			t.Fatal("late install after cancel")
		}
		status := requireOK(rpc(rank, "status", nil))
		var rows map[string]lifecycle.Instance
		json.Unmarshal(status["instances"], &rows)
		in := rows[instances[rank]]
		if in.State != "stopped" || len(in.Resources) > 0 || len(in.Reservations) > 0 {
			t.Fatal("cancel did not release preparation")
		}
	}
}

func mustJSON(value any) []byte {
	data, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return data
}
