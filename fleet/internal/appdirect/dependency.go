package appdirect

import (
	"context"
	"encoding/hex"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

// Dependency is private node control data, never part of the public QUIC grant.
// Proof authorizes only a read-only check of this exact grant at the node's
// configured Controller. HTTP authority and consumer lifetime remain pinned.
type Dependency struct {
	ID       string                        `json:"grant_id"`
	Proof    string                        `json:"proof"`
	Consumer apptransport.InstanceIdentity `json:"consumer"`
	HTTP     apptransport.HTTPDependency   `json:"http"`
}

func (d *Dependency) Valid(q Request) bool {
	if len(d.ID) != 64 || len(d.Proof) != 64 || !d.Consumer.Valid() || d.Consumer.Fleet != q.Fleet || !d.HTTP.Valid() || d.HTTP.NodeBound || d.HTTP.Credential != q.Credential {
		return false
	}
	_, a := hex.DecodeString(d.ID)
	_, b := hex.DecodeString(d.Proof)
	return a == nil && b == nil
}

type DependencyCheck func(context.Context, Request) error

// Install before accepting grants. Older nodes reject the additional field
// during strict decoding instead of silently issuing broader authority.
func (s *Server) SetDependencyCheck(check DependencyCheck) { s.dependencyCheck = check }

func (s *Server) authorized(ctx context.Context, q Request) bool {
	if q.Dependency == nil {
		return true
	}
	return s.dependencyCheck != nil && q.Dependency.Valid(q) && s.dependencyCheck(ctx, q) == nil && ctx.Err() == nil
}
