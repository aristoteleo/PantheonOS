package runner

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"github.com/aristoteleo/pantheon-fleet/internal/appgateway"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
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
	t.Run("http", func(t *testing.T) { testDelegatedNodeControlPlane(t, false) })
	t.Run("private-https", func(t *testing.T) { testDelegatedNodeControlPlane(t, true) })
}

func testDelegatedNodeControlPlane(t *testing.T, privateTLS bool) {
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
	r := New(nc, fleet, parent, reg, nil, &proto.Node{NodeID: parent, Version: "test", Capability: proto.Capability{Runtimes: map[string]string{}}})
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
 os.execv(sys.executable,[sys.executable]+command[command.index('python3')+1:])
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

	// Exercise the existing gateway through real authenticated NATS, without a
	// compute-node listener or accepting an arbitrary destination from a caller.
	gateway, err := appgateway.New("apps.test", strings.Repeat("s", 32), []string{"http://localhost:5173"},
		func(ctx context.Context, b appgateway.Binding, stream, secret string) error {
			payload, _ := json.Marshal(map[string]any{"type": "app_service", "instance_id": b.Instance, "revision": b.Revision, "generation": b.Generation, "component": b.Component, "port": b.Port, "stream": stream, "secret": secret})
			msg, err := agent.RequestWithContext(ctx, proto.SubjNodeCmd(fleet, c.rec.NodeID), payload)
			if err != nil {
				return err
			}
			var result struct {
				OK bool `json:"ok"`
			}
			if json.Unmarshal(msg.Data, &result) != nil || !result.OK {
				return fmt.Errorf("gateway dispatch: %s", msg.Data)
			}
			return nil
		}, func(ctx context.Context, b appgateway.Binding) error {
			payload, _ := json.Marshal(map[string]any{"type": "app_lifecycle", "protocol": 1, "method": "service", "instance_id": b.Instance, "revision": b.Revision, "generation": b.Generation, "component": b.Component, "port": b.Port})
			msg, err := agent.RequestWithContext(ctx, proto.SubjNodeCmd(fleet, c.rec.NodeID), payload)
			if err != nil {
				return err
			}
			var result struct {
				Ready bool `json:"ready"`
			}
			if json.Unmarshal(msg.Data, &result) != nil || !result.Ready {
				return fmt.Errorf("gateway verify: %s", msg.Data)
			}
			return nil
		})
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	gateway.Register(mux)
	httpServer := httptest.NewUnstartedServer(gateway.Handler(mux))
	var trust *tls.Config
	if privateTLS {
		httpServer.EnableHTTP2 = true
		httpServer.StartTLS()
		roots := x509.NewCertPool()
		roots.AddCert(httpServer.Certificate())
		trust = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12, NextProtos: []string{"h2", "http/1.1"}}
	} else {
		httpServer.Start()
	}
	defer httpServer.Close()
	if err = r.EnableServicesWithTLS(ctx, httpServer.URL, trust); err != nil {
		t.Fatal(err)
	}
	if privateTLS && (strings.Join(trust.NextProtos, ",") != "h2,http/1.1" || strings.Join(r.serviceTLS.NextProtos, ",") != "http/1.1") {
		t.Fatal("tunnel ALPN must not change the owner REST transport")
	}
	spec, _ := json.Marshal(map[string]any{"type": "hpc_service", "protocol": 1, "method": "start", "generation": 1, "spec": map[string]any{"name": "web", "argv": []string{python, "-m", "http.server", "${PORT}", "--bind", "${HOST}"}, "startup_seconds": 5}})
	started := request(string(spec))
	if started["error"] != nil {
		t.Fatal(started)
	}
	service := started["service"].(map[string]any)
	if blocked := request(`{"type":"run_task","task":{"task_id":"while-service-starting","kind":"shell","code":"touch should-not-exist"}}`); blocked["error"] == nil {
		t.Fatal("task ran concurrently with an allocation service", blocked)
	}
	for i := 0; i < 100; i++ {
		if c.services.Ready(service["instance_id"].(string), service["revision"].(string), 1) == nil {
			break
		}
		time.Sleep(30 * time.Millisecond)
	}
	binding := appgateway.Binding{Fleet: fleet, Node: c.rec.NodeID, Instance: service["instance_id"].(string), Revision: service["revision"].(string), Generation: 1, Component: "service", Port: "http"}
	grantBody, _ := json.Marshal(appgateway.AttachRequest{Binding: binding, Credential: strings.Repeat("c", 32), Expires: time.Now().Add(time.Minute).Unix(), Workload: true})
	attach, _ := http.NewRequest("POST", httpServer.URL+"/apps/connect", bytes.NewReader(grantBody))
	attach.Header.Set("Authorization", "Bearer "+strings.Repeat("s", 32))
	response, err := httpServer.Client().Do(attach)
	if err != nil {
		t.Fatal(err)
	}
	var grant map[string]any
	json.NewDecoder(response.Body).Decode(&grant)
	response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal(response.StatusCode, grant)
	}
	get, _ := http.NewRequest("GET", httpServer.URL+"/result.txt", nil)
	get.Host = appgateway.Host(binding.Instance, binding.Component, binding.Port, 1, "apps.test")
	get.Header.Set("Authorization", "Bearer "+grant["access_token"].(string))
	response, err = httpServer.Client().Do(get)
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(response.Body)
	response.Body.Close()
	if err != nil || response.StatusCode != 200 || string(body) != "ok" {
		t.Fatal(response.StatusCode, string(body), err)
	}
	stop, _ := json.Marshal(map[string]any{"type": "hpc_service", "protocol": 1, "method": "stop", "instance_id": binding.Instance, "revision": binding.Revision, "generation": 1})
	if stopped := request(string(stop)); stopped["error"] != nil {
		t.Fatal(stopped)
	}
	for i := 0; i < 200 && c.services.Busy(); i++ {
		time.Sleep(30 * time.Millisecond)
	}
	if c.services.Busy() {
		t.Fatal("service did not stop")
	}
	response, err = httpServer.Client().Do(get)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode == 200 {
		t.Fatal("old gateway grant reached stopped service")
	}
	// Primary workloads cannot be launched separately, and stopping must pass
	// both the immutable binding check and scheduler ownership verification.
	primary, _ := (hpc.HTTPService{Name: "web", Argv: []string{python, "-m", "http.server", "${PORT}", "--bind", "${HOST}"}, StartupSeconds: 5}).Normalize()
	c.mu.Lock()
	c.job.Service = &primary
	c.mu.Unlock()
	var cancellations atomic.Int32
	scheduler := r.scheduler(profile.ID)
	recordDir := filepath.Join(scheduler.Root, "acceptance")
	if err := os.MkdirAll(recordDir, 0700); err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(job)
	if err := os.WriteFile(filepath.Join(recordDir, "job.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	scheduler.Remote = func(_ context.Context, _ []byte, argv ...string) ([]byte, error) {
		if argv[0] == "scontrol" {
			return []byte("Comment=pantheon-" + job.AllocationID), nil
		}
		if strings.Join(argv, " ") == "scancel 123" {
			cancellations.Add(1)
			return nil, nil
		}
		return nil, fmt.Errorf("unexpected scheduler command %v", argv)
	}
	if denied := request(string(spec)); denied["error"] == nil {
		t.Fatal("independently restarted primary", denied)
	}
	staleStop := strings.Replace(string(stop), binding.Revision, strings.Repeat("0", 64), 1)
	if denied := request(staleStop); denied["error"] == nil || cancellations.Load() != 0 {
		t.Fatal("stale binding cancelled job", denied)
	}
	if stopped := request(string(stop)); stopped["error"] != nil || cancellations.Load() != 1 {
		t.Fatal("primary stop did not cancel owned job", stopped)
	}
	c.mu.Lock()
	c.job.Service = nil
	c.mu.Unlock()
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
