package appsvc

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// natsServer starts a JetStream server with an unrestricted user and an App
// user that, like node and App credentials, cannot reach the JetStream API.
func natsServer(t *testing.T) string {
	bin := os.Getenv("FLEET_TEST_NATS_SERVER")
	if bin == "" {
		bin, _ = exec.LookPath("nats-server")
	}
	if bin == "" {
		t.Skip("set FLEET_TEST_NATS_SERVER (or put nats-server on PATH)")
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := l.Addr().(*net.TCPAddr).Port
	_ = l.Close()
	dir := t.TempDir()
	cfg := fmt.Sprintf(`listen: 127.0.0.1:%d
jetstream { store_dir: %q }
authorization { users = [
  { user: admin, password: admin }
  { user: app, password: app, permissions: {
      publish: { allow: ["pantheon.service.>", "_INBOX.>"] },
      subscribe: { allow: ["pantheon.service.>", "_INBOX.>"] } } }
] }
`, port, filepath.Join(dir, "js"))
	path := filepath.Join(dir, "nats.conf")
	if err := os.WriteFile(path, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	srv := exec.Command(bin, "-c", path)
	if err := srv.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = srv.Process.Kill(); _ = srv.Wait() })
	url := fmt.Sprintf("nats://127.0.0.1:%d", port)
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(50 * time.Millisecond) {
		if nc, err := nats.Connect(url, nats.UserInfo("admin", "admin")); err == nil {
			nc.Close()
			return url
		}
	}
	t.Fatal("nats-server did not start")
	return ""
}

func connect(t *testing.T, url, user string) *nats.Conn {
	nc, err := nats.Connect(url, nats.UserInfo(user, user), nats.ErrorHandler(func(*nats.Conn, *nats.Subscription, error) {}))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(nc.Close)
	return nc
}

const testServiceID = "0123456789abcdef0123456789abcdef"

func TestAnAppWithoutJetStreamAccessStartsWithoutWaitingOutTheRegistration(t *testing.T) {
	url := natsServer(t)
	svc := New(connect(t, url, "app"), testServiceID, "shell", "", "1", "")
	started := time.Now()
	if err := svc.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer svc.Stop(context.Background())
	if took := time.Since(started); took > 2*time.Second {
		t.Fatalf("Start took %s waiting for a registration this connection cannot make", took)
	}
	reply, err := connect(t, url, "admin").Request("pantheon.service."+testServiceID, []byte(`{"method":"_ping","parameters":{}}`), 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if string(reply.Data) == "" {
		t.Fatal("empty _ping reply")
	}
}

func TestARegistrationThatCanBeMadeIsInPlaceWhenStartReturns(t *testing.T) {
	url := natsServer(t)
	nc := connect(t, url, "admin")
	svc := New(nc, testServiceID, "shell", "", "1", "")
	if err := svc.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	js, _ := jetstream.New(nc)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	kv, err := js.KeyValue(ctx, DiscoveryBucket)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := kv.Get(ctx, testServiceID); err != nil {
		t.Fatalf("registration missing after Start: %v", err)
	}
	svc.Stop(ctx)
	if _, err := kv.Get(ctx, testServiceID); err == nil {
		t.Fatal("registration kept after Stop")
	}
}
