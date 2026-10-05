package lifecycle

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testDependencyGrant(m *Manager, in *Instance) AppDependencyGrant {
	token := strings.Repeat("a", 64)
	id := sha256.Sum256([]byte(token))
	provider := apptransport.Binding{Fleet: m.owner, Node: "provider-node", Instance: strings.Repeat("b", 32), Revision: strings.Repeat("c", 64), Generation: 9, Component: "backend", Port: "http"}
	prefix := sha256.Sum256([]byte(provider.Instance + ":backend:http:9"))
	return AppDependencyGrant{Endpoint: fmt.Sprintf("https://%x.apps.test/rpc", prefix[:16]), Token: token, ID: hex.EncodeToString(id[:]), Expires: time.Now().Add(time.Minute).Unix(), Consumer: apptransport.InstanceIdentity{Fleet: m.owner, Node: m.node, Instance: in.ID, Revision: in.Digest, Generation: in.Generation + 1}, Provider: provider}
}
func dependencyConfig(m *Manager, in *Instance, cfg AppConfiguration) AppConfiguration {
	c := cfg.Components["backend"]
	c.Credentials = nil
	c.Dependencies = map[string]AppDependencyGrant{"provider": testDependencyGrant(m, in)}
	cfg.Components["backend"] = c
	return cfg
}
func TestLocalDependencyConfigRequiresTrustedOptIn(t *testing.T) {
	m, _, _ := setup(t)
	in, cfg := prepareConfigured(t, m, configDefinition(), nil, "local-rpc")
	cfg = dependencyConfig(m, in, cfg)
	grant := cfg.Components["backend"].Dependencies["provider"]
	grant.Endpoint = "https://127.0.0.1:19123/rpc"
	cfg.Components["backend"].Dependencies["provider"] = grant
	if m.ConfigureApp(in.ID, in.Digest, in.Generation, cfg) == nil {
		t.Fatal("implicit localhost exception")
	}
	for _, origin := range []string{"http://127.0.0.1:19123", "https://localhost:19123", "https://127.0.0.1:019123", "https://127.0.0.1:19123/", "https://127.0.0.1:65536"} {
		if m.SetLocalDependencyRPC(origin) == nil {
			t.Fatal("invalid origin", origin)
		}
	}
	if err := m.SetLocalDependencyRPC("https://127.0.0.1:19123"); err != nil {
		t.Fatal(err)
	}
	if m.SetLocalDependencyRPC("https://127.0.0.1:19124") == nil {
		t.Fatal("changed fixed authority")
	}
	for _, endpoint := range []string{"https://127.0.0.1:19124/rpc", "https://127.0.0.1:19123/rpc?", "https://127.0.0.1:19123/rpc#"} {
		bad := grant
		bad.Endpoint = endpoint
		cfg.Components["backend"].Dependencies["provider"] = bad
		if m.ConfigureApp(in.ID, in.Digest, in.Generation, cfg) == nil {
			t.Fatal("wrong origin accepted", endpoint)
		}
	}
	cfg.Components["backend"].Dependencies["provider"] = grant
	configureForTest(t, m, in, cfg)
	if op := startConfigured(t, m, in, "local-start"); op.State != "succeeded" {
		t.Fatal(op)
	}
	running := m.Snapshot().Instances[in.ID]
	raw, err := os.ReadFile(m.boundComponent(configDefinition().Components[0], running).appConfigPath)
	if err != nil || !strings.Contains(string(raw), grant.Endpoint) {
		t.Fatal("local credential not materialized", err)
	}
}
func TestDependencyConfigPrivateGenerationAndCleanup(t *testing.T) {
	m, _, _ := setup(t)
	in, cfg := prepareConfigured(t, m, configDefinition(), nil, "dependency")
	cfg = dependencyConfig(m, in, cfg)
	configureForTest(t, m, in, cfg)
	configureForTest(t, m, in, cfg)
	if m.ledger.Protocol != 7 {
		t.Fatal("missing downgrade fence")
	}
	if op := startConfigured(t, m, in, "dependency-start"); op.State != "succeeded" {
		t.Fatal(op)
	}
	running := m.Snapshot().Instances[in.ID]
	path := m.boundComponent(configDefinition().Components[0], running).appConfigPath
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var resolved resolvedAppConfig
	if json.Unmarshal(raw, &resolved) != nil || resolved.Credentials["provider"].Key != cfg.Components["backend"].Dependencies["provider"].Token {
		t.Fatal("dependency credential not materialized")
	}
	ledger, _ := json.Marshal(m.Snapshot())
	disk, _ := os.ReadFile(filepath.Join(m.root, "ledger.json"))
	for _, b := range [][]byte{ledger, disk} {
		if strings.Contains(string(b), cfg.Components["backend"].Dependencies["provider"].Token) || strings.Contains(string(b), "apps.test") {
			t.Fatal("secret leaked to ledger")
		}
	}
	if op := submit(t, m, in.Digest, "dependency-stop", "stop", in.Scope, running.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	assertConfigRemoved(t, m, in, running.Generation)
}
func TestDependencyConfigRejectsWrongOrExpiredGrant(t *testing.T) {
	mutations := map[string]func(*AppDependencyGrant){
		"old-generation":   func(g *AppDependencyGrant) { g.Consumer.Generation-- },
		"another-consumer": func(g *AppDependencyGrant) { g.Consumer.Instance = "other" },
		"another-node":     func(g *AppDependencyGrant) { g.Consumer.Node = "other" },
		"another-owner":    func(g *AppDependencyGrant) { g.Consumer.Fleet = "other" },
		"another-revision": func(g *AppDependencyGrant) { g.Consumer.Revision = strings.Repeat("d", 64) },
		"provider-owner":   func(g *AppDependencyGrant) { g.Provider.Fleet = "other" },
		"expired":          func(g *AppDependencyGrant) { g.Expires = time.Now().Add(-time.Second).Unix() },
		"long-lived":       func(g *AppDependencyGrant) { g.Expires = time.Now().Add(time.Hour).Unix() },
		"mismatched-key":   func(g *AppDependencyGrant) { g.Token = strings.Repeat("f", 64) },
		"other-endpoint":   func(g *AppDependencyGrant) { g.Endpoint = "https://evil.test/rpc" },
		"query":            func(g *AppDependencyGrant) { g.Endpoint += "?" },
		"path":             func(g *AppDependencyGrant) { g.Endpoint += "/other" },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			m, driver, _ := setup(t)
			in, cfg := prepareConfigured(t, m, configDefinition(), nil, "bad-grant")
			cfg = dependencyConfig(m, in, cfg)
			grant := cfg.Components["backend"].Dependencies["provider"]
			mutate(&grant)
			cfg.Components["backend"].Dependencies["provider"] = grant
			if m.ConfigureApp(in.ID, in.Digest, in.Generation, cfg) == nil {
				t.Fatal("accepted invalid grant")
			}
			if driver.starts != 0 {
				t.Fatal("invalid grant started process")
			}
		})
	}
}
func TestDependencyConfigExpiryRecheckedBeforeStart(t *testing.T) {
	m, driver, _ := setup(t)
	in, cfg := prepareConfigured(t, m, configDefinition(), nil, "expiry")
	cfg = dependencyConfig(m, in, cfg)
	configureForTest(t, m, in, cfg)
	// Simulate a persisted grant expiring while the node was unavailable. Avoid
	// wall-clock sleeps by replacing the private fixture's expiry in place.
	path := filepath.Join(m.appConfigRoot(), m.appConfigName(in, in.Generation+1, "source"))
	raw, _ := os.ReadFile(path)
	var record appConfigRecord
	_ = json.Unmarshal(raw, &record)
	g := record.Configuration.Components["backend"].Dependencies["provider"]
	g.Expires = time.Now().Add(-time.Second).Unix()
	record.Configuration.Components["backend"].Dependencies["provider"] = g
	raw, _ = json.Marshal(record)
	_ = os.Chmod(path, 0600)
	if err := os.WriteFile(path, raw, 0400); err != nil {
		t.Fatal(err)
	}
	if op := startConfigured(t, m, in, "expired-start"); op.State != "failed" || driver.starts != 0 {
		t.Fatal("expired grant consumed preparation", op)
	}
	if current := m.Snapshot().Instances[in.ID]; current.Generation != in.Generation || current.State != "prepared" {
		t.Fatal("preparation lost")
	}
}
func TestDependencyConfigRejectsCredentialCollision(t *testing.T) {
	m, _, _ := setup(t)
	in, cfg := prepareConfigured(t, m, configDefinition(), nil, "collision")
	c := cfg.Components["backend"]
	c.Dependencies = map[string]AppDependencyGrant{"provider": testDependencyGrant(m, in)}
	cfg.Components["backend"] = c
	if m.ConfigureApp(in.ID, in.Digest, in.Generation, cfg) == nil {
		t.Fatal("accepted two credential sources")
	}
}
func TestInstalledManifestIsVerifiedArtifactNotExtractedFile(t *testing.T) {
	m, _, _ := setup(t)
	def := definition()
	files := map[string]string{"app.json": `{"apiVersion":2,"id":"example","version":"1.0.0","provides":{"interfaces":[{"name":"fs","version":1,"tools":[]}]}}`}
	b, digest := bundle(t, def, files)
	_, _ = m.Stage(digest, 0, b)
	if _, err := m.InstalledManifest(digest); err == nil {
		t.Fatal("uninstalled artifact exposed")
	}
	if op := submit(t, m, digest, "manifest-install", "install", "app", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	extracted := filepath.Join(m.paths(digest, "app").Package, "app.json")
	_ = os.Chmod(extracted, 0600)
	if err := os.WriteFile(extracted, []byte(`{"id":"changed"}`), 0600); err != nil {
		t.Fatal(err)
	}
	result, err := m.Dispatch(context.Background(), Command{Protocol: 1, Method: "app_manifest", Revision: digest})
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(result)
	if !strings.Contains(string(encoded), `"name":"fs"`) || strings.Contains(string(encoded), "changed") {
		t.Fatal("read mutable manifest")
	}
	archive := filepath.Join(m.root, "artifacts", digest+".tar")
	if err := os.WriteFile(archive, []byte("corrupted"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := m.InstalledManifest(digest); err == nil {
		t.Fatal("unverified artifact admitted")
	}
}
func TestInstalledManifestRejectsIdentityMismatch(t *testing.T) {
	for _, manifest := range []string{"", `{"id":"other","version":"1.0.0"}`, `{"id":"example","version":"2.0.0"}`} {
		m, _, _ := setup(t)
		files := map[string]string{}
		if manifest != "" {
			files["app.json"] = manifest
		}
		b, d := bundle(t, definition(), files)
		_, _ = m.Stage(d, 0, b)
		if op := submit(t, m, d, "install-manifest", "install", "app", 0); op.State != "succeeded" {
			t.Fatal(op)
		}
		if _, err := m.InstalledManifest(d); err == nil {
			t.Fatal("missing/mismatched manifest admitted")
		}
	}
}
