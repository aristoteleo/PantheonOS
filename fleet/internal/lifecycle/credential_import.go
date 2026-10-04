package lifecycle

import (
	"path/filepath"

	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
)

// CredentialImport is an owner-control operation, not an App invocation. The
// owner/node/root come exclusively from this authenticated Manager. Neither
// ciphertext nor plaintext enters the public operation ledger.
func (m *Manager) CredentialImport(q Command) (any, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return nil, modelcredentials.ErrImport
	}
	if m.credentialImporter == nil {
		m.credentialImporter = modelcredentials.NewImporter(filepath.Join(m.root, "model-credentials"), m.owner, m.node)
	}
	switch q.Method {
	case "credential_prepare":
		if q.CredentialChallenge != "" || q.CredentialEnvelope != nil {
			return nil, modelcredentials.ErrImport
		}
		return m.credentialImporter.Prepare(q.CredentialRef, q.CredentialEndpoint)
	case "credential_ensure":
		if q.CredentialRef != "" || q.CredentialEndpoint != "" || q.CredentialEnvelope == nil {
			return nil, modelcredentials.ErrImport
		}
		if err := m.credentialImporter.Ensure(q.CredentialChallenge, *q.CredentialEnvelope); err != nil {
			return nil, err
		}
		return map[string]bool{"ok": true}, nil
	default:
		return nil, modelcredentials.ErrImport
	}
}
