package node

import (
	"crypto/ed25519"
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
)

// LoadOrCreateKey returns this node's stable Ed25519 private key, persisting the
// seed under stateDir/node.key (0600). The private key NEVER leaves the node — it
// proves possession when refreshing credentials, so a leaked refresh token is
// useless without it. See docs/fleet-security-model.md.
func LoadOrCreateKey(stateDir string) (ed25519.PrivateKey, error) {
	path := filepath.Join(stateDir, "node.key")
	if b, err := os.ReadFile(path); err == nil {
		if seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(b))); err == nil && len(seed) == ed25519.SeedSize {
			return ed25519.NewKeyFromSeed(seed), nil
		}
	}
	_, priv, err := ed25519.GenerateKey(nil)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return nil, err
	}
	enc := base64.StdEncoding.EncodeToString(priv.Seed())
	if err := os.WriteFile(path, []byte(enc+"\n"), 0o600); err != nil {
		return nil, err
	}
	return priv, nil
}

// PubB64 is the base64 (std) public key of priv — sent to the Controller on join.
func PubB64(priv ed25519.PrivateKey) string {
	return base64.StdEncoding.EncodeToString(priv.Public().(ed25519.PublicKey))
}

// Sign returns a base64 (std) Ed25519 signature over msg.
func Sign(priv ed25519.PrivateKey, msg string) string {
	return base64.StdEncoding.EncodeToString(ed25519.Sign(priv, []byte(msg)))
}
