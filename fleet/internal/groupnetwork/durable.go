package groupnetwork

import (
	"context"
	"fmt"
	"net"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

type Link struct {
	Name      string   `json:"ifname"`
	Alias     string   `json:"ifalias"`
	Flags     []string `json:"flags"`
	Addresses []any    `json:"addr_info"`
	Info      struct {
		Kind string `json:"info_kind"`
	} `json:"linkinfo"`
}

// Observation describes only the exact claimed names. An error is uncertainty,
// never evidence of absence. Namespace identity is the nsfs device:inode pair.
type Observation struct {
	Identity string
	Host     *Link
	Links    []Link
	PIDs     []int
}

type Backend interface {
	Runner
	Observe(context.Context, groupcredentials.OverlayNetwork) (Observation, error)
}

// Attachment holds an opened namespace belonging to the lifecycle owner's
// original container. It must not resolve a PID again when Attach is called.
// The caller retains it until CreateDurableAttached returns, then closes it.
type Attachment interface {
	Identity() string
	Attach(context.Context, string) error
}

type NamespaceHandle interface {
	Attachment
	Close() error
	Dial(context.Context, int) (net.Conn, error)
}

type durableNetwork struct {
	store      groupcredentials.OverlayStore
	manifest   groupcredentials.Manifest
	state      groupcredentials.OverlayNetwork
	backend    Backend
	attachment Attachment
}

func (d *durableNetwork) alias() string {
	return fmt.Sprintf("pantheon-model-group:%s:r%d:%s", d.manifest.Fingerprint(), *d.manifest.Rank, d.state.Namespace)
}

func (d *durableNetwork) claim(ctx context.Context, n *Network) error {
	d.state = groupcredentials.OverlayNetwork{Namespace: n.namespace, HostInterface: n.hostInterface, Stage: "claimed"}
	if d.attachment != nil {
		d.state.AttachedIdentity = d.attachment.Identity()
		if d.state.AttachedIdentity == "" {
			return fmt.Errorf("missing original container namespace identity")
		}
	}
	// An existing name is never adopted as a fresh claim. Names are generated
	// exclusively on the node and never accepted from an App or controller.
	o, err := d.backend.Observe(ctx, d.state)
	if err != nil {
		return err
	}
	if o.Identity != "" || o.Host != nil {
		return fmt.Errorf("new overlay resource name already exists")
	}
	return d.store.ClaimNetwork(d.manifest, d.state)
}

func (d *durableNetwork) advance(stage, identity string) error {
	next := d.state
	next.Stage = stage
	if stage == "closing" && d.state.Stage != "closing" {
		next.ClosingFrom = d.state.Stage
	}
	if stage == "creating" {
		next.CreateRequested = true
	}
	if identity != "" {
		next.NamespaceIdentity = identity
	}
	if err := d.store.AdvanceNetwork(d.manifest, d.state, next); err != nil {
		return err
	}
	d.state = next
	return nil
}

// CreateDurable loads only the pinned original key/roster. It persists ownership
// and each mutation's intent before issuing commands. A retry never replays an
// uncertain create: the original claim must first be inspected/stopped. The
// caller must hold the node lifecycle lock for this complete operation.
func CreateDurable(ctx context.Context, store groupcredentials.OverlayStore, m groupcredentials.Manifest, backend Backend) (*Network, error) {
	return createDurable(ctx, store, m, backend, nil)
}

// CreateDurableAttached equips an owned --network none container with the
// group's encrypted interface. The engine must wait for admission before
// launching. No Docker bridge, host network, runtime capability or new daemon
// is required. The caller holds the same lifecycle lock as creation/cleanup.
func CreateDurableAttached(ctx context.Context, store groupcredentials.OverlayStore, m groupcredentials.Manifest, backend Backend, attachment Attachment) (*Network, error) {
	if attachment == nil {
		return nil, fmt.Errorf("original container namespace handle is required")
	}
	return createDurable(ctx, store, m, backend, attachment)
}

func createDurable(ctx context.Context, store groupcredentials.OverlayStore, m groupcredentials.Manifest, backend Backend, attachment Attachment) (*Network, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	private, endpoints, err := store.Material(m)
	if err != nil {
		return nil, err
	}
	defer func() {
		for i := range private {
			private[i] = 0
		}
	}()
	if backend == nil {
		backend = Commands{}
	}
	d := &durableNetwork{store: store, manifest: m, backend: backend, attachment: attachment}
	return create(ctx, Spec{Manifest: m, Endpoints: endpoints, Interface: "wg0"}, private, backend, d)
}

// StopDurable fences enrollment, then calls the lifecycle owner's original
// workload reconciler before deleting resources. The guard must reap all joined
// engines/containers and holders, not just hide their UI windows. A node-state
// lock must exclude new joins throughout this operation. Kernel PID checks are
// additional evidence, not a substitute for container ownership reconciliation.
// Safe retries resolve lost delete acknowledgements by inspecting exact names.
func StopDurable(ctx context.Context, store groupcredentials.OverlayStore, m groupcredentials.Manifest, backend Backend, guard func(context.Context) error) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if guard == nil {
		return fmt.Errorf("original workload reconciliation is required")
	}
	if backend == nil {
		backend = Commands{}
	}
	state, _, err := store.Network(m)
	if err != nil {
		return err
	}
	if _, err := store.Close(m.Topology.Group, m.Fingerprint()); err != nil {
		return err
	}
	if state == nil {
		return guard(ctx)
	}
	if state.Stage == "closed" {
		return nil
	}
	d := &durableNetwork{store: store, manifest: m, state: *state, backend: backend}
	if state.Stage != "closing" {
		if err := d.advance("closing", ""); err != nil {
			return err
		}
	}
	if err := guard(ctx); err != nil {
		return err
	}
	o, err := backend.Observe(ctx, d.state)
	if err != nil {
		return err
	}
	if err := d.validateCleanup(o); err != nil {
		return err
	}
	if o.Identity != "" && d.state.NamespaceIdentity == "" {
		// The original random name was reserved before create. In the crash
		// gap before its first acknowledgement, only an empty namespace (or
		// our exact aliased interface) can be bound; no existing foreign link
		// or process is adopted. Pin its inode before any destructive command.
		if err := d.advance("closing", o.Identity); err != nil {
			return err
		}
	}
	if o.Host != nil {
		if err := backend.Run(ctx, []string{"ip", "link", "del", d.state.HostInterface}, nil); err != nil {
			return err
		}
	}
	if o.Identity != "" {
		if err := backend.Run(ctx, []string{"ip", "netns", "del", d.state.Namespace}, nil); err != nil {
			return err
		}
	}
	final, err := backend.Observe(ctx, d.state)
	if err != nil {
		return err
	}
	if final.Identity != "" || final.Host != nil {
		return fmt.Errorf("original network resources remain after cleanup")
	}
	return d.advance("closed", "")
}

