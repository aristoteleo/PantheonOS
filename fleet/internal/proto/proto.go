// Package proto defines the shared wire types and the NATS subject layout for
// Pantheon-Fleet. These types travel over the control plane (NATS) between the
// Controller, the Runners, and the Agent's Fleet toolset.
package proto

import "time"

// Node status values.
const (
	StatusOnline  = "online"
	StatusBusy    = "busy"
	StatusOffline = "offline"
)

// Reachability describes how a Node can be reached on the data plane.
const (
	ReachDirect = "direct"
	ReachRelay  = "relay"
)

// Task kinds select how a Task's code is executed.
const (
	TaskShell  = "shell"
	TaskPython = "python"
)

// Node is the record a Runner publishes into the Registry (JetStream KV) and
// that the Agent reads to "see" the Fleet.
type Node struct {
	NodeID     string     `json:"node_id"`
	Name       string     `json:"name"`
	Labels     []string   `json:"labels,omitempty"`
	Capability Capability `json:"capability"`
	State      State      `json:"state"`
	Net        Net        `json:"net"`
	Version    string     `json:"version"`
	LastSeen   time.Time  `json:"last_seen"`
}

// Capability is the (mostly static) description of a Node.
type Capability struct {
	OS         string   `json:"os"`
	Arch       string   `json:"arch"`
	CPUCores   int      `json:"cpu_cores"`
	GPU        string   `json:"gpu,omitempty"`
	RAMGB      float64  `json:"ram_gb"`
	DiskFreeGB float64  `json:"disk_free_gb"`
	Tools      []string `json:"tools,omitempty"`
}

// State is the live, frequently-changing part of a Node.
type State struct {
	Status       string   `json:"status"`
	Load         Load     `json:"load"`
	RunningTasks []string `json:"running_tasks,omitempty"`
}

// Load is a normalized [0,1] resource-usage snapshot.
type Load struct {
	CPU float64 `json:"cpu"`
	Mem float64 `json:"mem"`
	GPU float64 `json:"gpu"`
}

// Net describes the Node's data-plane addressing.
type Net struct {
	PublicIP     string   `json:"public_ip,omitempty"`
	NATType      string   `json:"nat_type,omitempty"`
	Multiaddrs   []string `json:"multiaddrs,omitempty"`
	Reachability string   `json:"reachability,omitempty"`
}

// Command is the envelope sent to a Node's cmd subject. A transfer command is
// sent to the *source* Node, which then streams to the destination.
type Command struct {
	Type     string           `json:"type"` // run_task | transfer | cancel | ping
	Task     *Task            `json:"task,omitempty"`
	Transfer *TransferRequest `json:"transfer,omitempty"`
}

// Task is a single code execution request (Agent -> Node).
type Task struct {
	TaskID   string            `json:"task_id"`
	Kind     string            `json:"kind"` // shell | python
	Code     string            `json:"code"`
	Cwd      string            `json:"cwd,omitempty"`
	Env      map[string]string `json:"env,omitempty"`
	TimeoutS int               `json:"timeout_s,omitempty"`
}

// TaskResult is returned to the caller of a Task.
type TaskResult struct {
	TaskID   string `json:"task_id"`
	ExitCode int    `json:"exit_code"`
	Stdout   string `json:"stdout"`
	Stderr   string `json:"stderr"`
	Error    string `json:"error,omitempty"`
}

// TransferRequest schedules a bulk data move between two Nodes.
type TransferRequest struct {
	TransferID string          `json:"transfer_id"`
	SrcNode    string          `json:"src_node"`
	SrcPath    string          `json:"src_path"`
	DstNode    string          `json:"dst_node"`
	DstPath    string          `json:"dst_path"`
	Options    TransferOptions `json:"options,omitempty"`
}

// TransferOptions tune a Transfer.
type TransferOptions struct {
	Compress string `json:"compress,omitempty"` // zstd | none
	Verify   string `json:"verify,omitempty"`   // sha256 | none
	Resume   bool   `json:"resume,omitempty"`
}

// TransferProgress is published periodically during a Transfer.
type TransferProgress struct {
	TransferID string `json:"transfer_id"`
	State      string `json:"state"` // pending|connecting|transferring|verifying|done|failed
	BytesDone  int64  `json:"bytes_done"`
	BytesTotal int64  `json:"bytes_total"`
	RateBps    int64  `json:"rate_bps"`
	Path       string `json:"path"` // direct | relay
	SHA256     string `json:"sha256,omitempty"`
	Error      string `json:"error,omitempty"`
}

