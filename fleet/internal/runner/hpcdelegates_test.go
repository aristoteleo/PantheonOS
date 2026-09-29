package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
	"github.com/aristoteleo/pantheon-fleet/internal/hpcconn"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
	"github.com/nats-io/nats.go"
)

// Local protocol acceptance: real JWT-scoped NATS and remote Python steps,
// with an SSH/Slurm fixture (does not contact or allocate a real HPC cluster).
func TestDelegatedNodeControlPlane(t *testing.T) {
	server, err := exec.LookPath("nats-server")
	if err != nil {
		t.Skip("nats-server not installed")
	}
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 not installed")
	}
	root := t.TempDir()
	authority, err := auth.Bootstrap(filepath.Join(root, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := ln.Addr().String()
	ln.Close()
	config := filepath.Join(root, "nats.conf")
	os.WriteFile(config, []byte(authority.ServerConfig(address, filepath.Join(root, "js"))), 0600)
	process := exec.Command(server, "-c", config)
	if err = process.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { process.Process.Kill(); process.Wait() }()
	fleet, parent := "f_hpc_test", "mac"
	parentCreds, _ := authority.MintFleetNode(fleet, parent)
	var nc *nats.Conn
	for i := 0; i < 50; i++ {
		nc, err = nats.Connect("nats://"+address, nats.UserCredentialBytes(parentCreds), nats.CustomInboxPrefix("_INBOX_"+fleet))
		if err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	reg, err := registry.Open(ctx, nc, fleet, parent, 30*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	r := New(nc, fleet, parent, reg, nil, &proto.Node{NodeID: parent, Version: "test"})
	// The fixture still runs the production remote program with the actual task
	// and file request; it only replaces SSH and srun's scheduling boundary.
	fixture := `import os,sys,shlex,subprocess,time
args=sys.argv[1:]
if '-O' in args: sys.exit(0)
if '-M' in args:
 open(args[args.index('-S')+1],'w').close()
 print('__PANTHEON_HPC_CONNECTED__',flush=True)
 time.sleep(60)
else:
 command=shlex.split(args[-1])
 assert command[0]=='srun'
 os.environ['SLURM_JOB_ID']=command[1].split('=',1)[1]
 os.environ['HOME']=os.environ['HPC_TEST_HOME']
 os.execv(sys.executable,[sys.executable]+command[-2:])
`
	ssh := filepath.Join(root, "ssh")
	os.WriteFile(ssh, []byte("#!"+python+"\n"+fixture), 0700)
	t.Setenv("HPC_TEST_HOME", root)
	m := &hpcconn.Manager{Root: filepath.Join(root, "clusters"), SSH: ssh}
	profile, err := m.Save(hpcconn.Cluster{Host: "cluster.example", User: "u"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = m.SignIn(profile.ID); err != nil {
		t.Fatal(err)
	}
	defer m.SignOut(profile.ID)
	for i := 0; i < 100; i++ {
		st, _ := m.Status(profile.ID)
		if st.State == "connected" {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	r.EnableHPCClusters(m)
	job := hpc.Job{JobID: "123", AllocationID: strings.Repeat("a", 32), State: "RUNNING", NodeName: "cpu-123", CPUs: 1, MemGB: 1}
	authorize := func(_ context.Context, allocation string) (proto.DelegateResponse, error) {
		id := proto.DelegatedNodeID(fleet, parent, allocation)
		creds, err := authority.MintFleetNode(fleet, id)
		return proto.DelegateResponse{NodeID: id, Creds: string(creds), ExpiresAt: time.Now().Add(time.Hour).Unix()}, err
	}
	c, err := r.newDelegate(ctx, profile.ID, job, authorize)
	if err != nil {
		t.Fatal(err)
	}
	defer c.close()
	c.update(ctx, r, job)
	if c.snapshot().Delegation.State != "ready" {
		t.Fatalf("probe failed: %+v", c.snapshot().Delegation)
	}
	if err := c.reg.Put(ctx, c.snapshot()); err != nil {
		t.Fatal(err)
	}
	discovered, err := reg.Get(ctx, c.rec.NodeID)
	if err != nil || discovered.Delegation == nil || discovered.Delegation.ConnectorID != parent {
		t.Fatalf("delegated node missing from registry: %+v %v", discovered, err)
	}
	agentCreds, _ := authority.MintFleetUser(fleet)
	agent, err := nats.Connect("nats://"+address, nats.UserCredentialBytes(agentCreds), nats.CustomInboxPrefix("_INBOX_"+fleet))
	if err != nil {
		t.Fatal(err)
	}
	defer agent.Close()
	request := func(body string) map[string]any {
		t.Helper()
		msg, err := agent.Request(proto.SubjNodeCmd(fleet, c.rec.NodeID), []byte(body), 10*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		var reply map[string]any
		if err = json.Unmarshal(msg.Data, &reply); err != nil {
			t.Fatalf("%s: %v", msg.Data, err)
		}
		return reply
	}
	reply := request(`{"type":"run_task","task":{"task_id":"t","kind":"shell","code":"printf HPC_OK","timeout_s":5}}`)
	if reply["stdout"] != "HPC_OK" || reply["exit_code"] != float64(0) {
		t.Fatal(reply)
	}
	reply = request(`{"type":"hpc_file","file":{"operation":"write","path":"result.txt","data":"b2s="}}`)
	if reply["size"] != float64(2) {
		t.Fatal(reply)
	}
	reply = request(`{"type":"hpc_file","file":{"operation":"read","path":"result.txt"}}`)
	if reply["data"] != "b2s=" {
		t.Fatal(reply)
	}
	// A signed-out connection remains identifiable, but refuses work.
	c.unavailable("sign_in_required", "Sign in again")
	reply = request(`{"type":"run_task","task":{"task_id":"x","kind":"shell","code":"touch should-not-exist"}}`)
	if reply["error"] == nil {
		t.Fatal("offline node accepted work")
	}
	if _, err = os.Stat(filepath.Join(root, ".pantheon-fleet/workspaces", job.AllocationID, "should-not-exist")); !os.IsNotExist(err) {
		t.Fatal("offline task ran")
	}
	c.nextProbe = time.Time{}
	c.update(ctx, r, job)
	if got := request(`{"type":"ping"}`)["pong"]; got != c.rec.NodeID {
		t.Fatal(got)
	}
	if c.rec.NodeID != proto.DelegatedNodeID(fleet, parent, job.AllocationID) {
		t.Fatal("reconnect changed identity")
	}
	t.Log(fmt.Sprintf("delegated node %s executed task and file round trip", c.rec.NodeID))
}
