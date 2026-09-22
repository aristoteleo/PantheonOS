package lifecycle

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestRPCCredentialBoundToInstanceGenerationAndNotPublished(t *testing.T) {
	m, driver, in := readyResourceInstance(t)
	token, err := m.RPCCredential(in.ID, in.Digest, in.Generation)
	if err != nil || len(token) != 64 {
		t.Fatal(err)
	}
	b, _ := json.Marshal(m.Snapshot())
	if strings.Contains(string(b), token) {
		t.Fatal("management credential exposed in public ledger")
	}
	if _, err := m.RPCCredential(in.ID, in.Digest, in.Generation+1); err == nil {
		t.Fatal("stale RPC identity accepted")
	}
	if err := m.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	recovered, err := reopened.RPCCredential(in.ID, in.Digest, in.Generation)
	if err != nil || recovered != token {
		t.Fatal("restart invalidated live component credential", err)
	}
	if op := submit(t, reopened, in.Digest, "recover", "reconcile", in.Scope, in.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, reopened, in.Digest, "stop-auth", "stop", in.Scope, in.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, reopened, in.Digest, "start-auth", "start", in.Scope, in.Generation+1); op.State != "succeeded" {
		t.Fatal(op)
	}
	newInstance := reopened.Snapshot().Instances[in.ID]
	newToken, err := reopened.RPCCredential(in.ID, in.Digest, newInstance.Generation)
	if err != nil || newToken == token {
		t.Fatal("new generation reused old management credential", err)
	}
}
