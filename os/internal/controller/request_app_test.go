package controller

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
)

// An App service holds an App credential: it may answer only inboxes in the
// App namespace. The controller's calls must wait there, or every answer is
// refused and the call times out (as node_call did).
func TestRequestAppIsAnsweredByAnAppCredential(t *testing.T) {
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
	// The shapes of auth.MintFleetUser (ctl) and auth.MintApps (app).
	cfg := fmt.Sprintf(`listen: 127.0.0.1:%d
authorization { users = [
  { user: ctl, password: ctl, permissions: {
      publish: { allow: ["fleet.F.>", "_INBOX_F.>"] }, subscribe: { allow: ["fleet.F.>", "_INBOX_F.>"] } } }
  { user: app, password: app, permissions: {
      publish: { allow: ["fleet.F.apps.>", "_INBOX_F.apps.>"] }, subscribe: { allow: ["fleet.F.apps.>"] } } }
] }
`, port)
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
	var ctl, app *nats.Conn
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(50 * time.Millisecond) {
		if ctl, err = nats.Connect(url, nats.UserInfo("ctl", "ctl"), nats.CustomInboxPrefix("_INBOX_F")); err == nil {
			break
		}
	}
	if ctl == nil {
		t.Fatal("nats-server did not start")
	}
	defer ctl.Close()
	if app, err = nats.Connect(url, nats.UserInfo("app", "app")); err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	subject := "fleet.F.apps.pantheon.service.svc"
	if _, err := app.Subscribe(subject, func(m *nats.Msg) { _ = m.Respond([]byte(`{"result":"pong"}`)) }); err != nil {
		t.Fatal(err)
	}
	_ = app.Flush()

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	got, err := requestApp(ctx, ctl, "F", subject, []byte(`{}`))
	if err != nil || string(got) != `{"result":"pong"}` {
		t.Fatalf("got %s %v", got, err)
	}

	// The plain request is what failed: its inbox is outside the App namespace.
	short, cancelShort := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancelShort()
	if _, err := ctl.RequestWithContext(short, subject, []byte(`{}`)); err == nil {
		t.Fatal("an App credential answered outside the App namespace; the test no longer shows the fault")
	}

	none, cancelNone := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancelNone()
	if _, err := requestApp(none, ctl, "F", "fleet.F.apps.pantheon.service.nobody", []byte(`{}`)); !errors.Is(err, nats.ErrNoResponders) &&
		!strings.Contains(fmt.Sprint(err), "no responders") {
		t.Fatalf("a missing service must read as no responders (node_call retries on it): %v", err)
	}
}