// --- NATS subject layout (all scoped by fleet id) ---

// SubjNodeCmd is the request/reply subject for commands to a Node.
func SubjNodeCmd(fleet, node string) string { return "fleet." + fleet + ".node." + node + ".cmd" }

// SubjNodeEvent is where a Node publishes events (status, errors).
func SubjNodeEvent(fleet, node string) string { return "fleet." + fleet + ".node." + node + ".event" }

// SubjTaskOutput carries streamed stdout/stderr and the final result of a Task.
func SubjTaskOutput(fleet, task string) string { return "fleet." + fleet + ".task." + task + ".output" }

// SubjTransferSignal is the data-plane signaling subject (peers exchange addrs).
func SubjTransferSignal(fleet, id string) string {
	return "fleet." + fleet + ".transfer." + id + ".signal"
}

// SubjTransferProgress carries progress for a Transfer.
func SubjTransferProgress(fleet, id string) string {
	return "fleet." + fleet + ".transfer." + id + ".progress"
}

// RegistryBucket is the JetStream KV bucket holding a Fleet's Node records.
func RegistryBucket(fleet string) string { return "FLEET_" + fleet + "_NODES" }

// --- Controller join ---

// JoinRequest is what a Runner POSTs to the Controller's /join endpoint. Either
// Key (legacy) or JoinToken (single-use, preferred) selects the fleet.
type JoinRequest struct {
	Key       string `json:"key,omitempty"`
	JoinToken string `json:"join_token,omitempty"`
	// NodePub is the node's base64 Ed25519 public key. The Controller binds it
	// into the returned refresh token so only the holder of the matching private
	// key can later refresh (proof-of-possession). See docs/fleet-security-model.md.
	NodePub string `json:"node_pub,omitempty"`
	// NodeID is this node's stable id. When present the Controller mints a NARROW
	// per-node credential (scoped to fleet.<fid>.node.<NodeID>.cmd + its own
	// registry key) instead of the broad agent credential, so a compromised node
	// cannot command its peers. Absent for the Agent, which needs broad access.
	NodeID string `json:"node_id,omitempty"`
}

// JoinTokenRequest asks the Controller to mint a single-use join token. In P0
// this is authorized like /join (the key resolves the fleet); Increment D moves
// it behind the platform session.
type JoinTokenRequest struct {
	Key string `json:"key"`
}

// JoinTokenResponse is a single-use, short-lived token that adds one machine.
type JoinTokenResponse struct {
	JoinToken string `json:"join_token"`
	ExpiresAt int64  `json:"expires_at"`
}

// RevokeRequest revokes a node's ability to refresh: the Controller records the
// node's public key on a revocation list, so /token refuses it and its current
// short-lived credential expires within the TTL. NodeID is looked up in the
// registry when NodePub isn't supplied directly.
type RevokeRequest struct {
	NodePub string `json:"node_pub,omitempty"`
	NodeID  string `json:"node_id,omitempty"`
}

// JoinResponse is what the Controller returns: the Fleet the key maps to, how
// to reach the control plane, and the data-plane relays. Creds (a scoped NATS
// credential) is short-lived; RefreshToken is used with the node key to mint
// fresh creds via /token without re-presenting the API key.
type JoinResponse struct {
	FleetID      string   `json:"fleet_id"`
	NatsURL      string   `json:"nats_url"`
	Relays       []string `json:"relays,omitempty"`
	Creds        string   `json:"creds,omitempty"`
	RefreshToken string   `json:"refresh_token,omitempty"`
}

// TokenRequest is what a Runner POSTs to /token to refresh its credential. It
// carries the refresh token plus a proof-of-possession: a signature (with the
// node key) over PoPChallenge(node_id, fleet_id, ts).
type TokenRequest struct {
	RefreshToken string `json:"refresh_token"`
	TS           int64  `json:"ts"`  // unix seconds the challenge was signed at
	Sig          string `json:"sig"` // base64 node-key signature over the challenge
}

// TokenResponse returns a fresh short-lived credential (and, on rotation, a new
// refresh token).
type TokenResponse struct {
	Creds        string `json:"creds"`
	RefreshToken string `json:"refresh_token,omitempty"`
}
