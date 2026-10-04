package runner

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

// Actual owner-scoped NATS envelope, Runner decoder, lifecycle and process.
func TestAppConfigurationOverOwnerNATS(t *testing.T) {
	binary, err := exec.LookPath("nats-server")
	if err != nil {
		t.Skip("nats-server required")
	}
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 required")
	}
	root := t.TempDir()
	authority, err := auth.Bootstrap(filepath.Join(root, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	listener.Close()
	config := filepath.Join(root, "nats.conf")
	if err := os.WriteFile(config, []byte(authority.ServerConfig(address, filepath.Join(root, "js"))), 0600); err != nil {
		t.Fatal(err)
	}
	process := exec.Command(binary, "-c", config)
	if err := process.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { process.Process.Kill(); process.Wait() }()
	owner, node := "f_configuration_test", "mac"
	creds, err := authority.MintFleetNode(owner, node)
	if err != nil {
		t.Fatal(err)
	}
	var nc *nats.Conn
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
		nc, err = nats.Connect("nats://"+address, nats.UserCredentialBytes(creds), nats.CustomInboxPrefix("_INBOX_"+owner))
		if err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	r := New(nc, owner, node, nil, nil, &proto.Node{NodeID: node, Capability: proto.Capability{Runtimes: map[string]string{}}})
	if err := r.EnableLifecycle(filepath.Join(root, "state")); err != nil {
		t.Fatal(err)
	}
	defer r.CloseLifecycle()
	defer func() {
		for _, in := range r.lifecycle.Snapshot().Instances {
			for _, res := range in.Resources {
				if err := (lifecycle.NativeDriver{}).Stop(context.Background(), lifecycle.Component{StopSeconds: 1}, res); err != nil {
					t.Error(err)
				}
			}
		}
	}()
	if _, err := nc.Subscribe(proto.SubjNodeCmd(owner, node), r.handleLifecycle); err != nil {
		t.Fatal(err)
	}
	if err := nc.Flush(); err != nil {
		t.Fatal(err)
	}
	userCreds, err := authority.MintFleetUser(owner)
	if err != nil {
		t.Fatal(err)
	}
	user, err := nats.Connect("nats://"+address, nats.UserCredentialBytes(userCreds), nats.CustomInboxPrefix("_INBOX_"+owner))
	if err != nil {
		t.Fatal(err)
	}
	defer user.Close()
	call := func(q lifecycle.Command) map[string]json.RawMessage {
		t.Helper()
		q.Type, q.Protocol = "app_lifecycle", 1
		body, err := json.Marshal(q)
		if err != nil {
			t.Fatal(err)
		}
		message, err := user.Request(proto.SubjNodeCmd(owner, node), body, 5*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		var response map[string]json.RawMessage
		if json.Unmarshal(message.Data, &response) != nil || response["error"] != nil {
			t.Fatalf("control request failed: %s", message.Data)
		}
		return response
	}
	if r.rec.Capability.Runtimes["credential-import"] != "1" {
		t.Fatal("credential import capability missing")
	}
	challenge := call(lifecycle.Command{Method: "credential_prepare", CredentialRef: "node-secret://budget", CredentialEndpoint: "https://api.test/v1"})
	if string(challenge["owner"]) != `"`+owner+`"` || string(challenge["node_id"]) != `"`+node+`"` {
		t.Fatal("challenge identity does not match authenticated Runner")
	}
	var challengeID string
	if err := json.Unmarshal(challenge["challenge_id"], &challengeID); err != nil {
		t.Fatal(err)
	}
	invalidEnvelope, _ := json.Marshal(lifecycle.Command{Type: "app_lifecycle", Protocol: 1, Method: "credential_ensure",
		CredentialChallenge: challengeID, CredentialEnvelope: &modelcredentials.ImportEnvelope{PublicKey: "invalid", Nonce: "invalid", Data: "invalid"}})
	reply, err := user.Request(proto.SubjNodeCmd(owner, node), invalidEnvelope, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	var failed map[string]string
	if json.Unmarshal(reply.Data, &failed) != nil || failed["error"] != modelcredentials.ErrImport.Error() {
		t.Fatal("encrypted delivery did not reach the credential importer")
	}
	def := lifecycle.Definition{Protocol: 1, AppID: "config-test", Version: "1.0.0", Components: []lifecycle.Component{{
		Name: "backend", Runtime: "process", Configuration: &lifecycle.ConfigDeclaration{Values: map[string]lifecycle.ConfigField{"marker": {Required: true}}},
		Argv:      []string{"python3", "-c", `import os,json,sys,time; from pathlib import Path; cfg=json.load(open(os.environ['PANTHEON_APP_CONFIG'])); assert cfg['values']['marker']=='owner-input'; Path(sys.argv[1],'ready').touch(); time.sleep(120)`, "${DATA}"},
		Readiness: lifecycle.Probe{Argv: []string{"python3", "-c", "from pathlib import Path; import sys; assert Path(sys.argv[1],'ready').is_file()", "${DATA}"}, TimeoutSeconds: 3},
	}}}
	manifest, _ := json.Marshal(def)
	var archive bytes.Buffer
	w := tar.NewWriter(&archive)
	if err := w.WriteHeader(&tar.Header{Name: "fleet.json", Mode: 0400, Size: int64(len(manifest))}); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Write(manifest); err != nil {
		t.Fatal(err)
	}
	appManifest := []byte(`{"apiVersion":2,"id":"config-test","version":"1.0.0"}`)
	if err := w.WriteHeader(&tar.Header{Name: "app.json", Mode: 0400, Size: int64(len(appManifest))}); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Write(appManifest); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(archive.Bytes())
	digest := hex.EncodeToString(sum[:])
	call(lifecycle.Command{Method: "stage", Digest: digest, Data: archive.Bytes()})
	run := func(id, action string, generation uint64, preparation string) {
		t.Helper()
		call(lifecycle.Command{Method: "submit", Request: &lifecycle.Request{Protocol: 1, OperationID: id, Action: action, Digest: digest, Scope: "app", Generation: generation, StartPreparationID: preparation}})
		for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); {
			op := r.lifecycle.Snapshot().Operations[id]
			if op.State == "succeeded" {
				return
			}
			if op.State != "running" && op.State != "queued" {
				t.Fatal(op.Error)
			}
			time.Sleep(10 * time.Millisecond)
		}
		t.Fatal("App operation timed out")
	}
	run("install", "install", 0, "")
	run("prepare", "prepare_start", 0, "")
	var prepared *lifecycle.Instance
	for _, in := range r.lifecycle.Snapshot().Instances {
		prepared = in
	}
	if prepared == nil || prepared.State != "prepared" {
		t.Fatal("missing prepared instance")
	}
	manifestReply := call(lifecycle.Command{Method: "app_manifest", Revision: digest})
	if !bytes.Contains(manifestReply["manifest"], []byte("config-test")) {
		t.Fatal("wrong installed manifest")
	}
	call(lifecycle.Command{Method: "check_instance", Instance: prepared.ID, Revision: digest, Generation: prepared.Generation + 1, Preparation: prepared.StartPreparationID})
	q := lifecycle.Command{Method: "configure", Instance: prepared.ID, Revision: digest, Generation: prepared.Generation, Configuration: &lifecycle.AppConfiguration{Preparation: prepared.StartPreparationID, Components: map[string]lifecycle.ComponentConfig{"backend": {Values: map[string]json.RawMessage{"marker": json.RawMessage(`"owner-input"`)}}}}}
	if response := call(q); string(response["ok"]) != "true" {
		t.Fatal(response)
	}
	run("start", "start", prepared.Generation, prepared.StartPreparationID)
	current := r.lifecycle.Snapshot().Instances[prepared.ID]
	if current.State != "ready" {
		t.Fatal(current.State)
	}
	call(lifecycle.Command{Method: "check_instance", Instance: current.ID, Revision: digest, Generation: current.Generation})
	status := call(lifecycle.Command{Method: "status"})
	if string(status["app_config_protocol"]) != "1" {
		t.Fatal("missing protocol advertisement")
	}
	run("stop", "stop", current.Generation, "")
}
