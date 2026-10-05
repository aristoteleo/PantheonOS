package lifecycle

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

// AppDependencyGrant is a short-lived owner-issued capability, never a provider
// secret or a Fleet management credential. Only ConfigureApp's authenticated
// owner control plane can deliver it. The gateway remains the authority on
// signature/token validity, revocation, provider liveness and admission.
type AppDependencyGrant struct {
	Endpoint string                        `json:"endpoint"`
	Token    string                        `json:"access_token"`
	ID       string                        `json:"grant_id"`
	Expires  int64                         `json:"expires"`
	Consumer apptransport.InstanceIdentity `json:"consumer"`
	Provider apptransport.Binding          `json:"provider"`
}

func (g AppDependencyGrant) validate() error {
	return g.validateWithRPCOrigin("")
}

func (g AppDependencyGrant) validateWithRPCOrigin(localOrigin string) error {
	sum := sha256.Sum256([]byte(g.Token))
	u, err := url.Parse(g.Endpoint)
	// Match the gateway's generation-specific hostname; never pair the token
	// with a caller-substituted path, query, userinfo or unrelated instance.
	host := sha256.Sum256([]byte(g.Provider.Instance + ":backend:http:" + fmt.Sprint(g.Provider.Generation)))
	boundOrigin := err == nil && u.Port() == "" && strings.HasPrefix(u.Hostname(), hex.EncodeToString(host[:16])+".")
	if apptransport.ValidLocalRPCOrigin(localOrigin) && g.Endpoint == localOrigin+"/rpc" {
		boundOrigin = true
	}
	if !g.Consumer.Valid() || !g.Provider.Valid() || g.Consumer.Fleet != g.Provider.Fleet ||
		g.Provider.Component != "backend" || g.Provider.Port != "http" ||
		!digestRE.MatchString(g.Token) || g.ID != hex.EncodeToString(sum[:]) ||
		g.Expires <= time.Now().Unix() || g.Expires > time.Now().Add(15*time.Minute).Unix() ||
		err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" ||
		u.Path != "/rpc" || u.RawPath != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" ||
		!boundOrigin ||
		strings.ContainsAny(u.Host, "\\\r\n\t ") {
		return fmt.Errorf("invalid App dependency credential")
	}
	return nil
}

// SetLocalDependencyRPC is set by the trusted product launcher before serving
// owner commands. A manifest/configuration cannot opt itself into this origin.
func (m *Manager) SetLocalDependencyRPC(origin string) error {
	if !apptransport.ValidLocalRPCOrigin(origin) {
		return fmt.Errorf("invalid local dependency RPC origin")
	}
	m.serial.Lock()
	defer m.serial.Unlock()
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.localRPCOrigin != "" && m.localRPCOrigin != origin {
		return fmt.Errorf("local dependency RPC origin already fixed")
	}
	m.localRPCOrigin = origin
	return nil
}

func hasDependencyGrants(c AppConfiguration) bool {
	for _, component := range c.Components {
		if len(component.Dependencies) != 0 {
			return true
		}
	}
	return false
}

func (m *Manager) validateDependencyConsumers(in *Instance, c AppConfiguration) error {
	expected := apptransport.InstanceIdentity{Fleet: m.owner, Node: m.node, Instance: in.ID, Revision: in.Digest, Generation: in.Generation + 1}
	for _, component := range c.Components {
		for _, grant := range component.Dependencies {
			if grant.Consumer != expected {
				return fmt.Errorf("App dependency credential belongs to a different start")
			}
		}
	}
	return nil
}
