package lifecycle

// App configuration is an owner-authorized input to one prepared start, not a
// manifest environment override. Neither references nor resolved credentials
// appear in the public lifecycle ledger. Native Apps still share their OS user's
// trust boundary; private files are not a sandbox against same-user processes.
import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
)

const appConfigContainerPath = "/run/pantheon/app-config.json"
const maxAppConfig = 64 << 10
const maxResolvedAppConfig = 256 << 10

type ConfigField struct {
	Required bool `json:"required,omitempty"`
}

type ConfigDeclaration struct {
	Values      map[string]ConfigField `json:"values,omitempty"`
	Credentials map[string]ConfigField `json:"credentials,omitempty"`
}

type AppCredentialRef struct {
	Ref      string `json:"ref"`
	Endpoint string `json:"endpoint"`
}

type ComponentConfig struct {
	Values       map[string]json.RawMessage    `json:"values,omitempty"`
	Credentials  map[string]AppCredentialRef   `json:"credentials,omitempty"`
	Dependencies map[string]AppDependencyGrant `json:"dependencies,omitempty"`
}

type AppConfiguration struct {
	Preparation string                     `json:"preparation_id"`
	Components  map[string]ComponentConfig `json:"components"`
}

type appConfigRecord struct {
	Protocol      int              `json:"protocol"`
	Owner         string           `json:"owner"`
	Node          string           `json:"node_id"`
	Instance      string           `json:"instance_id"`
	Revision      string           `json:"revision"`
	Generation    uint64           `json:"generation"` // the upcoming running generation
	Configuration AppConfiguration `json:"configuration"`
}

type resolvedAppCredential struct {
	Endpoint string `json:"endpoint"`
	Key      string `json:"key"`
}

type resolvedAppConfig struct {
	Protocol    int                              `json:"protocol"`
	Owner       string                           `json:"owner"`
	Node        string                           `json:"node_id"`
	Instance    string                           `json:"instance_id"`
	Revision    string                           `json:"revision"`
	Generation  uint64                           `json:"generation"`
	Component   string                           `json:"component"`
	Values      map[string]json.RawMessage       `json:"values"`
	Credentials map[string]resolvedAppCredential `json:"credentials"`
}

func consumesAppConfig(d Definition) bool {
	for _, c := range d.Components {
		if c.Configuration != nil {
			return true
		}
	}
	return false
}

func validateConfigDeclaration(c Component) error {
	if _, ok := c.Env["PANTHEON_APP_CONFIG"]; ok {
		return fmt.Errorf("App configuration environment is Runner-owned")
	}
	if c.Configuration == nil {
		return nil
	}
	if len(c.Configuration.Values)+len(c.Configuration.Credentials) == 0 {
		return fmt.Errorf("App configuration declaration must name its inputs")
	}
	for _, fields := range []map[string]ConfigField{c.Configuration.Values, c.Configuration.Credentials} {
		if len(fields) > 16 {
			return fmt.Errorf("too many App configuration fields")
		}
		for name := range fields {
			if !nameRE.MatchString(name) {
				return fmt.Errorf("invalid App configuration field name")
			}
		}
	}
	// The host file is readable by the Runner UID only. A container must use
	// that same identity instead of relying on root or broadening file access.
	if c.Runtime == "container" && !c.RunAsOwner {
		return fmt.Errorf("configured containers must run as owner")
	}
	for _, mounts := range []map[string]string{c.Mounts, c.ReadOnlyMounts} {
		for _, target := range mounts {
			if target == appConfigContainerPath || strings.HasPrefix(target, appConfigContainerPath+"/") || strings.HasPrefix(appConfigContainerPath, strings.TrimRight(target, "/")+"/") {
				return fmt.Errorf("mount overlaps App configuration")
			}
		}
	}
	return nil
}

