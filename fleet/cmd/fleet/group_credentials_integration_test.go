package main

import (
	"archive/tar"
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"math/big"
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

// Exercises real owner-scoped NATS -> Runner -> persisted preparation -> CSR
// -> certificate install. No engine, real Fleet node, GPU or external CA is used.
func TestGroupCredentialsThroughAuthenticatedRunner(t *testing.T) {
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		t.Skip("Unix private-file credential storage")
	}
	const owner, nodeID = "f_aaaaaaaaaaaaaaaa", "credential-test"
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
	nc := connect(owner, nodeID, nil)
	rec := &proto.Node{NodeID: nodeID}
	r := runner.New(nc, owner, nodeID, nil, nil, rec)
	if err := r.EnableLifecycle(filepath.Join(dir, "node")); err != nil {
		t.Fatal(err)
	}
	defer r.CloseLifecycle()
	if rec.Capability.Runtimes["model-group-credentials"] != "1" {
		t.Fatal("missing credential capability")
	}
	if _, err := r.Serve(); err != nil {
		t.Fatal(err)
	}
	if err := nc.Flush(); err != nil {
		t.Fatal(err)
	}
	rpc := func(method string, data map[string]any) map[string]json.RawMessage {
		if data == nil {
			data = map[string]any{}
		}
		data["protocol"], data["type"], data["method"] = 1, "app_lifecycle", method
		payload, _ := json.Marshal(data)
		msg, err := agent.Request(proto.SubjNodeCmd(owner, nodeID), payload, 5*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		var result map[string]json.RawMessage
		if err := json.Unmarshal(msg.Data, &result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(4 * time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	der, err := x509.CreateCertificate(rand.Reader, ca, ca, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	ca, _ = x509.ParseCertificate(der)
	caHash := sha256.Sum256(der)
	caPEM := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
	manifest := fmt.Sprintf(`{"protocol":1,"rank":0,"ca_sha256":%q,"topology":{"protocol":1,"owner":%q,"group_id":"test","model_sha256":%q,"launch_sha256":%q,"members":[{"rank":0,"node_id":%q,"generation":2,"address":"10.10.0.1","port":18400},{"rank":1,"node_id":"peer","generation":2,"address":"10.10.0.2","port":18400}]}}`, hex.EncodeToString(caHash[:]), owner, strings.Repeat("b", 64), strings.Repeat("c", 64), nodeID)
	def := lifecycle.Definition{Protocol: 1, AppID: "model-service", Version: "test", Components: []lifecycle.Component{{Name: "engine", Runtime: "process", Argv: []string{"true"}, Readiness: lifecycle.Probe{Argv: []string{"true"}, TimeoutSeconds: 1}, Resources: &lifecycle.ResourceRequest{MemoryBytes: 1 << 20}}}}
	defBytes, _ := json.Marshal(def)
	var archive bytes.Buffer
	tw := tar.NewWriter(&archive)
	for name, body := range map[string]string{"fleet.json": string(defBytes), "group-peer.json": manifest} {
		if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0400, Size: int64(len(body))}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	digestBytes := sha256.Sum256(archive.Bytes())
	digest := hex.EncodeToString(digestBytes[:])
	if out := rpc("stage", map[string]any{"digest": digest, "offset": 0, "data": archive.Bytes()}); out["error"] != nil {
		t.Fatal(string(out["error"]))
	}
	submit := func(id, action string, generation uint64) {
		out := rpc("submit", map[string]any{"request": lifecycle.Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "group", Generation: generation}})
		if out["error"] != nil {
			t.Fatal(string(out["error"]))
		}
		waitRecovery(t, "operation did not finish", func() bool {
			out := rpc("status", nil)
			var ops map[string]lifecycle.Operation
			json.Unmarshal(out["operations"], &ops)
			op := ops[id]
			if op.State == "failed" {
				t.Fatal(op.Error)
			}
			return op.State == "succeeded"
		})
	}
	submit("install", "install", 0)
	submit("prepare", "prepare_start", 0)
	status := rpc("status", nil)
	var instances map[string]lifecycle.Instance
	json.Unmarshal(status["instances"], &instances)
	if len(instances) != 1 {
		t.Fatal("expected one preparation")
	}
	var instance string
	for id := range instances {
		instance = id
	}
	binding := func() map[string]any {
		return map[string]any{"instance_id": instance, "revision": digest, "generation": 1}
	}
	out := rpc("group_peer_enroll", binding())
	if out["error"] != nil {
		t.Fatal(string(out["error"]))
	}
	data, _ := json.Marshal(out)
	var enrollment groupcredentials.Enrollment
	if err := json.Unmarshal(data, &enrollment); err != nil || enrollment.CSR == "" || enrollment.Generation != 2 {
		t.Fatal("invalid enrollment", err)
	}
	block, _ := pem.Decode([]byte(enrollment.CSR))
	if block == nil {
		t.Fatal("missing CSR")
	}
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil || csr.CheckSignature() != nil {
		t.Fatal("invalid CSR", err)
	}
	leaf := &x509.Certificate{SerialNumber: big.NewInt(2), DNSNames: csr.DNSNames, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}}
	certDER, err := x509.CreateCertificate(rand.Reader, leaf, ca, csr.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	install := binding()
	install["certificate_pem"] = string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certDER}))
	install["ca_pem"] = caPEM
	out = rpc("group_peer_install", install)
	if string(out["certificate_installed"]) != "true" {
		t.Fatal("install failed", string(out["error"]))
	}
	// A different owner's real NATS credentials cannot reach this lifecycle RPC.
	denied := make(chan error, 8)
	foreign := connect("f_bbbbbbbbbbbbbbbb", "", denied)
	if err := foreign.Publish(proto.SubjNodeCmd(owner, nodeID), []byte(`{"type":"app_lifecycle","protocol":1,"method":"group_peer_enroll"}`)); err != nil {
		t.Fatal(err)
	}
	foreign.Flush()
	select {
	case err := <-denied:
		if !strings.Contains(strings.ToLower(err.Error()), "permission") {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("foreign owner publish was not denied")
	}
	submit("cancel", "stop", 1)
	if out := rpc("group_peer_install", install); out["error"] == nil {
		t.Fatal("late certificate installed after cancellation")
	}
}
