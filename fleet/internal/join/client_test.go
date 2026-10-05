package join

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func privateServer(t *testing.T, handler http.Handler) (*httptest.Server, string) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test profile"},
		NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}}
	der, err := x509.CreateCertificate(rand.Reader, template, template, pub, priv)
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(file, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0600); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewUnstartedServer(handler)
	server.TLS = &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: priv}}}
	server.StartTLS()
	t.Cleanup(server.Close)
	return server, file
}

func TestPrivateControllerTrustIsScopedAndRejectsRedirects(t *testing.T) {
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/token" {
			w.Write([]byte(`{"creds":"renewed"}`))
			return
		}
		w.Write([]byte(`{"fleet_id":"local", "nats_url":"nats://127.0.0.1:4222"}`))
	})
	a, ca := privateServer(t, handler)
	b, _ := privateServer(t, handler)
	client, err := NewClient(a.URL, ca)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	ctx := context.Background()
	if _, err = client.Join(ctx, a.URL, proto.JoinRequest{Key: "private"}); err != nil {
		t.Fatal(err)
	}
	if _, err = client.Refresh(ctx, a.URL, proto.TokenRequest{}); err != nil {
		t.Fatal(err)
	}
	if _, err = client.Join(ctx, b.URL, proto.JoinRequest{Key: "private"}); err == nil {
		t.Fatal("cross-origin join allowed")
	}
	wrong, err := NewClient(b.URL, ca)
	if err != nil {
		t.Fatal(err)
	}
	defer wrong.Close()
	if _, err = wrong.Join(ctx, b.URL, proto.JoinRequest{}); err == nil {
		t.Fatal("another profile's CA accepted")
	}
	if _, err = Join(ctx, a.URL, proto.JoinRequest{}); err == nil {
		t.Fatal("private CA leaked into default client")
	}
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("credential redirect was followed") }))
	defer redirect.Close()
	c, cc := privateServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, redirect.URL, http.StatusTemporaryRedirect)
	}))
	r, err := NewClient(c.URL, cc)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if _, err = r.Join(ctx, c.URL, proto.JoinRequest{Key: "secret"}); err == nil {
		t.Fatal("redirect accepted")
	}
	for _, origin := range []string{"http://127.0.0.1:1234", a.URL + "/path", a.URL + "?", a.URL + "#fragment", "https://user@localhost"} {
		if _, err := NewClient(origin, ca); err == nil {
			t.Errorf("unsafe origin %s accepted", origin)
		}
	}
}
