package hosted

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/appgateway"
	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/aristoteleo/pantheonos/os/internal/deployments"
)

// Allocator is the dependency-binding service (formerly the Python allocator
// App, pantheonos/apps/allocator_host.py + live_dependencies.py): it binds an
// Agent's tool sessions to the deployment's provider Apps at run time with
// dependency grants, under fixed per-consumer policies from its
// configuration, and renews, revokes and retires them.
type Allocator struct {
	Bus    Bus
	Store  *deployments.Store
	Grants Grants
	// Root holds the journals: <Root>/<fleet>/{bindings,retirements,sessions}.
	Root string
	Now  func() time.Time

	mu    sync.Mutex
	locks map[string]*sync.Mutex
}

// Grants is the controller's dependency grant authority (the App gateway).
type Grants interface {
	IssueDependency(ctx context.Context, q appgateway.DependencyRequest) (map[string]any, error)
	RenewDependency(ctx context.Context, fleet, id string, expires int64) (map[string]any, error)
	RevokeDependency(fleet, id string) error
}

var (
	nameRE     = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,79}$`)
	identRE    = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)
	rpcRE      = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]{0,127}$`)
	argumentRE = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`)
	digestRE   = regexp.MustCompile(`^[a-f0-9]{64}$`)
	versionRE  = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)
)

const pendingSession = "<pending-resource-session>"

// live consumer states a binding may be issued to.
var liveStates = map[string]bool{"starting": true, "ready": true, "recovered": true}

// errAllocation is what an Agent sees; the cause stays in the controller log.
var errAllocation = errors.New("Dependency allocation unavailable; retry the original operation or inspect its owner")

type identity struct {
	NodeID     string `json:"node_id"`
	InstanceID string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation int64  `json:"generation"`
	Component  string `json:"component,omitempty"`
	Port       string `json:"port,omitempty"`
}

func (i identity) valid(provider bool) bool {
	ok := identRE.MatchString(i.NodeID) && identRE.MatchString(i.InstanceID) && digestRE.MatchString(i.Revision) && i.Generation > 0
	if provider {
		return ok && i.Component == "backend" && i.Port == "http"
	}
	return ok && i.Component == "" && i.Port == ""
}

func (i identity) binding(fleet string) apptransport.Binding {
	return apptransport.Binding{Fleet: fleet, Node: i.NodeID, Instance: i.InstanceID, Revision: i.Revision,
		Generation: uint64(i.Generation), Component: i.Component, Port: i.Port}
}

func (i identity) instance(fleet string) apptransport.InstanceIdentity {
	return apptransport.InstanceIdentity{Fleet: fleet, Node: i.NodeID, Instance: i.InstanceID, Revision: i.Revision, Generation: uint64(i.Generation)}
}

// public is the identity as grant receipts carry it (with the owner).
func (i identity) public(fleet string) map[string]any {
	out := map[string]any{"node_id": i.NodeID, "instance_id": i.InstanceID, "revision": i.Revision, "generation": i.Generation, "fleet_id": fleet}
	if i.Component != "" {
		out["component"], out["port"] = i.Component, i.Port
	}
	return out
}

type rule struct {
	Arguments []string                   `json:"arguments"`
	Bound     map[string]json.RawMessage `json:"bound"`
}

type resourcePolicy struct {
	Kind      string            `json:"kind"`
	Arguments map[string]string `json:"arguments"`
}

type bindingPolicy struct {
	AppID    string          `json:"app_id"`
	Provider json.RawMessage `json:"provider"`
	Methods  map[string]rule `json:"methods"`
	Resource *resourcePolicy `json:"resource,omitempty"`
}

type policy struct {
	Consumer json.RawMessage          `json:"consumer"`
	Bindings map[string]bindingPolicy `json:"bindings"`
}

// digest is a stable digest of JSON values (journal keys, operation ids).
func digest(values ...any) string {
	raw, _ := json.Marshal(values)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func (a *Allocator) now() time.Time {
	if a.Now != nil {
		return a.Now()
	}
	return time.Now()
}

func (a *Allocator) lock(key string) func() {
	a.mu.Lock()
	if a.locks == nil {
		a.locks = map[string]*sync.Mutex{}
	}
	l := a.locks[key]
	if l == nil {
		l = &sync.Mutex{}
		a.locks[key] = l
	}
	a.mu.Unlock()
	l.Lock()
	return l.Unlock
}

func (a *Allocator) Call(ctx context.Context, c *Call) (any, error) {
	policies, err := a.policies(c)
	if err != nil {
		log.Printf("[hosted] %s allocator configuration: %v", c.Fleet, err)
		return nil, errAllocation
	}
	switch c.Method {
	case "bind_dependencies":
		var args struct {
			PolicyID    string   `json:"policy_id"`
			OwnerRef    string   `json:"owner_ref"`
			OperationID string   `json:"operation_id"`
			Aliases     []string `json:"aliases"`
		}
		if err := c.Decode(&args); err != nil {
			return nil, err
		}
		p, ok := policies[args.PolicyID]
		if !ok {
			return nil, errAllocation
		}
		result, err := a.bind(ctx, c, p, args.OwnerRef, args.OperationID, args.Aliases)
		if err != nil {
			log.Printf("[hosted] %s allocation %s: %v", c.Fleet, args.OwnerRef, err)
			return nil, errAllocation
		}
		return result, nil
	case "retire_dependencies":
		var args struct {
			PolicyID string `json:"policy_id"`
			OwnerRef string `json:"owner_ref"`
		}
		if err := c.Decode(&args); err != nil {
			return nil, err
		}
		p, ok := policies[args.PolicyID]
		if !ok {
			return nil, errAllocation
		}
		consumer, err := a.resolveIdentity(c.Fleet, c.Deployment, p.Consumer, false)
		if err != nil {
			log.Printf("[hosted] %s retirement %s: %v", c.Fleet, args.OwnerRef, err)
			return nil, errors.New("Dependency retirement incomplete; retry the same logical owner")
		}
		result, err := a.retire(ctx, c.Fleet, consumer, args.OwnerRef)
		if err != nil {
			log.Printf("[hosted] %s retirement %s: %v", c.Fleet, args.OwnerRef, err)
			return nil, errors.New("Dependency retirement incomplete; retry the same logical owner")
		}
		return result, nil
	}
	return nil, &ArgumentError{"Method " + c.Method + " not found on service dependency-binding"}
}

// policies reads the App's fixed allocation policies from its configuration.
func (a *Allocator) policies(c *Call) (map[string]policy, error) {
	var backend struct {
		Values struct {
			DependencyBinding struct {
				Protocol int               `json:"protocol"`
				Policies map[string]policy `json:"policies"`
			} `json:"dependency_binding"`
		} `json:"values"`
	}
	if err := json.Unmarshal(c.Spec.Config["backend"], &backend); err != nil {
		return nil, err
	}
	spec := backend.Values.DependencyBinding
	if spec.Protocol != 1 || len(spec.Policies) == 0 || len(spec.Policies) > 64 {
		return nil, fmt.Errorf("explicit dependency allocation policies are required")
	}
	for name := range spec.Policies {
		if !nameRE.MatchString(name) {
			return nil, fmt.Errorf("invalid policy name")
		}
	}
	return spec.Policies, nil
}

// resolveIdentity turns a policy reference ({"$app": name, ...} naming an App
// of the deployment, or an exact identity) into the instance it runs now.
func (a *Allocator) resolveIdentity(fleet, deployment string, raw json.RawMessage, provider bool) (identity, error) {
	var ref map[string]any
	if err := json.Unmarshal(raw, &ref); err != nil {
		return identity{}, fmt.Errorf("invalid instance reference")
	}
	var out identity
	if name, ok := ref["$app"].(string); ok {
		d, err := a.Store.Get(fleet, deployment)
		if err != nil {
			return out, err
		}
		s := d.Status.Apps[name]
		if (s.State != "ready" && s.State != "starting") || s.NodeID == "" || s.InstanceID == "" || s.Revision == "" || s.Generation == 0 {
			return out, fmt.Errorf("%s is not running yet", name)
		}
		out = identity{NodeID: s.NodeID, InstanceID: s.InstanceID, Revision: s.Revision, Generation: s.Generation}
		out.Component, _ = ref["component"].(string)
		out.Port, _ = ref["port"].(string)
	} else if err := json.Unmarshal(raw, &out); err != nil {
		return out, fmt.Errorf("invalid instance reference")
	}
	if !out.valid(provider) {
		return out, fmt.Errorf("use an exact App instance binding")
	}
	return out, nil
}

// validatePolicy is the former _binding_policy check of one binding.
func validatePolicy(alias string, b bindingPolicy) error {
	if !nameRE.MatchString(alias) || !nameRE.MatchString(b.AppID) || b.Methods == nil {
		return fmt.Errorf("invalid live dependency policy")
	}
	if r := b.Resource; r != nil {
		if !identRE.MatchString(r.Kind) || len(r.Arguments) == 0 {
			return fmt.Errorf("invalid resource-session binding")
		}
		for method, argument := range r.Arguments {
			m, ok := b.Methods[method]
			if !ok || !rpcRE.MatchString(argument) || m.Bound == nil || m.Arguments == nil || slices.Contains(m.Arguments, argument) {
				return fmt.Errorf("resource arguments must be supplied only by the owner")
			}
			if _, bound := m.Bound[argument]; bound {
				return fmt.Errorf("resource arguments must be supplied only by the owner")
			}
		}
	}
	return nil
}

// ---- journals ---------------------------------------------------------------

func (a *Allocator) dir(fleet, kind string) (string, error) {
	if !identRE.MatchString(fleet) {
		return "", fmt.Errorf("invalid fleet")
	}
	dir := filepath.Join(a.Root, fleet, kind)
	return dir, os.MkdirAll(dir, 0o700)
}

func readJournal(path string, v any) (bool, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if len(raw) > 256*1024 {
		return true, fmt.Errorf("journal %s is oversized", filepath.Base(path))
	}
	return true, json.Unmarshal(raw, v)
}

func writeJournal(path string, v any) error {
	raw, err := json.Marshal(v)
	if err != nil {
		return err
	}
	if len(raw) > 256*1024 {
		return fmt.Errorf("journal exceeds its storage limit")
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".tmp-*")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if _, err = f.Write(raw); err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	if err := os.Chmod(tmp, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	if d, err := os.Open(filepath.Dir(path)); err == nil {
		_ = d.Sync()
		d.Close()
	}
	return nil
}

func journals(dir string) []string {
	paths, _ := filepath.Glob(filepath.Join(dir, "*.json"))
	sort.Strings(paths)
	return paths
}

// ---- binding ----------------------------------------------------------------

type grantRequest struct {
	OperationID string          `json:"operation_id"`
	Consumer    identity        `json:"consumer"`
	Provider    identity        `json:"provider"`
	AppID       string          `json:"app_id"`
	Methods     map[string]rule `json:"methods"`
}

type receipt struct {
	GrantID  string         `json:"grant_id"`
	Consumer map[string]any `json:"consumer"`
	Provider map[string]any `json:"provider"`
	Expires  int64          `json:"expires"`
	State    string         `json:"state,omitempty"` // "", active, revoked, expired
}

type sessionRecipe struct {
	Consumer    identity `json:"consumer"`
	OperationID string   `json:"operation_id"`
	OwnerRef    string   `json:"owner_ref"`
	Provider    identity `json:"provider"`
	AppID       string   `json:"app_id"`
	Kind        string   `json:"kind"`
}

type bindingRecipe struct {
	Consumer    identity                 `json:"consumer"`
	OwnerRef    string                   `json:"owner_ref"`
	OperationID string                   `json:"operation_id"`
	Bindings    map[string]bindingPolicy `json:"bindings"`
}

type bindingRecord struct {
	Protocol int           `json:"protocol"`
	Recipe   bindingRecipe `json:"recipe"`
	Plan     struct {
		Requests map[string]*grantRequest  `json:"requests"`
		Sessions map[string]*sessionRecipe `json:"sessions"`
	} `json:"plan"`
	Phase    string              `json:"phase"` // binding, bound, retiring
	Renewals map[string]*receipt `json:"renewals"`
	Terminal []string            `json:"terminal_issuance,omitempty"`
}

// lifecycleStatus reads a node's ledger and checks it belongs to the fleet.
func (a *Allocator) lifecycleStatus(ctx context.Context, fleet, node string) (map[string]any, error) {
	state, err := lifecycle(ctx, a.Bus, fleet, node, "status", nil)
	if err != nil {
		return nil, err
	}
	if state["node_id"] != node || state["owner"] != fleet {
		return nil, fmt.Errorf("node state belongs to another owner or node")
	}
	return state, nil
}

func instanceOf(state map[string]any, id string) map[string]any {
	instances, _ := state["instances"].(map[string]any)
	in, _ := instances[id].(map[string]any)
	return in
}

// live checks the consumer runs at exactly its identity.
func (a *Allocator) live(ctx context.Context, fleet string, consumer identity) error {
	state, err := a.lifecycleStatus(ctx, fleet, consumer.NodeID)
	if err != nil {
		return err
	}
	in := instanceOf(state, consumer.InstanceID)
	if in == nil || str(in["digest"]) != consumer.Revision || int64(num(in["generation"])) != consumer.Generation || !liveStates[str(in["state"])] {
		return fmt.Errorf("dependency binding requires the exact live consumer")
	}
	return nil
}

// installedManifest is an exact installed revision's manifest, from its node
// or, for a hosted App, from this controller.
func (a *Allocator) installedManifest(ctx context.Context, fleet string, who identity) (map[string]any, error) {
	var raw []byte
	if who.NodeID == Node {
		var ok bool
		if raw, ok = ByRevision(who.Revision); !ok {
			return nil, fmt.Errorf("unknown hosted revision")
		}
	} else {
		result, err := lifecycle(ctx, a.Bus, fleet, who.NodeID, "app_manifest", map[string]any{"revision": who.Revision})
		if err != nil {
			return nil, err
		}
		if num(result["protocol"]) != 1 || result["revision"] != who.Revision {
			return nil, fmt.Errorf("node returned another App manifest")
		}
		raw, _ = json.Marshal(result["manifest"])
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func (a *Allocator) bind(ctx context.Context, c *Call, p policy, ownerRef, operationID string, aliases []string) (map[string]any, error) {
	if len(aliases) == 0 || len(aliases) > 16 {
		return nil, fmt.Errorf("select only the approved dependency aliases")
	}
	seen := map[string]bool{}
	for _, alias := range aliases {
		if _, ok := p.Bindings[alias]; !ok || seen[alias] {
			return nil, fmt.Errorf("select only the approved dependency aliases")
		}
		seen[alias] = true
	}
	if !identRE.MatchString(ownerRef) || !nameRE.MatchString(operationID) {
		return nil, fmt.Errorf("use stable logical owner and binding operation IDs")
	}
	consumer, err := a.resolveIdentity(c.Fleet, c.Deployment, p.Consumer, false)
	if err != nil {
		return nil, err
	}
	sort.Strings(aliases)
	bindings := map[string]bindingPolicy{}
	for _, alias := range aliases {
		b := p.Bindings[alias]
		if err := validatePolicy(alias, b); err != nil {
			return nil, err
		}
		provider, err := a.resolveIdentity(c.Fleet, c.Deployment, b.Provider, true)
		if err != nil {
			return nil, err
		}
		b.Provider, _ = json.Marshal(provider)
		bindings[alias] = b
	}
	retirements, err := a.dir(c.Fleet, "retirements")
	if err != nil {
		return nil, err
	}
	ownerPath := filepath.Join(retirements, digest(consumer, ownerRef)+".json")
	unlockOwner := a.lock(ownerPath)
	defer unlockOwner()
	if _, err := os.Lstat(ownerPath); err == nil {
		return nil, fmt.Errorf("this logical dependency owner is retiring or retired")
	}
	dir, err := a.dir(c.Fleet, "bindings")
	if err != nil {
		return nil, err
	}
	recipe := bindingRecipe{Consumer: consumer, OwnerRef: ownerRef, OperationID: operationID, Bindings: bindings}
	path := filepath.Join(dir, digest(consumer, operationID)+".json")
	unlock := a.lock(path)
	defer unlock()
	var record bindingRecord
	exists, err := readJournal(path, &record)
	if err != nil {
		return nil, err
	}
	if exists {
		want, _ := json.Marshal(recipe)
		have, _ := json.Marshal(record.Recipe)
		if record.Protocol != 1 || (record.Phase != "binding" && record.Phase != "bound") || string(want) != string(have) {
			return nil, fmt.Errorf("binding operation belongs to another recipe or needs recovery")
		}
	} else {
		if len(journals(dir)) >= 4096 {
			return nil, fmt.Errorf("live dependency journal is full")
		}
		record = bindingRecord{Protocol: 1, Recipe: recipe, Phase: "binding", Renewals: map[string]*receipt{}}
		if err := a.plan(ctx, c.Fleet, &record); err != nil {
			return nil, err
		}
		if err := writeJournal(path, record); err != nil {
			return nil, err
		}
	}
	if err := a.live(ctx, c.Fleet, consumer); err != nil {
		return nil, err
	}
	// Acquire/observe all sessions before issuing grants.
	for _, alias := range sortedKeys(record.Plan.Sessions) {
		s, err := a.acquire(ctx, c.Fleet, *record.Plan.Sessions[alias])
		if err != nil {
			return nil, err
		}
		if s.Receipt == nil || s.Receipt.State != "active" {
			return nil, fmt.Errorf("original resource session is unavailable; explicit recovery required")
		}
		session, _ := json.Marshal(s.Receipt.SessionID)
		for method, argument := range bindings[alias].Resource.Arguments {
			current := record.Plan.Requests[alias].Methods[method].Bound[argument]
			if string(current) != strconv.Quote(pendingSession) && string(current) != string(session) {
				return nil, fmt.Errorf("original resource session identity changed")
			}
			record.Plan.Requests[alias].Methods[method].Bound[argument] = session
		}
		if err := writeJournal(path, record); err != nil {
			return nil, err
		}
	}
	delivered := map[string]any{}
	for _, alias := range aliases {
		q := record.Plan.Requests[alias]
		grant, err := a.issue(ctx, c.Fleet, q)
		if err != nil {
			return nil, err
		}
		if previous := record.Renewals[alias]; previous != nil && (previous.GrantID != grant["grant_id"] || previous.State == "expired" || previous.State == "revoked") {
			return nil, fmt.Errorf("original binding cannot be replaced or revived")
		}
		record.Renewals[alias] = &receipt{GrantID: str(grant["grant_id"]), Consumer: q.Consumer.public(c.Fleet),
			Provider: q.Provider.public(c.Fleet), Expires: int64(num(grant["expires"]))}
		if err := writeJournal(path, record); err != nil {
			return nil, err
		}
		delivered[alias] = grant
	}
	if err := a.live(ctx, c.Fleet, consumer); err != nil {
		return nil, err
	}
	record.Phase = "bound"
	if err := writeJournal(path, record); err != nil {
		return nil, err
	}
	return map[string]any{"protocol": 1, "owner_ref": ownerRef, "operation_id": operationID,
		"consumer": consumer, "bindings": delivered}, nil
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// plan checks every binding against the installed manifests (former _plan).
func (a *Allocator) plan(ctx context.Context, fleet string, record *bindingRecord) error {
	consumer := record.Recipe.Consumer
	if err := a.live(ctx, fleet, consumer); err != nil {
		return err
	}
	manifest, err := a.installedManifest(ctx, fleet, consumer)
	if err != nil {
		return err
	}
	declared, _ := manifest["dependencies"].(map[string]any)
	if num(manifest["apiVersion"]) != 2 || declared == nil {
		return fmt.Errorf("live bindings require declared App dependencies")
	}
	record.Plan.Requests, record.Plan.Sessions = map[string]*grantRequest{}, map[string]*sessionRecipe{}
	for alias, b := range record.Recipe.Bindings {
		dependency, _ := declared[b.AppID].(map[string]any)
		if err := bindingPhase(dependency); err != nil {
			return err
		}
		var provider identity
		_ = json.Unmarshal(b.Provider, &provider)
		provided, err := a.installedManifest(ctx, fleet, provider)
		if err != nil {
			return err
		}
		constraint, _ := dependency["range"].(string)
		if constraint == "" {
			constraint = "*"
		}
		compatible, err := compatibleVersion(str(provided["version"]), constraint)
		if num(provided["apiVersion"]) != 2 || provided["id"] != b.AppID || err != nil || !compatible {
			return fmt.Errorf("provider does not match the consumer dependency declaration")
		}
		methods := map[string]rule{}
		for name, r := range b.Methods {
			bound := map[string]json.RawMessage{}
			for k, v := range r.Bound {
				bound[k] = v
			}
			methods[name] = rule{Arguments: append([]string{}, r.Arguments...), Bound: bound}
		}
		if b.Resource != nil {
			for method, argument := range b.Resource.Arguments {
				methods[method].Bound[argument] = json.RawMessage(strconv.Quote(pendingSession))
			}
			record.Plan.Sessions[alias] = &sessionRecipe{Consumer: consumer, OperationID: "resource-" + digest(consumer, record.Recipe.OwnerRef, alias),
				OwnerRef: digest(fleet, consumer, record.Recipe.OwnerRef), Provider: provider, AppID: b.AppID, Kind: b.Resource.Kind}
		}
		uses, _ := dependency["uses"].([]any)
		if err := checkMethods(uses, provided, methods); err != nil {
			return err
		}
		record.Plan.Requests[alias] = &grantRequest{OperationID: "live-" + digest(consumer, record.Recipe.OperationID, alias),
			Consumer: consumer, Provider: provider, AppID: b.AppID, Methods: methods}
	}
	return nil
}

func bindingPhase(dependency map[string]any) error {
	if dependency == nil {
		return fmt.Errorf("dependency must declare a supported binding phase and interface contract")
	}
	for k := range dependency {
		if k != "range" && k != "uses" && k != "binding" {
			return fmt.Errorf("dependency must declare a supported binding phase and interface contract")
		}
	}
	if phase, ok := dependency["binding"]; ok && phase != "startup" && phase != "runtime" {
		return fmt.Errorf("dependency must declare a supported binding phase and interface contract")
	}
	return nil
}

func parseVersion(v string) ([3]int, error) {
	m := versionRE.FindStringSubmatch(v)
	if m == nil {
		return [3]int{}, fmt.Errorf("dependency assembly requires a stable three-part version")
	}
	var out [3]int
	for i := range 3 {
		out[i], _ = strconv.Atoi(m[i+1])
	}
	return out, nil
}

func compareVersion(a, b [3]int) int {
	for i := range 3 {
		if a[i] != b[i] {
			if a[i] < b[i] {
				return -1
			}
			return 1
		}
	}
	return 0
}

// compatibleVersion: "*", exact, >=, ^ and ~ ranges (former _compatible).
func compatibleVersion(version, constraint string) (bool, error) {
	actual, err := parseVersion(version)
	if err != nil {
		return false, err
	}
	if constraint == "*" {
		return true, nil
	}
	prefix := ""
	for _, p := range []string{">=", "^", "~"} {
		if strings.HasPrefix(constraint, p) {
			prefix = p
			break
		}
	}
	minimum, err := parseVersion(constraint[len(prefix):])
	if err != nil {
		return false, err
	}
	var maximum [3]int
	switch {
	case prefix == "":
		return actual == minimum, nil
	case prefix == ">=":
		return compareVersion(actual, minimum) >= 0, nil
	case prefix == "~":
		maximum = [3]int{minimum[0], minimum[1] + 1, 0}
	case minimum[0] > 0:
		maximum = [3]int{minimum[0] + 1, 0, 0}
	case minimum[1] > 0:
		maximum = [3]int{0, minimum[1] + 1, 0}
	default:
		maximum = [3]int{0, 0, minimum[2] + 1}
	}
	return compareVersion(minimum, actual) <= 0 && compareVersion(actual, maximum) < 0, nil
}

// checkMethods allows only methods of the declared interfaces, each with
// exactly its parameters (former _methods).
func checkMethods(uses []any, provided map[string]any, requested map[string]rule) error {
	bad := fmt.Errorf("provider does not satisfy the declared dependency interface or method arguments")
	provides, _ := provided["provides"].(map[string]any)
	interfaces := map[string][]string{}
	for _, item := range orList(provides["interfaces"]) {
		iface, _ := item.(map[string]any)
		version := 1.0
		if v, ok := iface["version"]; ok {
			version = num(v)
		}
		key := str(iface["name"]) + "@" + strconv.Itoa(int(version))
		if _, dup := interfaces[key]; dup || version < 1 {
			return bad
		}
		interfaces[key] = strings_(iface["tools"])
	}
	tools := map[string][]map[string]any{}
	for _, item := range orList(provides["tools"]) {
		tool, _ := item.(map[string]any)
		var params []map[string]any
		for _, p := range orList(tool["params"]) {
			param, _ := p.(map[string]any)
			if !argumentRE.MatchString(str(param["name"])) {
				return bad
			}
			params = append(params, param)
		}
		tools[str(tool["name"])] = params
	}
	allowed := map[string]bool{}
	if len(uses) == 0 {
		return bad
	}
	for _, u := range uses {
		names, ok := interfaces[str(u)]
		if !ok {
			return bad
		}
		for _, n := range names {
			allowed[n] = true
		}
	}
	if len(requested) == 0 || len(requested) > 64 {
		return bad
	}
	for method, r := range requested {
		params, ok := tools[method]
		if !allowed[method] || !rpcRE.MatchString(method) || !ok || r.Arguments == nil || r.Bound == nil {
			return bad
		}
		selected := map[string]bool{}
		for _, arg := range r.Arguments {
			if selected[arg] {
				return bad
			}
			selected[arg] = true
		}
		for arg := range r.Bound {
			if selected[arg] {
				return bad
			}
			selected[arg] = true
		}
		names := map[string]bool{}
		for _, p := range params {
			names[str(p["name"])] = true
			required, has := p["required"].(bool)
			if (!has || required) && !selected[str(p["name"])] {
				return bad
			}
		}
		for arg := range selected {
			if !names[arg] {
				return bad
			}
		}
		if len(selected) > 64 {
			return bad
		}
	}
	return nil
}

// issue mints (or, for the same operation, recovers) the grant and returns
// it as consumers receive it.
func (a *Allocator) issue(ctx context.Context, fleet string, q *grantRequest) (map[string]any, error) {
	methods := map[string]appgateway.RPCMethod{}
	for name, r := range q.Methods {
		methods[name] = appgateway.RPCMethod{Arguments: r.Arguments, Bound: r.Bound}
	}
	result, err := a.Grants.IssueDependency(ctx, appgateway.DependencyRequest{Operation: q.OperationID,
		Consumer: q.Consumer.instance(fleet), Provider: q.Provider.binding(fleet), AppID: q.AppID, Methods: methods,
		Expires: a.now().Add(15 * time.Minute).Unix(), Timeout: appgateway.MaxDependencyTimeout})
	if err != nil {
		return nil, err
	}
	return map[string]any{"endpoint": result["endpoint"], "access_token": result["access_token"], "grant_id": result["grant_id"],
		"expires": result["expires"], "consumer": q.Consumer.public(fleet), "provider": q.Provider.public(fleet)}, nil
}

func gone(err error) bool {
	var e *appgateway.DependencyError
	return errors.As(err, &e) && e.Status == 410
}
