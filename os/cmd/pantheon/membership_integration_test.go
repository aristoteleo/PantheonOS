package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/auth"
	"github.com/aristoteleo/pantheonos/os/internal/proto"
	"github.com/aristoteleo/pantheonos/os/internal/token"
)

// membershipController is the Controller's /join as far as memberships go:
// join tokens name their fleet, keys resolve to one, and a node identity that
// belongs to another fleet is refused (internal/controller otherFleetNode).
type membershipController struct {
	t      *testing.T
	a      *auth.Authority
	nats   string
	priv   ed25519.PrivateKey
	keys   map[string]string
	mu     sync.Mutex
	fleets map[string]string // node id -> fleet
	pubs   map[string]string // node key -> fleet
}

func (c *membershipController) token(fleet string) string {
	tok, err := token.SignJoin(c.priv, token.JoinPayload{FleetID: fleet, JTI: fleet + time.Now().String(), Exp: time.Now().Add(time.Hour).Unix()})
	if err != nil {
		c.t.Fatal(err)
	}
	return tok
}

func (c *membershipController) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/join" {
		http.NotFound(w, r)
		return
	}
	var req proto.JoinRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	fid := c.keys[req.Key]
	if req.JoinToken != "" {
		jp, err := token.VerifyJoin(c.priv.Public().(ed25519.PublicKey), req.JoinToken)
		if err != nil {
			http.Error(w, err.Error(), http.StatusUnauthorized)
			return
		}
		fid = jp.FleetID
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if (c.fleets[req.NodeID] != "" && c.fleets[req.NodeID] != fid) || (c.pubs[req.NodePub] != "" && c.pubs[req.NodePub] != fid) {
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(proto.JoinConflict{Error: proto.JoinConflictNodeInOtherFleet, FleetID: fid})
		return
	}
	c.fleets[req.NodeID], c.pubs[req.NodePub] = fid, fid
	creds, err := c.a.MintFleetNode(fid, req.NodeID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	_ = json.NewEncoder(w).Encode(proto.JoinResponse{FleetID: fid, NatsURL: c.nats, Creds: string(creds), RefreshToken: "rt-" + fid})
}

type runtimeInfo struct {
	NodeID  string `json:"node_id"`
	FleetID string `json:"fleet_id"`
}

func startMembershipNode(t *testing.T, home, controller string, args ...string) *exec.Cmd {
	t.Helper()
	full := append([]string{"--controller", controller, "--workdir", home, "--no-dataplane", "--no-files",
		"--no-capture-setup", "--system-python", "--no-auto-update"}, args...)
	raw, _ := json.Marshal(full)
	child := exec.Command(os.Args[0], "-test.run=^TestNodeStartupChild$")
	// A private HOME: nothing may reach this machine's own node state.
	child.Env = append(os.Environ(), "FLEET_TEST_STARTUP_ARGS="+string(raw), "HOME="+home)
	log, err := os.Create(filepath.Join(home, "node-"+time.Now().Format("150405.000000000")+".log"))
	if err != nil {
		t.Fatal(err)
	}
	child.Stdout, child.Stderr = log, log
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = child.Process.Kill(); _ = child.Wait(); _ = log.Close() })
	return child
}

func waitRuntime(t *testing.T, dir, fleet string) runtimeInfo {
	t.Helper()
	var info runtimeInfo
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		b, err := os.ReadFile(filepath.Join(dir, "runtime.json"))
		if err == nil && json.Unmarshal(b, &info) == nil && info.FleetID == fleet {
			return info
		}
		time.Sleep(50 * time.Millisecond)
	}
	logs, _ := filepath.Glob(filepath.Join(filepath.Dir(dir), "*.log"))
	var out strings.Builder
	for _, l := range logs {
		b, _ := os.ReadFile(l)
		out.Write(b)
	}
	t.Fatalf("no node of fleet %s in %s; logs:\n%s", fleet, dir, out.String())
	return info
}

