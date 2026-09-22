package lifecycle

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
)

// The seed is node-local, outside artifacts and the public lifecycle ledger.
// Instance RPC credentials only travel from Runner to its owned component.
func (m *Manager) loadRPCSecret() error {
	p := filepath.Join(m.root, "rpc-secret")
	b, err := os.ReadFile(p)
	if os.IsNotExist(err) {
		b = make([]byte, 32)
		if _, err = rand.Read(b); err != nil {
			return err
		}
		f, err := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		if _, err = f.Write(b); err == nil {
			err = f.Sync()
		}
		closeErr := f.Close()
		if err != nil {
			return err
		}
		if closeErr != nil {
			return closeErr
		}
	} else if err != nil {
		return err
	}
	if len(b) != 32 {
		return fmt.Errorf("invalid node RPC secret; recover it before restarting owned apps")
	}
	m.rpcSecret = b
	return nil
}
func (m *Manager) rpcCredential(id, revision string, generation uint64) string {
	mac := hmac.New(sha256.New, m.rpcSecret)
	fmt.Fprintf(mac, "%s\x00%s\x00%s\x00%s\x00%d", m.owner, m.node, id, revision, generation)
	return hex.EncodeToString(mac.Sum(nil))
}
func (m *Manager) RPCCredential(id, revision string, generation uint64) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	in := m.ledger.Instances[id]
	if m.closed || in == nil || in.Digest != revision || in.Generation != generation {
		return "", fmt.Errorf("stale App RPC generation")
	}
	return m.rpcCredential(id, revision, generation), nil
}
