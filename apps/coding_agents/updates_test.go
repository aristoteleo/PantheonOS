package codingagentsapp

import (
	"os"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// TestMain keeps the package's tests off the network: no agent's latest
// version is looked up (os/codingagents tests the lookups).
func TestMain(m *testing.M) {
	os.Setenv("PANTHEON_NO_UPDATE_CHECK", "1")
	os.Exit(m.Run())
}

func TestAgentReportOffersTheNewerVersion(t *testing.T) {
	var i AgentInfo
	i.setUpdate(codingagents.UpdatePlan{Argv: []string{"/Users/u/.local/bin/npm", "install", "-g", "--prefix", "/Users/u/.local",
		"@earendil-works/pi-coding-agent@latest"}, Latest: "1.5.0", Newer: true})
	if i.UpdateTo != "1.5.0" || i.Latest != "1.5.0" ||
		i.Update != "/Users/u/.local/bin/npm install -g --prefix /Users/u/.local @earendil-works/pi-coding-agent@latest" {
		t.Fatalf("%+v", i)
	}
	// Up to date: the command stays (the user may run it), no offer.
	var up AgentInfo
	up.setUpdate(codingagents.UpdatePlan{Argv: []string{"/bin/claude", "update"}, Latest: "2.1.316"})
	if up.UpdateTo != "" || up.Update != "/bin/claude update" {
		t.Fatalf("%+v", up)
	}
	// Too new for the node's Node.js: no command, the note says why.
	var old AgentInfo
	old.setUpdate(codingagents.UpdatePlan{Latest: "1.5.0", Note: "Pi 1.5.0 needs Node.js >=22.19.0; this computer has v20.19.0"})
	if old.Update != "" || old.UpdateTo != "" || old.UpdateNote == "" {
		t.Fatalf("%+v", old)
	}
}
