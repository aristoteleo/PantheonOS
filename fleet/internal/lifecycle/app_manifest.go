package lifecycle

import (
	"archive/tar"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
)

// InstalledManifest reads only the digest-verified code artifact, not mutable
// extracted files or a catalog's latest release. It is an owner query shared by
// native nodes and job workers. Installation need not have app.json for legacy
// Apps; dependency assembly requires one and fails explicitly when absent.
func (m *Manager) InstalledManifest(revision string) (any, error) {
	installed := func() (Definition, bool) {
		m.mu.Lock()
		defer m.mu.Unlock()
		i := m.ledger.Installations[revision]
		if m.closed || i == nil || i.State != "installed" {
			return Definition{}, false
		}
		return clone(i.Definition), true
	}
	def, ok := installed()
	if !ok {
		return nil, fmt.Errorf("App manifest requires an installed revision")
	}
	b, err := m.ArtifactBytes(revision)
	if err != nil {
		return nil, fmt.Errorf("installed App artifact cannot be verified")
	}
	decoded, err := openArtifact(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	defer decoded.Close()
	r := tar.NewReader(decoded)
	manifests := map[string]json.RawMessage{}
	for {
		h, err := r.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("invalid App artifact")
		}
		if h.Name != "app.json" && h.Name != "atrium.json" {
			continue
		}
		if _, exists := manifests[h.Name]; exists || h.Typeflag != tar.TypeReg || h.Size > MaxRPC || h.Size <= 0 {
			return nil, fmt.Errorf("invalid App manifest entry")
		}
		raw, err := io.ReadAll(io.LimitReader(r, MaxRPC+1))
		if err != nil || len(raw) > MaxRPC || !json.Valid(raw) {
			return nil, fmt.Errorf("invalid App manifest JSON")
		}
		manifests[h.Name] = raw
	}
	if _, err := io.Copy(io.Discard, decoded); err != nil {
		return nil, err
	}
	manifest := manifests["app.json"]
	if manifest == nil {
		manifest = manifests["atrium.json"]
	}
	var identity struct {
		ID      string `json:"id"`
		Version string `json:"version"`
	}
	if json.Unmarshal(manifest, &identity) != nil || identity.ID != def.AppID || identity.Version != def.Version {
		return nil, fmt.Errorf("App manifest is absent or differs from installed execution identity")
	}
	if _, ok := installed(); !ok {
		return nil, fmt.Errorf("App installation changed during manifest read")
	}
	return map[string]any{"protocol": 1, "revision": revision, "manifest": manifest, "definition": def}, nil
}
