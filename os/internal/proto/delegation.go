package proto

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

// DelegateRequest authorizes one allocation using the connector's identity.
// No user API key or credential is sent to the HPC cluster.
type DelegateRequest struct {
	RefreshToken string `json:"refresh_token"`
	TS           int64  `json:"ts"`
	Sig          string `json:"sig"`
	Allocation   string `json:"allocation"`
}
type DelegateResponse struct {
	NodeID    string `json:"node_id"`
	Creds     string `json:"creds"`
	ExpiresAt int64  `json:"expires_at"`
}

func DelegatedNodeID(fleet, parent, allocation string) string {
	b, _ := json.Marshal([]string{fleet, parent, allocation})
	h := sha256.Sum256(b)
	return "hpc_" + hex.EncodeToString(h[:20])
}

// Bind the signature to this operation and allocation, not just a timestamp.
func DelegateChallenge(parentPub, fleet, allocation string, ts int64) string {
	b, _ := json.Marshal([]any{"fleet-delegate-v1", parentPub, fleet, allocation, ts})
	return string(b)
}

type Delegation struct {
	ConnectorID string `json:"connector_id"`
	ClusterID   string `json:"cluster_id"`
	JobID       string `json:"job_id"`
	Allocation  string `json:"allocation"`
	State       string `json:"state"`
	Reason      string `json:"reason,omitempty"`
}
