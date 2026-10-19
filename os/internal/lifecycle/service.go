package lifecycle

import (
	"fmt"
	"net/url"
)

// Service resolves a declared port on the exact serving instance generation.
// During drain the App controls admission, while existing editors and save
// callbacks must remain reachable. A blocked stop has not killed the service.
// Callers cannot supply a loopback address, port number or arbitrary proxy URL.
func (m *Manager) Service(id, revision string, generation uint64, component, port string) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	in := m.ledger.Instances[id]
	if m.closed || in == nil || (in.State != "ready" && in.State != "draining" && in.State != "stop_blocked" && in.State != "recovered") || in.Digest != revision || in.Generation != generation {
		return "", fmt.Errorf("App instance is not ready at the requested revision/generation")
	}
	install := m.ledger.Installations[in.Digest]
	if install == nil || install.State != "installed" {
		return "", fmt.Errorf("App installation is unavailable")
	}
	declared := false
	for _, c := range install.Definition.Components {
		if c.Name == component {
			_, declared = c.Ports[port]
		}
	}
	if !declared {
		return "", fmt.Errorf("App service is not declared")
	}
	for _, r := range in.Resources {
		if r.Component != component {
			continue
		}
		endpoint := r.Endpoints[port]
		u, err := url.Parse(endpoint)
		if err == nil && u.Scheme == "http" && u.Hostname() == "127.0.0.1" && u.Port() != "" && u.Path == "" && u.User == nil && u.RawQuery == "" && u.Fragment == "" {
			return endpoint, nil
		}
	}
	return "", fmt.Errorf("App service has no registered loopback endpoint")
}
