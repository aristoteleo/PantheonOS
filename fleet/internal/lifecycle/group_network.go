package lifecycle

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/groupnetwork"
)

// Executed as the container's PID 1, before any model code. The node writes an
// exclusive marker only after the original namespace and all routes are ready.
// No credentials, writable host mounts or network capabilities are added.
const groupAdmissionGate = `import json,os,signal,sys,time
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
signal.signal(signal.SIGINT,lambda *_:sys.exit(0))
resource=sys.argv[1]
path='/tmp/.pantheon-group-'+resource
deadline=time.monotonic()+90
while True:
 try:
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 except FileNotFoundError:
  if time.monotonic()>deadline:raise TimeoutError('Fleet group network admission timed out')
  time.sleep(.025);continue
 with os.fdopen(fd,'rb') as f: data=json.loads(f.read(4097))
 ns=os.stat('/proc/self/ns/net')
 if data!={'resource':resource,'namespace':str(ns.st_dev)+':'+str(ns.st_ino)}:raise ValueError('Original group network identity changed')
 if 'CapEff:\t0000000000000000' not in open('/proc/self/status').read():raise ValueError('Group engine retained capabilities')
 os.execvp(sys.argv[2],sys.argv[2:])
`

const groupAdmissionWrite = `import os,sys,tempfile
path='/tmp/.pantheon-group-'+sys.argv[1]
fd,temp=tempfile.mkstemp(prefix='.pantheon-group-',dir='/tmp')
try:
 with os.fdopen(fd,'w') as f:f.write(sys.argv[2]);f.flush();os.fsync(f.fileno())
 os.chmod(temp,0o400);os.link(temp,path)
finally:os.unlink(temp)
`

type groupIngress struct {
	handle   groupnetwork.NamespaceHandle
	forwards []*groupnetwork.Forwarder
}

func (g *groupIngress) close() {
	for _, f := range g.forwards {
		_ = f.Close()
	}
	_ = g.handle.Close()
}

type groupIngressRegistry struct {
	mu      sync.Mutex
	closed  bool
	entries map[string]*groupIngress
}

func newGroupIngressRegistry() *groupIngressRegistry {
	return &groupIngressRegistry{entries: map[string]*groupIngress{}}
}
func (g *groupIngressRegistry) has(id string) bool {
	if g == nil {
		return false
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	return !g.closed && g.entries[id] != nil
}
func (g *groupIngressRegistry) add(id string, ingress *groupIngress) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.closed || g.entries[id] != nil {
		return fmt.Errorf("original group ingress already registered or Runner closed")
	}
	g.entries[id] = ingress
	return nil
}
func (g *groupIngressRegistry) release(id string) {
	if g == nil {
		return
	}
	g.mu.Lock()
	v := g.entries[id]
	delete(g.entries, id)
	g.mu.Unlock()
	if v != nil {
		v.close()
	}
}
func (d NativeDriver) CloseGroupIngress() {
	g := d.groupIngress
	if g == nil {
		return
	}
	g.mu.Lock()
	g.closed = true
	all := g.entries
	g.entries = map[string]*groupIngress{}
	g.mu.Unlock()
	for _, v := range all {
		v.close()
	}
}

func consumesGroupNetwork(def Definition) bool {
	for _, c := range def.Components {
		if c.GroupNetwork {
			return true
		}
	}
	return false
}
func overlayStore(root, owner, node string) groupcredentials.OverlayStore {
	return groupcredentials.OverlayStore{Root: root, Owner: owner, Node: node}
}
func (m *Manager) checkPreparedGroupNetwork(def Definition, in *Instance, packagePath string) error {
	if !consumesGroupNetwork(def) {
		return nil
	}
	if in == nil || in.State != "prepared" {
		return fmt.Errorf("private group requires original prepared generation")
	}
	manifest, err := readGroupManifest(packagePath)
	if err != nil || manifest == nil {
		return fmt.Errorf("private group manifest unavailable")
	}
	store := overlayStore(filepath.Join(m.root, "group-overlays"), m.owner, m.node)
	key, _, err := store.Material(*manifest)
	clear(key)
	if err != nil {
		return err
	}
	n, _, err := store.Network(*manifest)
	if err != nil {
		return err
	}
	if n != nil {
		return fmt.Errorf("private network already consumed; reconcile original group")
	}
	return nil
}

