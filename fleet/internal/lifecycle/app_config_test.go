package lifecycle

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func configDefinition() Definition {
	d := definition()
	d.Components[0].Configuration = &ConfigDeclaration{
		Values:      map[string]ConfigField{"route": {Required: true}},
		Credentials: map[string]ConfigField{"provider": {Required: true}},
	}
	return d
}

func prepareConfigured(t *testing.T, m *Manager, def Definition, files map[string]string, scope string) (*Instance, AppConfiguration) {
	t.Helper()
	b, digest := bundle(t, def, files)
	if _, err := m.Stage(digest, 0, b); err != nil {
		t.Fatal(err)
	}
	if op := submit(t, m, digest, "install-"+scope, "install", scope, 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, digest, "prepare-"+scope, "prepare_start", scope, 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	in := m.Snapshot().Instances[m.instanceID(digest, scope)]
	cfg := AppConfiguration{Preparation: in.StartPreparationID, Components: map[string]ComponentConfig{
		"backend": {Values: map[string]json.RawMessage{"route": json.RawMessage(`"private-route"`)}, Credentials: map[string]AppCredentialRef{"provider": {"node-secret://test-provider", "https://provider.example/v1"}}},
	}}
	return in, cfg
}

func configureForTest(t *testing.T, m *Manager, in *Instance, cfg AppConfiguration) {
	t.Helper()
	// Exercise the same envelope the job worker dispatches, not a separate HPC adapter.
	command := Command{Type: "app_lifecycle", Protocol: 1, Method: "configure", Instance: in.ID, Revision: in.Digest, Generation: in.Generation, Configuration: &cfg}
	wire, _ := json.Marshal(command)
	var decoded Command
	if err := StrictDecode(wire, &decoded); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Dispatch(context.Background(), decoded); err != nil {
		t.Fatal(err)
	}
}

func startConfigured(t *testing.T, m *Manager, in *Instance, id string) Operation {
	t.Helper()
	_, err := m.Submit(Request{Protocol: 1, OperationID: id, Action: "start", Digest: in.Digest, Scope: in.Scope, Generation: in.Generation, StartPreparationID: in.StartPreparationID})
	if err != nil {
		t.Fatal(err)
	}
	return wait(t, m, id)
}

func putTestCredential(t *testing.T, m *Manager) {
	t.Helper()
	if err := modelcredentials.Put(filepath.Join(m.root, "model-credentials"), "node-secret://test-provider", "https://provider.example/v1", "fixture-secret-123", false); err != nil {
		t.Fatal(err)
	}
}

func TestAppConfigurationPreservesCredentialBaseURL(t *testing.T) {
	for _, endpoint := range []string{"https://provider.example", "https://provider.example/", "https://provider.example/v1", "https://provider.example/native-api", "nats://127.0.0.1:4222", "wss://bus.example/nats", "wss://bus.example/nats/"} {
		t.Run(endpoint, func(t *testing.T) {
			m, _, _ := setup(t)
			if err := modelcredentials.Put(filepath.Join(m.root, "model-credentials"), "node-secret://test-provider", endpoint, "fixture-secret-123", false); err != nil {
				t.Fatal(err)
			}
			in, cfg := prepareConfigured(t, m, configDefinition(), nil, "base-url")
			cfg.Components["backend"].Credentials["provider"] = AppCredentialRef{"node-secret://test-provider", endpoint}
			configureForTest(t, m, in, cfg)
			if op := startConfigured(t, m, in, "base-url-start"); op.State != "succeeded" {
				t.Fatal(op)
			}
			running := m.Snapshot().Instances[in.ID]
			bound := m.boundComponent(configDefinition().Components[0], running)
			raw, err := os.ReadFile(bound.appConfigPath)
			if err != nil {
				t.Fatal(err)
			}
			var resolved resolvedAppConfig
			if err := json.Unmarshal(raw, &resolved); err != nil {
				t.Fatal(err)
			}
			want := strings.TrimRight(endpoint, "/")
			if strings.HasPrefix(endpoint, "wss://") {
				want = endpoint
			}
			if got := resolved.Credentials["provider"]; got.Endpoint != want || got.Key != "fixture-secret-123" {
				t.Fatal("App configuration changed the provider's API base path")
			}
		})
	}
}

func TestCompleteToolSchemasSurvivePreparedConfiguration(t *testing.T) {
	m, _, _ := setup(t)
	putTestCredential(t, m)
	in, cfg := prepareConfigured(t, m, configDefinition(), nil, "complete-tools")
	value, _ := json.Marshal(map[string]string{"tool_description": strings.Repeat("schema", 14000)})
	cfg.Components["backend"].Values["route"] = value
	tooLarge := clone(cfg)
	tooLarge.Components["backend"].Values["route"] = json.RawMessage(`"` + strings.Repeat("x", maxAppConfig) + `"`)
	if err := m.ConfigureApp(in.ID, in.Digest, in.Generation, tooLarge); err == nil {
		t.Fatal("unbounded configuration accepted")
	}
	configureForTest(t, m, in, cfg)
	configureForTest(t, m, in, cfg) // Same configuration remains an idempotent retry.
	if op := startConfigured(t, m, in, "complete-tools-start"); op.State != "succeeded" {
		t.Fatal(op)
	}
	running := m.Snapshot().Instances[in.ID]
	bound := m.boundComponent(configDefinition().Components[0], running)
	raw, err := os.ReadFile(bound.appConfigPath)
	if err != nil {
		t.Fatal(err)
	}
	var resolved resolvedAppConfig
	if json.Unmarshal(raw, &resolved) != nil || string(resolved.Values["route"]) != string(value) {
		t.Fatal("complete tool schema was lost in prepared start")
	}
}

func assertConfigRemoved(t *testing.T, m *Manager, in *Instance, generation uint64) {
	t.Helper()
	for _, suffix := range []string{"source", "component-backend"} {
		if _, err := os.Lstat(filepath.Join(m.appConfigRoot(), m.appConfigName(in, generation, suffix))); !os.IsNotExist(err) {
			t.Fatalf("config retained: %s: %v", suffix, err)
		}
	}
}

func TestAppConfigurationLifecycle(t *testing.T) {
	m, driver, _ := setup(t)
	putTestCredential(t, m)
	m.SetResourceSampler(func() proto.ResourceInventory {
		t.Error("unbudgeted configuration sampled resources")
		return proto.ResourceInventory{}
	})
	in, cfg := prepareConfigured(t, m, configDefinition(), nil, "configured")
	if op := startConfigured(t, m, in, "without-config"); op.State != "failed" || driver.starts != 0 {
		t.Fatal("started without configuration", op)
	}
	configureForTest(t, m, in, cfg)
	configureForTest(t, m, in, cfg) // exact lost-ack retry
	changed := clone(cfg)
	changed.Components["backend"].Values["route"] = json.RawMessage(`"other"`)
	if err := m.ConfigureApp(in.ID, in.Digest, in.Generation, changed); err == nil {
		t.Fatal("changed immutable configuration")
	}
	wrongPreparation := clone(cfg)
	wrongPreparation.Preparation = "different"
	for _, attempt := range []struct {
		id, revision string
		generation   uint64
		config       AppConfiguration
	}{
		{in.ID, in.Digest, in.Generation + 1, cfg}, {"other-instance", in.Digest, in.Generation, cfg},
		{in.ID, strings.Repeat("f", 64), in.Generation, cfg}, {in.ID, in.Digest, in.Generation, wrongPreparation},
	} {
		if err := m.ConfigureApp(attempt.id, attempt.revision, attempt.generation, attempt.config); err == nil {
			t.Fatal("accepted stale or mismatched configuration")
		}
	}
	if op := startConfigured(t, m, in, "configured-start"); op.State != "succeeded" {
		t.Fatal(op)
	}
	running := m.Snapshot().Instances[in.ID]
	bound := m.boundComponent(configDefinition().Components[0], running)
	raw, err := os.ReadFile(bound.appConfigPath)
	if err != nil {
		t.Fatal(err)
	}
	var resolved resolvedAppConfig
	if json.Unmarshal(raw, &resolved) != nil || resolved.Generation != running.Generation || resolved.Credentials["provider"].Key != "fixture-secret-123" {
		t.Fatal("configuration was not resolved for running generation")
	}
	if err := m.ConfigureApp(in.ID, in.Digest, running.Generation, cfg); err == nil {
		t.Fatal("reconfigured live process")
	}
	ledger, _ := json.Marshal(m.Snapshot())
	disk, _ := os.ReadFile(filepath.Join(m.root, "ledger.json"))
	for _, b := range [][]byte{ledger, disk} {
		for _, secret := range []string{"fixture-secret-123", "private-route", "node-secret://test-provider", "provider.example"} {
			if strings.Contains(string(b), secret) {
				t.Fatal("configuration leaked into ledger")
			}
		}
	}
	if m.ledger.Protocol != 6 {
		t.Fatal("old Runner can downgrade configured deployment")
	}
	// A blocked drain retains both the process and its config.
	driver.blocked = true
	if op := submit(t, m, in.Digest, "blocked-stop", "stop", in.Scope, running.Generation); op.State != "failed" {
		t.Fatal(op)
	}
	if _, err := os.Stat(bound.appConfigPath); err != nil {
		t.Fatal("live configuration removed")
	}
	driver.blocked = false
	if op := submit(t, m, in.Digest, "configured-stop", "stop", in.Scope, running.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	assertConfigRemoved(t, m, in, running.Generation)
	stopped := m.Snapshot().Instances[in.ID]
	if op := submit(t, m, in.Digest, "no-reuse", "start", in.Scope, stopped.Generation); op.State != "failed" {
		t.Fatal("reused prior generation configuration")
	}
}

func TestAppConfigurationRejectedBeforeProcesses(t *testing.T) {
	for _, mode := range []string{"missing-key", "wrong-endpoint", "source-symlink", "source-corrupt", "resolved-corrupt"} {
		t.Run(mode, func(t *testing.T) {
			m, driver, _ := setup(t)
			in, cfg := prepareConfigured(t, m, configDefinition(), nil, "failure")
			if mode != "missing-key" {
				putTestCredential(t, m)
			}
			if mode == "wrong-endpoint" {
				cfg.Components["backend"].Credentials["provider"] = AppCredentialRef{"node-secret://test-provider", "https://wrong.example/v1"}
			}
			configureForTest(t, m, in, cfg)
			path := filepath.Join(m.appConfigRoot(), m.appConfigName(in, in.Generation+1, "source"))
			if mode == "source-corrupt" {
				os.Chmod(path, 0600)
				os.WriteFile(path, []byte(`{"credential":"sensitive-error-marker"}`), 0400)
			}
			if mode == "source-symlink" {
				if runtime.GOOS == "windows" {
					t.Skip("symlink privilege unavailable on Windows CI")
				}
				external := filepath.Join(t.TempDir(), "external")
				os.WriteFile(external, []byte("sensitive-error-marker"), 0600)
				os.Remove(path)
				if err := os.Symlink(external, path); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "resolved-corrupt" {
				if err := m.materializeAppConfig(configDefinition(), in); err != nil {
					t.Fatal(err)
				}
				path = filepath.Join(m.appConfigRoot(), m.appConfigName(in, in.Generation+1, "component-backend"))
				os.Chmod(path, 0600)
				os.WriteFile(path, []byte("sensitive-error-marker"), 0400)
			}
			op := startConfigured(t, m, in, "failed-start")
			if op.State != "failed" || driver.starts != 0 || strings.Contains(op.Error, "sensitive-error-marker") {
				t.Fatal(op)
			}
			if m.Snapshot().Instances[in.ID].State != "prepared" {
				t.Fatal("failed preflight consumed preparation")
			}
			if op := submit(t, m, in.Digest, "cancel", "stop", in.Scope, in.Generation); op.State != "succeeded" {
				t.Fatal(op)
			}
			assertConfigRemoved(t, m, in, in.Generation+1)
		})
	}
}

func TestAppConfigurationRestartAndReconcile(t *testing.T) {
	for _, start := range []bool{false, true} {
		t.Run(map[bool]string{false: "prepared", true: "running"}[start], func(t *testing.T) {
			m, driver, _ := setup(t)
			putTestCredential(t, m)
			in, cfg := prepareConfigured(t, m, configDefinition(), nil, "restart")
			configureForTest(t, m, in, cfg)
			if start {
				if op := startConfigured(t, m, in, "start-original"); op.State != "succeeded" {
					t.Fatal(op)
				}
			}
			m.Close()
			reopened, err := Open(m.root, m.owner, m.node, proto.Capability{}, driver)
			if err != nil {
				t.Fatal(err)
			}
			defer reopened.Close()
			if !start {
				configureForTest(t, reopened, in, cfg)
				if op := startConfigured(t, reopened, in, "start-reopened"); op.State != "succeeded" {
					t.Fatal(op)
				}
			}
			live := reopened.Snapshot().Instances[in.ID]
			path := filepath.Join(reopened.appConfigRoot(), reopened.appConfigName(in, live.Generation, "component-backend"))
			if _, err := os.Stat(path); err != nil {
				t.Fatal("restart lost runtime config")
			}
			// A confirmed dead process, including one lost during Runner downtime,
			// releases its config through the same reconciliation as other resources.
			driver.mu.Lock()
			for id := range driver.alive {
				driver.alive[id] = false
			}
			driver.mu.Unlock()
			if op := submit(t, reopened, in.Digest, "reconcile-dead", "reconcile", in.Scope, live.Generation); op.State != "succeeded" {
				t.Fatal(op)
			}
			assertConfigRemoved(t, reopened, in, live.Generation)
		})
	}
}

func TestAppConfigurationDeclarations(t *testing.T) {
	for _, mutate := range []func(*Component){
		func(c *Component) { c.Env = map[string]string{"PANTHEON_APP_CONFIG": "forged"} },
		func(c *Component) { c.Configuration.Values["../escape"] = ConfigField{} },
		func(c *Component) { c.Configuration = &ConfigDeclaration{} },
	} {
		d := configDefinition()
		mutate(&d.Components[0])
		if d.Validate() == nil {
			t.Fatal("accepted invalid declaration")
		}
	}
	m, _, _ := setup(t)
	in, cfg := prepareConfigured(t, m, configDefinition(), nil, "validation")
	for _, mutate := range []func(*AppConfiguration){
		func(c *AppConfiguration) { delete(c.Components["backend"].Values, "route") },
		func(c *AppConfiguration) { c.Components["backend"].Values["route"] = json.RawMessage("null") },
		func(c *AppConfiguration) { c.Components["backend"].Values["extra"] = json.RawMessage("true") },
		func(c *AppConfiguration) {
			c.Components["backend"].Values["route"] = json.RawMessage(`"` + strings.Repeat("x", maxAppConfig) + `"`)
		},
		func(c *AppConfiguration) { c.Components["other"] = ComponentConfig{} },
		func(c *AppConfiguration) {
			c.Components["backend"].Credentials["provider"] = AppCredentialRef{"file:///etc/passwd", "https://provider.example/v1"}
		},
	} {
		bad := clone(cfg)
		mutate(&bad)
		if m.ConfigureApp(in.ID, in.Digest, in.Generation, bad) == nil {
			t.Fatal("accepted undeclared or invalid input")
		}
	}
}

func TestAppConfigurationContainerMount(t *testing.T) {
	d := configDefinition()
	d.Dependencies.ContainerEngine = &EngineDependency{Provider: "docker", Provision: "never"}
	c := &d.Components[0]
	c.Runtime, c.Image, c.RunAsOwner = "container", "example@sha256:"+strings.Repeat("a", 64), true
	if err := d.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, target := range []string{"/run", "/run/pantheon", appConfigContainerPath, appConfigContainerPath + "/child"} {
		c.ReadOnlyMounts = map[string]string{"package": target}
		if d.Validate() == nil {
			t.Fatal("mount can shadow configuration", target)
		}
	}
	c.ReadOnlyMounts = nil
	c.RunAsOwner = false
	if d.Validate() == nil {
		t.Fatal("container cannot read owner-only config")
	}
	c.RunAsOwner = true
	m, _, _ := setup(t)
	in := &Instance{ID: "instance", Digest: strings.Repeat("a", 64), Generation: 2}
	bound := m.boundComponent(*c, in)
	args, err := appConfigMount(bound)
	if err != nil || len(args) != 2 || !strings.HasSuffix(args[1], ",dst="+appConfigContainerPath+",readonly") || bound.Env["PANTHEON_APP_CONFIG"] != appConfigContainerPath {
		t.Fatal(args, err)
	}
	if _, err := appConfigMount(*c); err == nil {
		t.Fatal("unbound container was accepted")
	}
}

func TestAppConfigurationNativeSDK(t *testing.T) {
	python := "python3"
	if runtime.GOOS == "windows" {
		python = "python"
	}
	if _, err := exec.LookPath(python); err != nil {
		t.Skip("Python required for real App SDK test")
	}
	sdk, err := os.ReadFile(filepath.Join("..", "..", "..", "pantheon", "apps", "runtime_config.py"))
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("OPENAI_API_KEY", "ambient-should-not-reach-app")
	t.Setenv("FLEET_KEY", "management-should-not-reach-app")
	t.Setenv("PANTHEON_APP_CONFIG", "/invalid-ambient-config")
	m, err := Open(t.TempDir(), "sdk-owner", "sdk-node", proto.Capability{}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		for _, in := range m.Snapshot().Instances {
			for _, r := range in.Resources {
				if err := (NativeDriver{}).Stop(context.Background(), Component{StopSeconds: 2}, r); err != nil {
					t.Error(err)
				}
			}
		}
		m.Close()
	})
	putTestCredential(t, m)
	d := configDefinition()
	d.Hooks = nil
	d.Components[0].Argv = []string{python, "reader.py", "${DATA}"}
	d.Components[0].Readiness = Probe{Argv: []string{python, "probe.py", "${DATA}/backend.json"}, TimeoutSeconds: 3}
	d.Components = append(d.Components, Component{Name: "observer", Runtime: "process", Argv: []string{python, "reader.py", "${DATA}"}, Readiness: Probe{Argv: []string{python, "probe.py", "${DATA}/observer.json"}, TimeoutSeconds: 3}})
	files := map[string]string{"runtime_config.py": string(sdk), "reader.py": `
import json, os, sys, time
from pathlib import Path
from runtime_config import load_runtime_configuration
cfg = load_runtime_configuration()
name = os.environ["PANTHEON_COMPONENT_NAME"]
if name == "backend":
    assert cfg.values["route"] == "private-route"
    assert cfg.credentials["provider"].key == "fixture-secret-123"
    assert cfg.credentials["provider"].endpoint == "https://provider.example"
    assert cfg.instance_id == os.environ["PANTHEON_INSTANCE_ID"]
else:
    assert cfg is None
assert "OPENAI_API_KEY" not in os.environ and "FLEET_KEY" not in os.environ
Path(sys.argv[1], name+".json").write_text(json.dumps({"component":name,"configured":cfg is not None}))
while True: time.sleep(1)
`, "probe.py": `import json, sys; assert json.load(open(sys.argv[1]))["component"]`}
	in, cfg := prepareConfigured(t, m, d, files, "native")
	cfg.Components["backend"].Credentials["provider"] = AppCredentialRef{"node-secret://test-provider", "https://provider.example"}
	configureForTest(t, m, in, cfg)
	if op := startConfigured(t, m, in, "native-start"); op.State != "succeeded" {
		t.Fatal(op)
	}
	running := m.Snapshot().Instances[in.ID]
	for _, name := range []string{"backend", "observer"} {
		b, err := os.ReadFile(filepath.Join(m.paths(in.Digest, in.Scope).Data, name+".json"))
		if err != nil || !strings.Contains(string(b), name) {
			t.Fatalf("real child did not report: %s %v", name, err)
		}
	}
	if op := submit(t, m, in.Digest, "native-stop", "stop", in.Scope, running.Generation); op.State != "succeeded" {
		t.Fatal(op)
	}
	assertConfigRemoved(t, m, in, running.Generation)
}
