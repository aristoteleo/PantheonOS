package childenv

import (
	"strings"
	"testing"
)

func TestCredentialsStayWithTheNode(t *testing.T) {
	got := Filter([]string{"PATH=/bin", "FLEET_KEY=k", "MODAL_TOKEN_SECRET=s", "LLM_API_KEY=mine",
		"PANTHEON_APP_CONFIG=c", "nats_jwt=lower", "HOME=/root"})
	want := []string{"PATH=/bin", "LLM_API_KEY=mine", "HOME=/root"}
	if len(got) != len(want) {
		t.Fatalf("%v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%v", got)
		}
	}
}

func TestCommandsGetTheNodesWorkspaceEnvironmentWithoutItsCredentials(t *testing.T) {
	t.Setenv("PYTHONPATH", "/node/python")
	t.Setenv("FLEET_KEY", "node-secret")
	encoded := EncodeWorkspace()
	t.Setenv("PYTHONPATH", "")
	t.Setenv(WorkspaceVariable, encoded)
	if got := Getenv("PYTHONPATH"); got != "/node/python" {
		t.Fatal(got)
	}
	for _, variable := range Workspace() {
		if strings.HasPrefix(variable, "FLEET_KEY=") || strings.HasPrefix(variable, WorkspaceVariable+"=") {
			t.Fatal(variable)
		}
	}
	// A credential that reaches the App's variable is still withheld.
	t.Setenv(WorkspaceVariable, `["PATH=/bin","NATS_JWT=jwt"]`)
	if got := Workspace(); len(got) != 1 || got[0] != "PATH=/bin" {
		t.Fatal(got)
	}
}

func TestAnOlderNodesUnexpandedPlaceholderLeavesTheAppsOwnEnvironment(t *testing.T) {
	t.Setenv("PYTHONPATH", "/app/own")
	t.Setenv(WorkspaceVariable, "${WORKSPACE_ENV}")
	if got := Getenv("PYTHONPATH"); got != "/app/own" {
		t.Fatal(got)
	}
	if got := Getenv(WorkspaceVariable); got != "" {
		t.Fatal(got)
	}
}
