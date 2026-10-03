package jobapp

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// The real job HTTP control transport, lifecycle and native child. This does
// not allocate a scheduler job or provision credentials on a remote cluster.
func TestConfiguredAppWaitsForOwnerInJob(t *testing.T) {
	payload, _ := fixtureArtifact(t)
	reader := tar.NewReader(bytes.NewReader(payload))
	var buffer bytes.Buffer
	writer := tar.NewWriter(&buffer)
	for {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		body, err := io.ReadAll(reader)
		if err != nil {
			t.Fatal(err)
		}
		if header.Name == "fleet.json" {
			var def lifecycle.Definition
			if err := json.Unmarshal(body, &def); err != nil {
				t.Fatal(err)
			}
			def.Requires = lifecycle.Requirements{}
			def.Components[0].Configuration = &lifecycle.ConfigDeclaration{Values: map[string]lifecycle.ConfigField{"marker": {Required: true}}}
			body, _ = json.Marshal(def)
		}
		if header.Name == "server.py" {
			body = []byte(strings.Replace(string(body), "import json,os", `import json,os
cfg = json.load(open(os.environ["PANTHEON_APP_CONFIG"]))
assert cfg["values"]["marker"] == "job-owner-config"
assert cfg["instance_id"] == os.environ["PANTHEON_INSTANCE_ID"]
assert str(cfg["generation"]) == os.environ["PANTHEON_INSTANCE_GENERATION"]`, 1))
		}
		header.Size = int64(len(body))
		if err := writer.WriteHeader(header); err != nil {
			t.Fatal(err)
		}
		if _, err := writer.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	payload = buffer.Bytes()
	sum := sha256.Sum256(payload)
	digest := hex.EncodeToString(sum[:])
	m, err := lifecycle.Open(t.TempDir(), "owner", "job-node", proto.Capability{}, lifecycle.NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	defer func() {
		for _, in := range m.Snapshot().Instances {
			for _, r := range in.Resources {
				if err := (lifecycle.NativeDriver{}).Stop(context.Background(), lifecycle.Component{StopSeconds: 1}, r); err != nil {
					t.Error(err)
				}
			}
		}
	}()
	if _, err := m.Stage(digest, 0, payload); err != nil {
		t.Fatal(err)
	}
	handler, err := New(m, strings.Repeat("a", 64))
	if err != nil {
		t.Fatal(err)
	}
	defer handler.Close()
	server := httptest.NewServer(handler)
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	id, err := StartInitial(ctx, m, digest, "app")
	if err != nil {
		t.Fatal(err)
	}
	in := m.Snapshot().Instances[id]
	if in.State != "prepared" || len(in.Resources) != 0 {
		t.Fatal("job started without owner configuration")
	}
	q := lifecycle.Command{Protocol: 1, Method: "configure", Instance: id, Revision: digest, Generation: in.Generation,
		Configuration: &lifecycle.AppConfiguration{Preparation: in.StartPreparationID, Components: map[string]lifecycle.ComponentConfig{
			"backend": {Values: map[string]json.RawMessage{"marker": json.RawMessage(`"job-owner-config"`)}},
		}}}
	call := func(command lifecycle.Command, token string) map[string]json.RawMessage {
		t.Helper()
		body, _ := json.Marshal(command)
		req, _ := http.NewRequestWithContext(ctx, "POST", server.URL+"/control", bytes.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		if token != handler.Token {
			if response.StatusCode != 403 {
				t.Fatal("job accepted unauthorized configuration")
			}
			return nil
		}
		var result map[string]json.RawMessage
		if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
			t.Fatal(err)
		}
		if result["error"] != nil {
			t.Fatal(string(result["error"]))
		}
		return result
	}
	call(q, "not-the-job-owner")
	if result := call(q, handler.Token); string(result["ok"]) != "true" {
		t.Fatal(result)
	}
	call(q, handler.Token) // lost-ack retry
	manifestReply := call(lifecycle.Command{Protocol: 1, Method: "app_manifest", Revision: digest}, handler.Token)
	if !bytes.Contains(manifestReply["manifest"], []byte("ordinary-test")) {
		t.Fatal("job did not return installed manifest")
	}
	start := lifecycle.Request{Protocol: 1, OperationID: "configured-job-start", Action: "start", Digest: digest, Scope: "app", Generation: in.Generation, StartPreparationID: in.StartPreparationID}
	call(lifecycle.Command{Protocol: 1, Method: "submit", Request: &start}, handler.Token)
	for {
		op := m.Snapshot().Operations[start.OperationID]
		if op.State == "succeeded" {
			break
		}
		if op.State != "queued" && op.State != "running" {
			t.Fatal(op.Error)
		}
		select {
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-time.After(20 * time.Millisecond):
		}
	}
	if m.Snapshot().Instances[id].State != "ready" {
		t.Fatal("configured job did not become ready")
	}
	status := call(lifecycle.Command{Protocol: 1, Method: "status"}, handler.Token)
	if string(status["app_config_protocol"]) != "1" {
		t.Fatal("job cannot advertise configuration capability")
	}
	wire, _ := json.Marshal(status)
	if bytes.Contains(wire, []byte("job-owner-config")) {
		t.Fatal("job status exposed configuration")
	}
}
