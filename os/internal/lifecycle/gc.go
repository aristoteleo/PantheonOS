package lifecycle

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Release updates install every changed App again; without collection each
// update leaves its predecessor's package, Python environment, staged
// artifact and a copy of its data on the node until the disk is full. After a
// start, stop, recovery or uninstall succeeds:
//
//   - installations no instance runs are removed, except the newest stopped
//     revision of each App scope (the data source and rollback target);
//   - the data of stopped revisions older than the newest keepData that ran
//     in their App scope is removed with their instance record, once they are
//     no longer installed: a later start of such a revision copies the data
//     of the revision that ran last, as for a revision that never ran;
//   - staged artifacts of revisions that are not installed are removed; an
//     install stages its artifact again.
//
// Anything operated on (or staged) recently is left alone: it may be an
// install still waiting for its start. Under disk pressure (an install or
// data copy failed for want of space) the quiet period is skipped: waiting
// would only repeat the failure.
const collectQuiet = 30 * time.Minute

// keepData is how many stopped revisions of each App scope keep their data:
// the newest (also kept installed) and the one before it, e.g. a fork that
// ran in between two releases.
const keepData = 2

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
	m.collectLeftovers(pressure, quiet)
}

// collectLeftovers removes the data of stopped revisions beyond keepData per
// App scope and the staged artifacts of revisions that are not installed. It
// holds the Runner, so no operation starts while it decides.
func (m *Manager) collectLeftovers(pressure bool, quiet time.Duration) {
	m.serial.Lock()
	defer m.serial.Unlock()
	l := m.Snapshot()
	last, busy := map[string]time.Time{}, map[string]bool{}
	for _, op := range l.Operations {
		if op.State == "queued" || op.State == "running" {
			busy[op.Request.Digest] = true
			if op.Request.DataSource != nil {
				busy[op.Request.DataSource.Digest] = true
			}
		}
		if op.Request.Action != "uninstall" && op.CreatedAt.After(last[op.Request.Digest]) {
			last[op.Request.Digest] = op.CreatedAt
		}
	}
	installed := func(d string) bool { inst := l.Installations[d]; return inst != nil && inst.State != "absent" }
	settled := func(t time.Time) bool { return pressure || time.Since(t) >= quiet }

	active := map[string]bool{}
	for _, in := range l.Instances {
		if in.State != "stopped" || len(in.Resources) > 0 || len(in.Reservations) > 0 {
			active[in.Digest] = true
		}
	}
	forget := func(in *Instance) {
		d := in.Digest
		if active[d] || busy[d] || installed(d) || !settled(last[d]) {
			return
		}
		if err := os.RemoveAll(filepath.Join(m.root, "data", in.ID)); err != nil {
			fmt.Fprintf(os.Stderr, "fleet: cannot remove data of %s: %v\n", in.ID[:12], err)
			return
		}
		id := in.ID
		if err := m.update(func() { delete(m.ledger.Instances, id); delete(m.usage, id) }); err != nil {
			fmt.Fprintf(os.Stderr, "fleet: cannot forget instance %s: %v\n", id[:12], err)
		}
	}
	ran := map[string][]*Instance{} // app/scope -> stopped revisions that ran, newest first
	for _, in := range l.Instances {
		if active[in.Digest] {
			continue
		}
		if in.Generation == 0 {
			// Data copied for a revision that never started: it holds no
			// place among those kept, and goes once its install is gone (an
			// update that was abandoned).
			forget(in)
			continue
		}
		key := in.AppID + "/" + in.Scope
		ran[key] = append(ran[key], in)
	}
	for _, list := range ran {
		sort.Slice(list, func(i, j int) bool { return last[list[i].Digest].After(last[list[j].Digest]) })
		for _, in := range list[min(keepData, len(list)):] {
			forget(in)
		}
	}

	entries, _ := os.ReadDir(filepath.Join(m.root, "artifacts"))
	for _, entry := range entries {
		d, ok := strings.CutSuffix(entry.Name(), ".tar")
		if !ok || !digestRE.MatchString(d) || installed(d) || busy[d] || active[d] {
			continue
		}
		// A recent file may be an upload for an install that has not been
		// submitted yet.
		if info, err := entry.Info(); err != nil || !settled(info.ModTime()) {
			continue
		}
		if err := os.Remove(filepath.Join(m.root, "artifacts", entry.Name())); err != nil && !os.IsNotExist(err) {
			fmt.Fprintf(os.Stderr, "fleet: cannot remove artifact %s: %v\n", d[:12], err)
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
