package groupcredentials

import (
	"fmt"
	"os"
	"reflect"
	"regexp"
)

// OverlayNetwork is a node-generated resource intent. Stage is persisted before
// each kernel mutation; an uncertain operation is never implicitly repeated.
// Closed enrollment and closed kernel resources are separate facts.
type OverlayNetwork struct {
	Namespace         string `json:"namespace"`
	HostInterface     string `json:"host_interface"`
	Stage             string `json:"stage"`
	ClosingFrom       string `json:"closing_from,omitempty"`
	CreateRequested   bool   `json:"create_requested"`
	NamespaceIdentity string `json:"namespace_identity,omitempty"`
	// An already owned container's namespace is pinned before attaching its
	// anchor. This is not permission to adopt a namespace found after a crash.
	AttachedIdentity string `json:"attached_identity,omitempty"`
}

var namespaceRE = regexp.MustCompile(`^pf-group-[a-f0-9]{24}$`)
var hostInterfaceRE = regexp.MustCompile(`^pfg[a-f0-9]{12}$`)
var namespaceIdentityRE = regexp.MustCompile(`^[1-9][0-9]{0,19}:[1-9][0-9]{0,19}$`)
var networkStages = []string{"claimed", "creating", "namespace", "linking", "link", "moving", "moved", "naming", "named", "configuring", "ready", "closing", "closed"}

func networkStage(stage string) int {
	for i, s := range networkStages {
		if s == stage {
			return i
		}
	}
	return -1
}

func (n OverlayNetwork) Validate() error {
	stage := networkStage(n.Stage)
	if !namespaceRE.MatchString(n.Namespace) || !hostInterfaceRE.MatchString(n.HostInterface) ||
		n.HostInterface != "pfg"+n.Namespace[len("pf-group-"):len("pf-group-")+12] || stage < 0 {
		return fmt.Errorf("invalid node-generated overlay ownership")
	}
	if n.NamespaceIdentity != "" && !namespaceIdentityRE.MatchString(n.NamespaceIdentity) {
		return fmt.Errorf("invalid namespace identity")
	}
	if n.AttachedIdentity != "" && (!namespaceIdentityRE.MatchString(n.AttachedIdentity) ||
		(n.NamespaceIdentity != "" && n.NamespaceIdentity != n.AttachedIdentity)) {
		return fmt.Errorf("attached container namespace identity changed")
	}
	if stage >= 2 && stage <= 10 && n.NamespaceIdentity == "" {
		return fmt.Errorf("namespace acknowledgement missing its identity")
	}
	if stage < 2 && n.NamespaceIdentity != "" {
		return fmt.Errorf("namespace has not been acknowledged")
	}
	if !n.CreateRequested && n.NamespaceIdentity != "" {
		return fmt.Errorf("unrequested namespace has an identity")
	}
	if (stage == 0 && n.CreateRequested) || (stage >= 1 && stage <= 10 && !n.CreateRequested) {
		return fmt.Errorf("invalid namespace creation intent")
	}
	if stage >= 11 {
		origin := networkStage(n.ClosingFrom)
		if origin < 0 || origin >= 11 || (origin == 0 && n.CreateRequested) || (origin > 0 && !n.CreateRequested) {
			return fmt.Errorf("cleanup must retain original setup phase")
		}
	} else if n.ClosingFrom != "" {
		return fmt.Errorf("unexpected cleanup origin")
	}
	return nil
}

func (s OverlayStore) networkRecord(m Manifest) (*os.Root, overlayRecord, error) {
	if err := s.validate(m); err != nil {
		return nil, overlayRecord{}, err
	}
	dir, _, err := s.directory(m.Topology.Group, m.Fingerprint(), false)
	if err != nil {
		return nil, overlayRecord{}, err
	}
	r, err := s.read(dir, m.Topology.Group, m.Fingerprint())
	if err != nil || !reflect.DeepEqual(r.Manifest, &m) {
		dir.Close()
		return nil, overlayRecord{}, fmt.Errorf("original overlay enrollment is unavailable")
	}
	return dir, r, nil
}

// ClaimNetwork is called once with freshly generated names before any effects.
// It never returns a prior claim as a fresh grant to repeat namespace creation.
func (s OverlayStore) ClaimNetwork(m Manifest, n OverlayNetwork) error {
	if n.Validate() != nil || n.Stage != "claimed" {
		return fmt.Errorf("claim requires fresh node-generated names")
	}
	dir, r, err := s.networkRecord(m)
	if err != nil {
		return err
	}
	defer dir.Close()
	if r.State != "pinned" || r.Network != nil {
		return fmt.Errorf("network already claimed or enrollment closed; reconcile original resources")
	}
	r.Network = &n
	return writeOverlay(dir, r)
}

// Network returns public ownership after enrollment close too, for cleanup.
func (s OverlayStore) Network(m Manifest) (*OverlayNetwork, []OverlayEndpoint, error) {
	dir, r, err := s.networkRecord(m)
	if err != nil {
		return nil, nil, err
	}
	defer dir.Close()
	return r.Network, r.Endpoints, nil
}

// AdvanceNetwork uses exact-state CAS and forward-only transitions. Network
// cleanup remains allowed after close erases the stored private key.
func (s OverlayStore) AdvanceNetwork(m Manifest, before, after OverlayNetwork) error {
	if before.Validate() != nil || after.Validate() != nil || before.Namespace != after.Namespace || before.HostInterface != after.HostInterface || before.AttachedIdentity != after.AttachedIdentity ||
		(before.NamespaceIdentity != "" && before.NamespaceIdentity != after.NamespaceIdentity) ||
		(before.CreateRequested && !after.CreateRequested) || (!before.CreateRequested && after.CreateRequested && after.Stage != "creating") {
		return fmt.Errorf("original network ownership cannot change")
	}
	a, b := networkStage(before.Stage), networkStage(after.Stage)
	if (a < 11 && b == 11 && after.ClosingFrom != before.Stage) || (a >= 11 && after.ClosingFrom != before.ClosingFrom) {
		return fmt.Errorf("original cleanup phase cannot change")
	}
	bindClosing := before.Stage == "closing" && after.Stage == "closing" && before.NamespaceIdentity == "" && after.NamespaceIdentity != ""
	if !(b == a+1 || (after.Stage == "closing" && a < 11) || bindClosing) {
		return fmt.Errorf("invalid network transition")
	}
	dir, r, err := s.networkRecord(m)
	if err != nil {
		return err
	}
	defer dir.Close()
	if r.Network == nil || !reflect.DeepEqual(*r.Network, before) {
		return fmt.Errorf("network ownership changed; read original state")
	}
	if r.State != "pinned" && b < 11 {
		return fmt.Errorf("overlay enrollment closed")
	}
	r.Network = &after
	return writeOverlay(dir, r)
}
