package lifecycle

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func groupCredentialFixture(t *testing.T) (*Manager, *fakeDriver, string, *ecdsa.PrivateKey, *x509.Certificate, string) {
	t.Helper()
	return groupCredentialFixtureWith(t, proto.Capability{}, nil)
}
func groupCredentialFixtureWith(t *testing.T, caps proto.Capability, mutate func(*Definition)) (*Manager, *fakeDriver, string, *ecdsa.PrivateKey, *x509.Certificate, string) {
	t.Helper()
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		t.Skip("Unix private-file credential storage")
	}
	driver := &fakeDriver{alive: map[string]bool{}}
	m, err := Open(t.TempDir(), "f_aaaaaaaaaaaaaaaa", "n_first", caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { m.Close() })
	m.SetResourceSampler(resourceInventory)
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(4 * time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	der, err := x509.CreateCertificate(rand.Reader, ca, ca, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	ca, err = x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	sha := sha256.Sum256(der)
	manifest := fmt.Sprintf(`{"protocol":1,"rank":0,"ca_sha256":%q,"topology":{"protocol":1,"owner":"f_aaaaaaaaaaaaaaaa","group_id":"group","model_sha256":%q,"launch_sha256":%q,"members":[{"rank":0,"node_id":"n_first","generation":2,"address":"10.10.0.1","port":18400},{"rank":1,"node_id":"n_second","generation":2,"address":"10.10.0.2","port":18400}]}}`, hex.EncodeToString(sha[:]), strings.Repeat("b", 64), strings.Repeat("c", 64))
	def := definition()
	def.AppID = "model-service"
	def.Components[0].GroupPeer = true
	def.Components[0].Resources = &ResourceRequest{MemoryBytes: 4 << 30}
	if mutate != nil {
		mutate(&def)
	}
	archive, digest := bundle(t, def, map[string]string{"group-peer.json": manifest})
	if _, err := m.Stage(digest, 0, archive); err != nil {
		t.Fatal(err)
	}
	if op := submit(t, m, digest, "install", "install", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, digest, "prepare", "prepare_start", "group", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	return m, driver, digest, key, ca, string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}

func signGroupEnrollment(t *testing.T, e groupcredentials.Enrollment, key *ecdsa.PrivateKey, ca *x509.Certificate) string {
	t.Helper()
	block, _ := pem.Decode([]byte(e.CSR))
	if block == nil {
		t.Fatal("missing CSR")
	}
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil || csr.CheckSignature() != nil {
		t.Fatal("invalid signed CSR", err)
	}
	cert := &x509.Certificate{SerialNumber: big.NewInt(2), DNSNames: csr.DNSNames, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, cert, ca, csr.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}

func TestPreparedGroupCredentialsSurviveManagerRestartAndStayNodeLocal(t *testing.T) {
	m, driver, digest, key, ca, caPEM := groupCredentialFixture(t)
	id := m.instanceID(digest, "group")
	first, err := m.GroupPeer(id, digest, 1, "", "", false)
	if err != nil {
		t.Fatal(err)
	}
	cert := signGroupEnrollment(t, first, key, ca)
	if err := m.Close(); err != nil {
		t.Fatal(err)
	}
	restarted, err := Open(m.root, m.owner, m.node, m.caps, driver)
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.Close()
	restarted.SetResourceSampler(resourceInventory)
	retry, err := restarted.GroupPeer(id, digest, 1, "", "", false)
	if err != nil || retry != first {
		t.Fatal("manager restart changed CSR", err)
	}
	installed, err := restarted.GroupPeer(id, digest, 1, cert, caPEM, true)
	if err != nil || !installed.Installed || installed.Generation != 2 {
		t.Fatal("certificate install failed", err)
	}
	if again, err := restarted.GroupPeer(id, digest, 1, cert, caPEM, true); err != nil || again != installed {
		t.Fatal("install retry", err)
	}
	public, _ := json.Marshal(restarted.Snapshot())
	if strings.Contains(string(public), "PRIVATE KEY") || strings.Contains(string(public), "CERTIFICATE REQUEST") {
		t.Fatal("secret material entered public ledger")
	}
	files, err := os.ReadDir(restarted.paths(digest, "group").Package)
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 2 {
		t.Fatal("credential material entered artifact", files)
	}
	if driver.starts != 0 || len(driver.hooks) != 0 {
		t.Fatal("enrollment executed app code")
	}
	if op := submit(t, restarted, digest, "cancel", "stop", "group", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	for _, generation := range []uint64{1, 2} {
		if _, err := restarted.GroupPeer(id, digest, generation, cert, caPEM, true); err == nil {
			t.Fatal("late install passed after cancellation")
		}
	}
	if op := submit(t, restarted, digest, "prepare-next", "prepare_start", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
	if _, err := restarted.GroupPeer(id, digest, 3, "", "", false); err == nil {
		t.Fatal("old roster reused for new launch generation")
	}
}

func TestGroupCredentialsRejectBusyStaleAndForeignInstances(t *testing.T) {
	m, _, digest, key, ca, caPEM := groupCredentialFixture(t)
	id := m.instanceID(digest, "group")
	m.serial.Lock()
	_, err := m.GroupPeer(id, digest, 1, "", "", false)
	m.serial.Unlock()
	if err == nil || !strings.Contains(err.Error(), "busy") {
		t.Fatal("credential RPC waited behind launch", err)
	}
	for _, request := range []struct {
		id, revision string
		generation   uint64
	}{{id, digest, 0}, {id, digest, 2}, {id, strings.Repeat("f", 64), 1}, {strings.Repeat("e", 32), digest, 1}} {
		if _, err := m.GroupPeer(request.id, request.revision, request.generation, "", "", false); err == nil {
			t.Fatal("accepted stale/foreign binding")
		}
	}
	if _, err := os.Stat(filepath.Join(m.root, "group-credentials")); !os.IsNotExist(err) {
		t.Fatal("invalid operation minted a key", err)
	}
	if op := commitPrepared(t, m, digest, "unsigned-start", "prepare", 1); op.State != "failed" {
		t.Fatal("unsigned peer started", op)
	}
	enrollment, err := m.GroupPeer(id, digest, 1, "", "", false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := m.GroupPeer(id, digest, 1, signGroupEnrollment(t, enrollment, key, ca), caPEM, true); err != nil {
		t.Fatal(err)
	}
	if op := commitPrepared(t, m, digest, "start", "prepare", 1); op.State != "succeeded" {
		t.Fatal(op)
	}
	if _, err := m.GroupPeer(id, digest, 2, "", "", false); err == nil {
		t.Fatal("enrolled an already-started instance")
	}
	if op := submit(t, m, digest, "stop", "stop", "group", 2); op.State != "succeeded" {
		t.Fatal(op)
	}
}

func TestGroupCredentialsRejectNonModelAndUnboundedOrLinkedManifest(t *testing.T) {
	for _, kind := range []string{"non-model", "missing", "symlink", "oversize", "owner", "generation", "reservation"} {
		t.Run(kind, func(t *testing.T) {
			m, _, digest, _, _, _ := groupCredentialFixture(t)
			id := m.instanceID(digest, "group")
			path := filepath.Join(m.paths(digest, "group").Package, "group-peer.json")
			switch kind {
			case "non-model":
				m.ledger.Installations[digest].Definition.AppID = "other"
			case "reservation":
				m.ledger.Instances[id].Reservations = nil
			case "missing":
				if err := os.Remove(path); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Remove(path); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink("fleet.json", path); err != nil {
					t.Fatal(err)
				}
			default:
				data, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				text := string(data)
				if kind == "owner" {
					text = strings.ReplaceAll(text, "f_aaaaaaaaaaaaaaaa", "f_bbbbbbbbbbbbbbbb")
				}
				if kind == "generation" {
					text = strings.ReplaceAll(text, `"generation":2`, `"generation":3`)
				}
				if kind == "oversize" {
					text = strings.Repeat(" ", 16385) + text
				}
				if err := os.Chmod(path, 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte(text), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := m.GroupPeer(id, digest, 1, "", "", false); err == nil {
				t.Fatal("accepted invalid artifact/instance")
			}
		})
	}
}