// Reproduces 2026-11-03: account A's node runs on a Mac; account B's install
// command, run on the same Mac, rewrote A's state and reused A's node id. Now
// B gets its own membership (own directory, node id and key) and A keeps its
// own, both running; re-running a fleet's own command replaces only that
// fleet's node, and a key (which names no fleet) is moved on the refusal.
func TestSecondFleetJoinsAsASeparateNode(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("uses the Unix membership lock")
	}
	dir := t.TempDir()
	a, err := auth.Bootstrap(filepath.Join(dir, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	natsURL := recoveryServer(t, a)
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	ctl := &membershipController{t: t, a: a, nats: natsURL, priv: priv, keys: map[string]string{"key-b": "f_b"},
		fleets: map[string]string{}, pubs: map[string]string{}}
	srv := httptest.NewServer(ctl)
	defer srv.Close()

	home := filepath.Join(dir, "home")
	base := filepath.Join(home, "state")
	if err := os.MkdirAll(base, 0o700); err != nil {
		t.Fatal(err)
	}
	nodeA := startMembershipNode(t, home, srv.URL, "--state-dir", base, "--join-token", ctl.token("f_a"))
	runA := waitRuntime(t, base, "f_a")
	stateA, _ := os.ReadFile(filepath.Join(base, fleetStateFileName))

	// Account B's command on the same machine, while A's node runs.
	startMembershipNode(t, home, srv.URL, "--state-dir", base, "--join-token", ctl.token("f_b"))
	dirB := filepath.Join(base, "fleets", "f_b")
	runB := waitRuntime(t, dirB, "f_b")
	if runB.NodeID == runA.NodeID {
		t.Fatalf("fleet B reused fleet A's node id %s", runA.NodeID)
	}
	keyA, _ := os.ReadFile(filepath.Join(base, "node.key"))
	keyB, _ := os.ReadFile(filepath.Join(dirB, "node.key"))
	if len(keyB) == 0 || string(keyA) == string(keyB) {
		t.Fatal("fleet B must have its own node key")
	}
	if after, _ := os.ReadFile(filepath.Join(base, fleetStateFileName)); string(after) != string(stateA) {
		t.Fatal("joining fleet B rewrote fleet A's state")
	}
	if got := waitRuntime(t, base, "f_a"); got.NodeID != runA.NodeID {
		t.Fatalf("fleet A's runtime changed: %+v", got)
	}
	if nodeA.ProcessState != nil {
		t.Fatal("fleet A's node stopped")
	}

	// Fleet A's own command again: replaces A's node, same node id.
	if err := os.Remove(filepath.Join(base, "runtime.json")); err != nil {
		t.Fatal(err)
	}
	startMembershipNode(t, home, srv.URL, "--state-dir", base, "--join-token", ctl.token("f_a"))
	if got := waitRuntime(t, base, "f_a"); got.NodeID != runA.NodeID {
		t.Fatalf("same-fleet re-join changed the node id: %s -> %s", runA.NodeID, got.NodeID)
	}
	done := make(chan error, 1)
	go func() { done <- nodeA.Wait() }()
	select {
	case <-done:
	case <-time.After(20 * time.Second):
		t.Fatal("re-running fleet A's command did not replace its running node")
	}

	// A key names no fleet before the join: the Controller's refusal moves it.
	base2 := filepath.Join(home, "state2")
	if err := os.MkdirAll(base2, 0o700); err != nil {
		t.Fatal(err)
	}
	first := startMembershipNode(t, home, srv.URL, "--state-dir", base2, "--join-token", ctl.token("f_a"))
	run2A := waitRuntime(t, base2, "f_a")
	startMembershipNode(t, home, srv.URL, "--state-dir", base2, "--key", "key-b")
	run2B := waitRuntime(t, filepath.Join(base2, "fleets", "f_b"), "f_b")
	if run2B.NodeID == run2A.NodeID || savedFleet(base2) != "f_a" {
		t.Fatalf("key join reused or rewrote fleet A's membership: %+v %+v", run2A, run2B)
	}
	_ = first.Process.Kill()
	_ = first.Wait()
}