func validateAppConfig(def Definition, cfg AppConfiguration) error {
	raw, err := json.Marshal(cfg)
	if err != nil || len(raw) > maxAppConfig || !nameRE.MatchString(cfg.Preparation) {
		return fmt.Errorf("invalid or oversized App configuration")
	}
	count := 0
	for _, component := range def.Components {
		decl := component.Configuration
		if decl == nil {
			continue
		}
		count++
		value, ok := cfg.Components[component.Name]
		if !ok {
			return fmt.Errorf("missing declared component configuration")
		}
		for name := range value.Values {
			if _, ok := decl.Values[name]; !ok {
				return fmt.Errorf("undeclared App configuration value")
			}
		}
		for name, field := range decl.Values {
			raw, ok := value.Values[name]
			if (field.Required && (!ok || bytes.Equal(bytes.TrimSpace(raw), []byte("null")))) || (ok && !json.Valid(raw)) {
				return fmt.Errorf("missing or invalid App configuration value")
			}
		}
		for name, ref := range value.Credentials {
			if _, ok := decl.Credentials[name]; !ok {
				return fmt.Errorf("undeclared App credential")
			}
			if _, err := modelcredentials.Name(ref.Ref); err != nil {
				return fmt.Errorf("invalid App credential reference")
			}
			if _, err := modelcredentials.Endpoint(ref.Endpoint); err != nil {
				return fmt.Errorf("invalid App credential endpoint")
			}
		}
		for name, field := range decl.Credentials {
			_, local := value.Credentials[name]
			_, dependency := value.Dependencies[name]
			if local && dependency {
				return fmt.Errorf("App credential has multiple sources")
			}
			if !local && !dependency && field.Required {
				return fmt.Errorf("missing required App credential")
			}
		}
		for name, grant := range value.Dependencies {
			if _, ok := decl.Credentials[name]; !ok || grant.validate() != nil {
				return fmt.Errorf("invalid or undeclared App dependency credential")
			}
		}
	}
	if count == 0 || count != len(cfg.Components) {
		return fmt.Errorf("configuration does not match the installed components")
	}
	return nil
}

func (m *Manager) appConfigRoot() string {
	p, _ := filepath.Abs(filepath.Join(m.root, "app-configuration"))
	return p
}

func (m *Manager) appConfigName(in *Instance, generation uint64, suffix string) string {
	sum := sha256.Sum256(fmt.Appendf(nil, "%s\x00%s\x00%s\x00%s\x00%d", m.owner, m.node, in.ID, in.Digest, generation))
	return hex.EncodeToString(sum[:]) + "-" + suffix + ".json"
}

func openAppConfigRoot(path string, create bool) (*os.Root, error) {
	if create {
		if err := makeConfigDirectory(path); err != nil {
			return nil, fmt.Errorf("cannot prepare private App configuration directory")
		}
	}
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || checkConfigPrivate(path, info) != nil {
		return nil, fmt.Errorf("App configuration directory is not private")
	}
	return os.OpenRoot(path)
}

func readAppConfigFile(root *os.Root, name string) ([]byte, error) {
	info, err := root.Lstat(name)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > maxResolvedAppConfig || checkConfigPrivate(filepath.Join(root.Name(), name), info) != nil {
		return nil, fmt.Errorf("invalid private App configuration file")
	}
	f, err := root.Open(name)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, maxResolvedAppConfig+1))
	if err != nil || len(b) > maxResolvedAppConfig {
		return nil, fmt.Errorf("cannot read bounded App configuration")
	}
	return b, nil
}

// An acknowledgement may be lost. Exact retries succeed; an existing record
// is never rewritten. A partial/corrupt record requires cancelling preparation.
func putAppConfigFile(root *os.Root, name string, value []byte) error {
	previous, err := readAppConfigFile(root, name)
	defer clear(previous)
	if err == nil {
		if bytes.Equal(previous, value) {
			return nil
		}
		return fmt.Errorf("App configuration is immutable; cancel and prepare a new start to change it")
	}
	if !os.IsNotExist(err) {
		return fmt.Errorf("cannot reuse existing App configuration")
	}
	f, err := root.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0400)
	if err != nil {
		return fmt.Errorf("cannot create private App configuration")
	}
	_, err = f.Write(value)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil || closeErr != nil {
		return fmt.Errorf("App configuration write incomplete; cancel and prepare a new start")
	}
	return nil
}

// ConfigureApp is called only by the owner-authenticated lifecycle control
// plane, never by the component's RPC token. Authorization is not a usage lease.
func (m *Manager) ConfigureApp(instance, revision string, generation uint64, cfg AppConfiguration) error {
	if !m.serial.TryLock() {
		return fmt.Errorf("node lifecycle is busy; retry the same configuration")
	}
	defer m.serial.Unlock()
	m.mu.Lock()
	defer m.mu.Unlock()
	in := m.ledger.Instances[instance]
	if m.closed || in == nil || in.Digest != revision || in.Generation != generation || in.State != "prepared" || generation >= 1<<63-1 || cfg.Preparation != in.StartPreparationID {
		return fmt.Errorf("App configuration requires the exact current start preparation")
	}
	install := m.ledger.Installations[revision]
	if install == nil || install.State != "installed" {
		return fmt.Errorf("App configuration requires an installed artifact")
	}
	if err := checkPreparedReservations(in, install.Definition); err != nil {
		return err
	}
	if err := validateAppConfig(install.Definition, cfg); err != nil {
		return err
	}
	if err := m.validateDependencyConsumers(in, cfg); err != nil {
		return err
	}
	// Fence older Runners before writing a private configuration they cannot
	// interpret. Keep the fence in memory on persistence failure and retry the
	// durable write; never acknowledge configuration after an uncertain fence.
	if hasDependencyGrants(cfg) {
		if m.ledger.Protocol < 7 {
			m.ledger.Protocol = 7
		}
		if err := m.persist(); err != nil {
			return fmt.Errorf("cannot persist App dependency configuration fence")
		}
	}
	record := appConfigRecord{1, m.owner, m.node, instance, revision, generation + 1, cfg}
	raw, err := json.Marshal(record)
	defer clear(raw)
	if err != nil {
		return fmt.Errorf("cannot encode App configuration")
	}
	root, err := openAppConfigRoot(m.appConfigRoot(), true)
	if err != nil {
		return err
	}
	defer root.Close()
	return putAppConfigFile(root, m.appConfigName(in, generation+1, "source"), raw)
}

