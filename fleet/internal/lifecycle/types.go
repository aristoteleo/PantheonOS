// Package lifecycle implements the versioned, durable App control protocol.
// A Fleet is the ownership boundary; neither callers nor manifests choose it.
package lifecycle

import (
	"encoding/json"
	"fmt"
	"io"
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

const Protocol = 1

var nameRE = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,79}$`)
var digestRE = regexp.MustCompile(`^[a-f0-9]{64}$`)
var stages = map[string]bool{"before_install": true, "after_install": true, "before_start": true, "after_start": true, "before_stop": true, "after_stop": true, "before_uninstall": true, "after_uninstall": true}

// Definition is read from the verified artifact, never from lifecycle requests.
// Commands are argv. Paths in the declaration are package relative. Only the
// documented ${PACKAGE}, ${DATA}, ${INSTALL} placeholders are expanded.
type Definition struct {
	Protocol     int             `json:"protocol"`
	AppID        string          `json:"app_id"`
	Version      string          `json:"version"`
	Requires     Requirements    `json:"requires"`
	Dependencies Dependencies    `json:"dependencies,omitempty"`
	Components   []Component     `json:"components"`
	Hooks        map[string]Hook `json:"hooks,omitempty"`
}
type Dependencies struct {
	// Installing the App authorizes preparing this node-local dependency.
	// No caller-supplied download URL, socket, privileged flag or install script.
	ContainerEngine *EngineDependency `json:"container_engine,omitempty"`
}
type EngineDependency struct {
	Provider  string `json:"provider"`  // docker
	Provision string `json:"provision"` // if-missing or never
}
type Requirements struct {
	OS       []string `json:"os,omitempty"`
	Arch     []string `json:"arch,omitempty"`
	Caps     []string `json:"caps,omitempty"`
	MemoryGB float64  `json:"memory_gb,omitempty"`
	DiskGB   float64  `json:"disk_gb,omitempty"`
}
type Hook struct {
	// Running-component hooks execute inside the owned container. Only stages
	// where that component is alive can use this (after_start / before_stop).
	Component      string   `json:"component,omitempty"`
	Argv           []string `json:"argv"`
	TimeoutSeconds int      `json:"timeout_seconds"`
}
type Component struct {
	GroupNetwork     bool              `json:"group_network,omitempty"`
	groupOverlayRoot string            // Runner-only; never taken from a package
	GroupPeer        bool              `json:"group_peer,omitempty"`
	groupPeerDir     string            // internal, never decoded from manifests or persisted
	RunAsOwner       bool              `json:"run_as_owner,omitempty"` // Linux container uses Runner UID/GID, never caller-supplied IDs
	Resources        *ResourceRequest  `json:"resources,omitempty"`
	Name             string            `json:"name"`
	Runtime          string            `json:"runtime"` // process or container
	Argv             []string          `json:"argv,omitempty"`
	Image            string            `json:"image,omitempty"` // repository@sha256:...
	DependsOn        []string          `json:"depends_on,omitempty"`
	Env              map[string]string `json:"env,omitempty"`
	Ports            map[string]int    `json:"ports,omitempty"`            // container port, or 0 for Runner-assigned process port
	Mounts           map[string]string `json:"mounts,omitempty"`           // relative state path -> container path
	ReadOnlyMounts   map[string]string `json:"read_only_mounts,omitempty"` // package or cache/<relative> -> container path
	Readiness        Probe             `json:"readiness"`
	StopSeconds      int               `json:"stop_seconds,omitempty"`
}
type Probe struct {
	Argv           []string `json:"argv,omitempty"` // executed on target, or docker exec
	TimeoutSeconds int      `json:"timeout_seconds"`
}
type Request struct {
	StartPreparationID string `json:"start_preparation_id,omitempty"`
	Protocol           int    `json:"protocol"`
	OperationID        string `json:"operation_id"`
	Action             string `json:"action"`
	Digest             string `json:"digest"`
	Scope              string `json:"scope"`
	// Generation implements compare-and-swap, including retry after reconnect.
	Generation uint64      `json:"generation"`
	IfIdle     bool        `json:"if_idle,omitempty"`
	DataSource *DataSource `json:"data_source,omitempty"`
}

// A state copy never accepts a caller-supplied filesystem path. Source and
// destination share the Runner's owner/node and the request's App scope.
type DataSource struct {
	Digest     string `json:"digest"`
	Generation uint64 `json:"generation"`
}
type Receipt struct {
	Status            string   `json:"status"` // succeeded, waiting, failed
	Message           string   `json:"message,omitempty"`
	SafeToStop        bool     `json:"safe_to_stop,omitempty"`
	Checkpoints       []string `json:"checkpoints,omitempty"` // relative DATA paths
	PendingWritebacks []string `json:"pending_writebacks,omitempty"`
}
type Step struct {
	Name       string     `json:"name"`
	State      string     `json:"state"`
	StartedAt  time.Time  `json:"started_at"`
	FinishedAt *time.Time `json:"finished_at,omitempty"`
	Receipt    *Receipt   `json:"receipt,omitempty"`
}
type Operation struct {
	ModelIdleID string    `json:"model_idle_id,omitempty"` // set only by the node coordinator, never by Request
	Request     Request   `json:"request"`
	State       string    `json:"state"`
	Error       string    `json:"error,omitempty"`
	Steps       []Step    `json:"steps"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}
