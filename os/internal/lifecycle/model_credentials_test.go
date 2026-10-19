package lifecycle

import (
	"path/filepath"
	"testing"
)

func TestModelCredentialRootIsFleetBound(t *testing.T) {
	m := &Manager{root: t.TempDir(), owner: "fleet", node: "node", rpcSecret: []byte("test")}
	for _, app := range []string{"model-service", "another-app"} {
		c := m.boundComponent(Component{Env: map[string]string{"PANTHEON_MODEL_CREDENTIALS": "/caller/chosen"}}, &Instance{AppID: app, ID: "instance", Digest: "digest", Generation: 1})
		if app == "model-service" {
			if c.Env["PANTHEON_MODEL_CREDENTIALS"] != filepath.Join(m.root, "model-credentials") {
				t.Fatal("manifest replaced credential root")
			}
		} else if _, ok := c.Env["PANTHEON_MODEL_CREDENTIALS"]; ok {
			t.Fatal("unrelated app inherited credential root")
		}
	}
}
