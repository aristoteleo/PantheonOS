package lifecycle

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Release updates install every changed App again; without collection each
// update leaves its predecessor's package and Python environment on the node
// until the disk is full. After a start, stop or recovery succeeds, installations no instance
// runs are removed, except the newest stopped revision of each App scope (the
// data source and rollback target) and anything operated on recently, which
// may be an install still waiting for its start. Under disk pressure (an
// install or data copy failed for want of space) the quiet period is skipped:
// waiting would only repeat the failure.
const collectQuiet = 30 * time.Minute

func (m *Manager) collectSuperseded(pressure bool) {
	l := m.Snapshot()
	last := map[string]time.Time{}
	for _, op := range l.Operations {
		// Removing a revision does not make it recent: counting collection's
		// own uninstalls would make a removed revision the "newest stopped"
		// one and collect the real data source next.
		if op.Request.Action == "uninstall" {
			continue
		}
		if op.CreatedAt.After(last[op.Request.Digest]) {
			last[op.Request.Digest] = op.CreatedAt
		}
	}
	active, scope := map[string]bool{}, map[string]string{}
	for _, in := range l.Instances {
		scope[in.Digest] = in.Scope
		if in.State != "stopped" || len(in.Resources) > 0 || len(in.Reservations) > 0 {
			active[in.Digest] = true
		}
	}
	newest := map[string]string{} // app/scope -> newest stopped digest
	for _, in := range l.Instances {
		if active[in.Digest] {
			continue
		}
		key := in.AppID + "/" + in.Scope
		if d, ok := newest[key]; !ok || last[in.Digest].After(last[d]) {
			newest[key] = in.Digest
		}
	}
	keep := map[string]bool{}
	for _, d := range newest {
		keep[d] = true
	}
	quiet := m.collectQuiet
	if quiet == 0 {
		quiet = collectQuiet
	}
	for d, inst := range l.Installations {
		if inst.State != "installed" || active[d] || keep[d] || scope[d] == "" || (!pressure && time.Since(last[d]) < quiet) {
			continue
		}
		id := fmt.Sprintf("gc-%s-%d", d[:16], last[d].Unix())
		if _, err := m.Submit(Request{Protocol: Protocol, OperationID: id, Action: "uninstall", Digest: d, Scope: scope[d]}); err != nil {
			fmt.Fprintf(os.Stderr, "fleet: cannot collect installation %s: %v\n", d[:12], err)
		}
	}
}

// boundEnvironment is the node-cached Python environment an installation was
// bound to, or "" when it has none or the environment lives elsewhere (an
// operator-configured or node-local temporary cache).
func (m *Manager) boundEnvironment(install string) (root, key string) {
	var binding struct{ Key, Python string }
	raw, err := os.ReadFile(filepath.Join(install, "python-environment.json"))
	if err != nil || json.Unmarshal(raw, &binding) != nil || binding.Key == "" {
		return "", ""
	}
	root = filepath.Join(m.root, "python-environments", binding.Key)
	if filepath.Dir(filepath.Dir(filepath.Clean(binding.Python))) != root {
		return "", ""
	}
	return root, binding.Key
}

// environmentInUse reports whether another installation is bound to key.
func (m *Manager) environmentInUse(key string) bool {
	bindings, _ := filepath.Glob(filepath.Join(m.root, "installations", "*", "python-environment.json"))
	for _, path := range bindings {
		if _, other := m.boundEnvironment(filepath.Dir(path)); other == key {
			return true
		}
	}
	return false
}