type Resource struct {
	Component string            `json:"component"`
	Runtime   string            `json:"runtime"`
	ID        string            `json:"id"`
	PID       int               `json:"pid,omitempty"`
	Birth     int64             `json:"birth,omitempty"`
	Endpoints map[string]string `json:"endpoints,omitempty"`
}
type Usage struct {
	Windows      int        `json:"windows"`
	Calls        int        `json:"calls"`
	IdleSince    *time.Time `json:"idle_since,omitempty"`
	GraceSeconds int        `json:"grace_seconds"`
}

type Instance struct {
	StartPreparationID string                         `json:"start_preparation_id,omitempty"`
	DataSource         *DataSource                    `json:"data_source,omitempty"`
	Reservations       map[string]ResourceReservation `json:"reservations,omitempty"`
	AutoStop           bool                           `json:"auto_stop"`
	KeepAlive          bool                           `json:"keep_alive"`
	Usage              *Usage                         `json:"usage,omitempty"`
	ID                 string                         `json:"instance_id"`
	AppID              string                         `json:"app_id"`
	Version            string                         `json:"version"`
	Digest             string                         `json:"digest"`
	Scope              string                         `json:"scope"`
	Generation         uint64                         `json:"generation"`
	// A committed ready generation proves startup hooks completed. Clear it
	// before draining so interrupted starts/stops cannot be silently adopted.
	ReadyGeneration uint64     `json:"ready_generation,omitempty"`
	State           string     `json:"state"`
	Resources       []Resource `json:"resources"`
	Error           string     `json:"error,omitempty"`
}
type Installation struct {
	Digest     string     `json:"digest"`
	Definition Definition `json:"definition"`
	State      string     `json:"state"`
}
type Ledger struct {
	ModelIdleProtocol int                      `json:"model_idle_protocol,omitempty"`
	ModelIdle         map[string]*ModelIdle    `json:"model_idle,omitempty"`
	ResourceProtocol  int                      `json:"resource_protocol,omitempty"`
	UsageProtocol     int                      `json:"usage_protocol,omitempty"`
	Protocol          int                      `json:"protocol"`
	Owner             string                   `json:"owner"`
	Node              string                   `json:"node_id"`
	Installations     map[string]*Installation `json:"installations"`
	Instances         map[string]*Instance     `json:"instances"`
	Operations        map[string]*Operation    `json:"operations"`
}

func relative(p string) bool {
	// Manifest and tar paths use '/' on every OS. IsLocal also rejects Windows
	// device names; colon/backslash rejection prevents drive and ADS paths.
	return p != "" && filepath.IsLocal(filepath.FromSlash(p)) && path.Clean(p) == p && !strings.ContainsAny(p, "\\:")
}