func (d NativeDriver) checkGroupNetwork(c Component, p Paths) error {
	if runtime.GOOS != "linux" || !c.GroupPeer || !c.RunAsOwner || d.groupIngress == nil || !filepath.IsAbs(c.groupOverlayRoot) {
		return fmt.Errorf("private collective containers require a Linux lifecycle owner")
	}
	m, err := readGroupManifest(p.Package)
	if err != nil || m == nil {
		return fmt.Errorf("original group manifest missing")
	}
	if fmt.Sprint(m.Topology.Members[*m.Rank].Generation) != c.Env["PANTHEON_INSTANCE_GENERATION"] {
		return fmt.Errorf("private group generation differs from original container")
	}
	s := overlayStore(c.groupOverlayRoot, c.Env["PANTHEON_FLEET_ID"], c.Env["PANTHEON_NODE_ID"])
	key, _, err := s.Material(*m)
	clear(key)
	return err
}

func isolatedContainer(info containerInfo) error {
	if info.ID == "" || !info.State.Running || info.State.Pid <= 0 || info.State.StartedAt == "" ||
		info.HostConfig.NetworkMode != "none" || info.HostConfig.Privileged || len(info.HostConfig.CapAdd) != 0 ||
		len(info.HostConfig.CapDrop) != 1 || !strings.EqualFold(info.HostConfig.CapDrop[0], "ALL") ||
		(info.HostConfig.RestartPolicy.Name != "" && info.HostConfig.RestartPolicy.Name != "no") {
		return fmt.Errorf("original group container is not isolated or can restart independently")
	}
	return nil
}
func sameContainer(before, after containerInfo) bool {
	return isolatedContainer(before) == nil && isolatedContainer(after) == nil && before.ID == after.ID && before.State.Pid == after.State.Pid && before.State.StartedAt == after.State.StartedAt
}

func (d NativeDriver) startGroupNetwork(ctx context.Context, c Component, p Paths, r Resource, original containerInfo) (Resource, error) {
	if err := isolatedContainer(original); err != nil {
		return r, err
	}
	h, err := groupnetwork.OpenContainerNamespace(original.State.Pid)
	if err != nil {
		return r, err
	}
	ingress := &groupIngress{handle: h}
	retained := false
	defer func() {
		if !retained {
			ingress.close()
		}
	}()
	after, err := d.inspectContainer(ctx, r)
	if err != nil {
		return r, err
	}
	if !sameContainer(original, after) {
		return r, fmt.Errorf("container changed while opening its network namespace")
	}
	manifest, err := readGroupManifest(p.Package)
	if err != nil || manifest == nil {
		return r, fmt.Errorf("original group manifest missing")
	}
	store := overlayStore(c.groupOverlayRoot, c.Env["PANTHEON_FLEET_ID"], c.Env["PANTHEON_NODE_ID"])
	if _, err = groupnetwork.CreateDurableAttached(ctx, store, *manifest, nil, h); err != nil {
		return r, err
	}
	after, err = d.inspectContainer(ctx, r)
	if err != nil {
		return r, err
	}
	if !sameContainer(original, after) {
		return r, fmt.Errorf("container changed during private network admission")
	}
	for name, port := range c.Ports {
		f, e := groupnetwork.Forward(port, h.Dial)
		if e != nil {
			return r, e
		}
		ingress.forwards = append(ingress.forwards, f)
		r.Endpoints[name] = f.Address()
	}
	marker, _ := json.Marshal(map[string]string{"resource": r.ID, "namespace": h.Identity()})
	if _, err = d.docker(ctx, "exec", r.ID, "python3", "-I", "-c", groupAdmissionWrite, r.ID, string(marker)); err != nil {
		return r, err
	}
	if err = d.groupIngress.add(r.ID, ingress); err != nil {
		return r, err
	}
	retained = true
	return r, nil
}

// Called under the lifecycle lock, after original resources have been reaped.
// Closing enrollment is separate from proving that all kernel state is gone.
func (m *Manager) clearGroupNetwork(ctx context.Context, in *Instance) error {
	install := m.ledger.Installations[in.Digest]
	if install == nil || !consumesGroupNetwork(install.Definition) {
		return nil
	}
	manifest, err := readGroupManifest(m.paths(in.Digest, in.Scope).Package)
	if err != nil || manifest == nil {
		return fmt.Errorf("original group network manifest unavailable for cleanup")
	}
	s := overlayStore(filepath.Join(m.root, "group-overlays"), m.owner, m.node)
	status, err := s.Status(manifest.Topology.Group, manifest.Fingerprint())
	if os.IsNotExist(err) {
		_, err = s.Close(manifest.Topology.Group, manifest.Fingerprint())
		return err
	}
	if err != nil {
		return err
	}
	if status.State == "closed" && status.Endpoint == nil {
		return nil
	}
	return groupnetwork.StopDurable(ctx, s, *manifest, nil, func(c context.Context) error {
		for _, r := range in.Resources {
			alive, e := m.driver.Alive(c, r)
			if e != nil {
				return e
			}
			if alive {
				return fmt.Errorf("group container still running")
			}
		}
		return nil
	})
}