// Before hooks/processes or consuming the preparation. Resolve every reference
// first so a missing credential cannot start a subset of configured components.
func (m *Manager) materializeAppConfig(def Definition, in *Instance) error {
	if !consumesAppConfig(def) {
		return nil
	}
	if in == nil || in.State != "prepared" || in.Generation >= 1<<63-1 {
		return fmt.Errorf("configured App requires a prepared start")
	}
	root, err := openAppConfigRoot(m.appConfigRoot(), false)
	if err != nil {
		return fmt.Errorf("App configuration is unavailable; configure the prepared start")
	}
	defer root.Close()
	raw, err := readAppConfigFile(root, m.appConfigName(in, in.Generation+1, "source"))
	defer clear(raw)
	var record appConfigRecord
	if err != nil || StrictDecode(raw, &record) != nil || record.Protocol != 1 || record.Owner != m.owner || record.Node != m.node || record.Instance != in.ID || record.Revision != in.Digest || record.Generation != in.Generation+1 || record.Configuration.Preparation != in.StartPreparationID {
		return fmt.Errorf("App configuration is missing or does not match this preparation")
	}
	if err := validateAppConfig(def, record.Configuration); err != nil {
		return err
	}
	if err := m.validateDependencyConsumers(in, record.Configuration); err != nil {
		return err
	}
	files := map[string][]byte{}
	defer func() {
		for _, data := range files {
			clear(data)
		}
	}()
	for name, config := range record.Configuration.Components {
		resolved := resolvedAppConfig{1, m.owner, m.node, in.ID, in.Digest, in.Generation + 1, name, config.Values, map[string]resolvedAppCredential{}}
		if resolved.Values == nil {
			resolved.Values = map[string]json.RawMessage{}
		}
		for alias, ref := range config.Credentials {
			key, err := modelcredentials.Read(filepath.Join(m.root, "model-credentials"), ref.Ref, ref.Endpoint)
			if err != nil {
				return fmt.Errorf("App credential unavailable or not authorized for its endpoint")
			}
			endpoint, _ := modelcredentials.Endpoint(ref.Endpoint)
			resolved.Credentials[alias] = resolvedAppCredential{endpoint, key}
		}
		for alias, grant := range config.Dependencies {
			resolved.Credentials[alias] = resolvedAppCredential{grant.Endpoint, grant.Token}
		}
		b, err := json.Marshal(resolved)
		if err != nil || len(b) > maxResolvedAppConfig {
			return fmt.Errorf("resolved App configuration exceeds limit")
		}
		files[m.appConfigName(in, in.Generation+1, "component-"+name)] = b
	}
	for name, b := range files {
		if err := putAppConfigFile(root, name, b); err != nil {
			return err
		}
	}
	return nil
}

// Called only for cancelled preparations or after owned processes are gone.
func (m *Manager) clearAppConfig(in *Instance, generation uint64) error {
	install := m.ledger.Installations[in.Digest]
	if install == nil || !consumesAppConfig(install.Definition) {
		return nil
	}
	root, err := openAppConfigRoot(m.appConfigRoot(), false)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	defer root.Close()
	names := []string{m.appConfigName(in, generation, "source")}
	for _, c := range install.Definition.Components {
		if c.Configuration != nil {
			names = append(names, m.appConfigName(in, generation, "component-"+c.Name))
		}
	}
	for _, name := range names {
		if err := root.Remove(name); err != nil && !errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("cannot remove stopped App configuration")
		}
	}
	return nil
}

func appConfigMount(c Component) ([]string, error) {
	if c.Configuration == nil {
		return nil, nil
	}
	if c.appConfigPath == "" || strings.ContainsAny(c.appConfigPath, ",\r\n") {
		return nil, fmt.Errorf("App configuration is not bound")
	}
	return []string{"--mount", "type=bind,src=" + c.appConfigPath + ",dst=" + appConfigContainerPath + ",readonly"}, nil
}