func (d Definition) Validate() error {
	if d.Protocol != Protocol || !nameRE.MatchString(d.AppID) || d.Version == "" {
		return fmt.Errorf("invalid lifecycle protocol, app id or version")
	}
	if len(d.Components) == 0 || len(d.Components) > 16 {
		return fmt.Errorf("declare 1..16 components")
	}
	seen := map[string]bool{}
	if dep := d.Dependencies.ContainerEngine; dep != nil {
		if dep.Provider != "docker" || (dep.Provision != "if-missing" && dep.Provision != "never") {
			return fmt.Errorf("unsupported container engine dependency")
		}
	}
	groupPeers := 0
	for _, c := range d.Components {
		if !nameRE.MatchString(c.Name) || seen[c.Name] {
			return fmt.Errorf("invalid/duplicate component %q", c.Name)
		}
		for _, dep := range c.DependsOn {
			if !seen[dep] {
				return fmt.Errorf("components must be in dependency order: %s needs %s", c.Name, dep)
			}
		}
		seen[c.Name] = true
		if c.GroupNetwork {
			if !c.GroupPeer || c.Runtime != "container" || len(c.Argv) == 0 {
				return fmt.Errorf("private collective networking requires a group peer container with argv")
			}
			capable := false
			for _, cap := range d.Requires.Caps {
				capable = capable || cap == "model-group-private-network"
			}
			if !capable {
				return fmt.Errorf("private collective networking requires explicit node admission")
			}
		}
		if c.GroupPeer {
			groupPeers++
			if groupPeers > 1 || d.AppID != "model-service" || c.Resources == nil || (c.Runtime == "container" && !c.RunAsOwner) {
				return fmt.Errorf("group peer requires one budgeted model-service component running as owner")
			}
			for key := range c.Env {
				if strings.HasPrefix(key, "PANTHEON_GROUP_") {
					return fmt.Errorf("group environment is Runner-owned")
				}
			}
			for _, mounts := range []map[string]string{c.Mounts, c.ReadOnlyMounts} {
				for _, target := range mounts {
					if target == groupPeerContainerPath || strings.HasPrefix(target, groupPeerContainerPath+"/") || strings.HasPrefix(groupPeerContainerPath, target+"/") {
						return fmt.Errorf("mount overlaps group credential directory")
					}
				}
			}
		}
		if c.Runtime != "process" && c.Runtime != "container" {
			return fmt.Errorf("unsupported runtime %q", c.Runtime)
		}
		if c.Resources != nil {
			if err := c.Resources.Validate(); err != nil {
				return err
			}
			if c.Runtime == "container" && c.Resources.MemoryBytes < 6<<20 {
				return fmt.Errorf("container memory budget must be at least 6 MiB")
			}
			for _, device := range c.Resources.Devices {
				if device.Backend == "rocm" || (c.Runtime == "container" && device.Backend != "cuda") {
					return fmt.Errorf("device binding currently supports CUDA and native Metal only")
				}
			}
		}
		if c.Runtime == "process" && (len(c.Argv) == 0 || len(c.Mounts) > 0 || len(c.ReadOnlyMounts) > 0 || c.RunAsOwner) {
			return fmt.Errorf("process needs argv; mounts are container-only")
		}
		if c.Runtime == "container" {
			if d.Dependencies.ContainerEngine == nil {
				return fmt.Errorf("container components must declare dependencies.container_engine")
			}
			parts := strings.Split(c.Image, "@sha256:")
			if len(parts) != 2 || parts[0] == "" || !digestRE.MatchString(parts[1]) {
				return fmt.Errorf("container image must be digest pinned")
			}
		}
		if len(c.Readiness.Argv) == 0 || c.Readiness.TimeoutSeconds < 1 || c.Readiness.TimeoutSeconds > 600 {
			return fmt.Errorf("component requires an explicit readiness probe (1..600s)")
		}
		if c.StopSeconds < 0 || c.StopSeconds > 600 {
			return fmt.Errorf("invalid stop timeout")
		}
		portEnv := map[string]bool{}
		for n, p := range c.Ports {
			if !nameRE.MatchString(n) || (c.Runtime == "container" && (p < 1 || p > 65535)) || (c.Runtime == "process" && p != 0) {
				return fmt.Errorf("invalid named port")
			}
			envName := strings.ToUpper(strings.ReplaceAll(n, "-", "_"))
			if portEnv[envName] {
				return fmt.Errorf("port names collide in process environment")
			}
			portEnv[envName] = true
		}
		for p, target := range c.Mounts {
			if !relative(p) || !strings.HasPrefix(target, "/") || path.Clean(target) != target || target == "/" {
				return fmt.Errorf("invalid data mount")
			}
		}
		for source, target := range c.ReadOnlyMounts {
			if (source != "package" && (!strings.HasPrefix(source, "cache/") || !relative(strings.TrimPrefix(source, "cache/")))) ||
				!strings.HasPrefix(target, "/") || path.Clean(target) != target || target == "/" || strings.ContainsAny(target, ",\\\n\r") {
				return fmt.Errorf("invalid read-only App mount")
			}
		}
	}
	for n, h := range d.Hooks {
		if !stages[n] || len(h.Argv) == 0 || h.TimeoutSeconds < 1 || h.TimeoutSeconds > 600 {
			return fmt.Errorf("invalid hook %s", n)
		}
		if h.Component != "" {
			if n != "after_start" && n != "before_stop" {
				return fmt.Errorf("container hook %s needs a running component", n)
			}
			found := false
			for _, c := range d.Components {
				if c.Name == h.Component && c.Runtime == "container" {
					found = true
				}
			}
			if !found {
				return fmt.Errorf("hook %s refers to an unknown container component", n)
			}
		}
	}
	return nil
}

// StrictDecode prevents misspelled safety/lifecycle fields being silently ignored.
func StrictDecode(b []byte, into any) error {
	decoder := json.NewDecoder(strings.NewReader(string(b)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(into); err != nil {
		return err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return fmt.Errorf("unexpected trailing JSON")
	}
	return nil
}
