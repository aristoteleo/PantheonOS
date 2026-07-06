// Package node handles a Runner's stable identity and the detection of its
// machine capability and live load.
package node

import (
	"os"
	"path/filepath"
	"strings"

	"github.com/google/uuid"
)

// Identity returns a stable node id for this machine, persisting it under
// stateDir so it survives restarts.
func Identity(stateDir string) (string, error) {
	path := filepath.Join(stateDir, "node_id")
	if b, err := os.ReadFile(path); err == nil {
		if id := strings.TrimSpace(string(b)); id != "" {
			return id, nil
		}
	}
	hex := strings.ReplaceAll(uuid.NewString(), "-", "")
	id := "n_" + hex[:20]
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return "", err
	}
	if err := os.WriteFile(path, []byte(id+"\n"), 0o644); err != nil {
		return "", err
	}
	return id, nil
}

// DefaultName returns a friendly default node name (the hostname).
func DefaultName() string {
	if h, err := os.Hostname(); err == nil && h != "" {
		return h
	}
	return "node"
}