func (d *durableNetwork) validateCleanup(o Observation) error {
	if o.Identity != "" && d.state.AttachedIdentity != "" && o.Identity != d.state.AttachedIdentity {
		return fmt.Errorf("original container namespace changed; refusing cleanup")
	}
	if !d.state.CreateRequested && (o.Identity != "" || o.Host != nil) {
		return fmt.Errorf("unrequested network resource exists; do not adopt")
	}
	if o.Identity != "" && d.state.NamespaceIdentity != "" && o.Identity != d.state.NamespaceIdentity {
		return fmt.Errorf("namespace identity changed; refusing cleanup")
	}
	if len(o.PIDs) != 0 {
		return fmt.Errorf("namespace still has live processes")
	}
	owned := func(link Link) bool { return link.Alias == d.alias() && link.Info.Kind == "wireguard" }
	if o.Host != nil {
		untaggedOriginal := d.state.ClosingFrom == "linking" && o.Host.Name == d.state.HostInterface && o.Host.Alias == "" && o.Host.Info.Kind == "wireguard"
		if o.Host.Name != d.state.HostInterface || (!owned(*o.Host) && !untaggedOriginal) {
			return fmt.Errorf("host interface is not owned by this group")
		}
	}
	for _, link := range o.Links {
		if link.Name == "wg0" || link.Name == d.state.HostInterface {
			// In the move-before-rename crash gap Linux has cleared ifalias.
			// The still-random original name inside the pinned original inode
			// proves ownership; never extend this exception to generic wg0.
			movedOriginal := link.Name == d.state.HostInterface && link.Alias == "" && link.Info.Kind == "wireguard" && o.Identity == d.state.NamespaceIdentity
			if !owned(link) && !movedOriginal {
				return fmt.Errorf("namespace interface ownership changed")
			}
			continue
		}
		if link.Name == "lo" {
			continue
		}
		fallback := map[string]bool{"tunl0": true, "gre0": true, "gretap0": true, "erspan0": true, "ip_vti0": true, "ip6_vti0": true, "sit0": true, "ip6tnl0": true, "ip6gre0": true}
		if !fallback[link.Name] || len(link.Addresses) != 0 {
			return fmt.Errorf("namespace contains an unowned interface")
		}
		for _, flag := range link.Flags {
			if flag == "UP" {
				return fmt.Errorf("namespace contains an active unowned interface")
			}
		}
	}
	return nil
}
