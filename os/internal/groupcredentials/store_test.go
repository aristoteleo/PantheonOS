package groupcredentials

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"
)

func fixture(t *testing.T) (Store, Binding, Manifest, *ecdsa.PrivateKey, *x509.Certificate, string) {
	t.Helper()
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		t.Skip("Unix private-file credential storage")
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test group"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(4 * time.Hour), IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign, MaxPathLen: 0, MaxPathLenZero: true}
	der, err := x509.CreateCertificate(rand.Reader, ca, ca, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	ca, err = x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	caPEM := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
	data := `{"protocol":1,"rank":0,"ca_sha256":"` + hash(der) + `","topology":{"protocol":1,"owner":"f_aaaaaaaaaaaaaaaa","group_id":"test-model-group","model_sha256":"` + strings.Repeat("b", 64) + `","launch_sha256":"` + strings.Repeat("c", 64) + `","members":[{"rank":0,"node_id":"n_first","generation":2,"address":"fd12::1","port":18400},{"rank":1,"node_id":"n_second","generation":2,"address":"fd12::2","port":18400}]}}`
	m, err := ParseManifest([]byte(data))
	if err != nil {
		t.Fatal(err)
	}
	b := Binding{Owner: m.Topology.Owner, Node: "n_first", Instance: strings.Repeat("d", 32), Revision: strings.Repeat("e", 64), Generation: 2, Preparation: "prepare-original"}
	return Store{Root: filepath.Join(t.TempDir(), "credentials")}, b, m, key, ca, caPEM
}

func sign(t *testing.T, e Enrollment, key *ecdsa.PrivateKey, ca *x509.Certificate, change func(*x509.Certificate)) string {
	t.Helper()
	der, err := parsePEM(e.CSR, "CERTIFICATE REQUEST")
	if err != nil {
		t.Fatal(err)
	}
	csr, err := x509.ParseCertificateRequest(der)
	if err != nil || csr.CheckSignature() != nil {
		t.Fatal("invalid CSR", err)
	}
	c := &x509.Certificate{SerialNumber: big.NewInt(2), NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour),
		DNSNames: csr.DNSNames, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}}
	if change != nil {
		change(c)
	}
	der, err = x509.CreateCertificate(rand.Reader, c, ca, csr.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}

func TestDurableEnrollmentAndCertificateInstall(t *testing.T) {
	s, b, m, key, ca, caPEM := fixture(t)
	e, err := s.Enroll(b, m)
	if err != nil {
		t.Fatal(err)
	}
	if e.Installed || e.Generation != 2 || e.Topology != "1fe35d972118ffa41fd499775ea750f8596fd39b672ebe904069168c7424155f" {
		t.Fatal(e)
	}
	dir, _, err := s.directory(b, m, false)
	if err != nil {
		t.Fatal(err)
	}
	defer dir.Close()
	before, err := readMaterial(dir, b, m)
	if err != nil {
		t.Fatal(err)
	}
	// A fresh Store instance reuses the durable key and CSR, not process memory.
	restarted := Store{Root: s.Root}
	again, err := restarted.Enroll(b, m)
	if err != nil || again != e {
		t.Fatal("retry changed identity", err)
	}
	cert := sign(t, e, key, ca, nil)
	installed, err := restarted.Install(b, m, cert, caPEM)
	if err != nil || !installed.Installed {
		t.Fatal(err)
	}
	if repeat, err := restarted.Install(b, m, cert, caPEM); err != nil || repeat != installed {
		t.Fatal("lost reply retry failed", err)
	}
	after, err := readMaterial(dir, b, m)
	if err != nil || after.Key != before.Key || after.CSR != before.CSR {
		t.Fatal("installation rotated private key", err)
	}
	public, _ := json.Marshal(installed)
	if strings.Contains(string(public), "PRIVATE KEY") || strings.Contains(string(public), s.Root) {
		t.Fatal("private material leaked")
	}
	changed := sign(t, e, key, ca, func(c *x509.Certificate) { c.SerialNumber = big.NewInt(3) })
	if _, err := restarted.Install(b, m, changed, caPEM); err == nil {
		t.Fatal("replaced installed certificate")
	}
	info, _ := dir.Stat("material.json")
	if info.Mode().Perm() != 0600 {
		t.Fatal("key permissions", info.Mode())
	}
}

func TestCertificateIdentityAndValidityAreStrict(t *testing.T) {
	s, b, m, key, ca, caPEM := fixture(t)
	e, err := s.Enroll(b, m)
	if err != nil {
		t.Fatal(err)
	}
	cases := map[string]func(*x509.Certificate){
		"other rank":  func(c *x509.Certificate) { c.DNSNames[0] = "r1." + strings.SplitN(e.Name, ".", 2)[1] },
		"wildcard":    func(c *x509.Certificate) { c.DNSNames = []string{"*.fleet-model.invalid"} },
		"extra SAN":   func(c *x509.Certificate) { c.DNSNames = append(c.DNSNames, "other.invalid") },
		"extra IP":    func(c *x509.Certificate) { c.IPAddresses = []net.IP{net.ParseIP("10.1.1.1")} },
		"CA leaf":     func(c *x509.Certificate) { c.IsCA = true },
		"server only": func(c *x509.Certificate) { c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth} },
		"any usage": func(c *x509.Certificate) {
			c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageAny, x509.ExtKeyUsageServerAuth}
		},
		"expired":         func(c *x509.Certificate) { c.NotAfter = time.Now().Add(-time.Second) },
		"not yet valid":   func(c *x509.Certificate) { c.NotBefore = time.Now().Add(time.Minute) },
		"long lived":      func(c *x509.Certificate) { c.NotAfter = time.Now().Add(48 * time.Hour) },
		"wrong key usage": func(c *x509.Certificate) { c.KeyUsage = x509.KeyUsageCertSign },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			cert := sign(t, e, key, ca, change)
			if _, err := s.Install(b, m, cert, caPEM); err == nil {
				t.Fatal("accepted invalid certificate")
			}
		})
	}
	cert := sign(t, e, key, ca, nil)
	other, ob, om, _, _, otherCA := fixture(t)
	oe, err := other.Enroll(ob, om)
	if err != nil {
		t.Fatal(err)
	}
	wrongKey := sign(t, oe, key, ca, nil)
	for name, pair := range map[string][2]string{"wrong key": {wrongKey, caPEM}, "wrong CA": {cert, otherCA}, "bundle": {cert + cert, caPEM}, "private key": {e.CSR, caPEM}, "too big": {strings.Repeat("x", 16385), caPEM}} {
		t.Run(name, func(t *testing.T) {
			if _, err := s.Install(b, m, pair[0], pair[1]); err == nil {
				t.Fatal("accepted invalid certificate")
			}
		})
	}
	if out, err := s.Enroll(b, m); err != nil || out.Installed || out.CSR != e.CSR {
		t.Fatal("failed install mutated enrollment", err)
	}
}

func TestMissingOrModifiedKeyNeverSilentlyRegenerates(t *testing.T) {
	for _, kind := range []string{"missing", "corrupt", "permissions", "symlink", "binding"} {
		t.Run(kind, func(t *testing.T) {
			s, b, m, _, _, _ := fixture(t)
			if _, err := s.Enroll(b, m); err != nil {
				t.Fatal(err)
			}
			dir, _, err := s.directory(b, m, false)
			if err != nil {
				t.Fatal(err)
			}
			defer dir.Close()
			switch kind {
			case "missing":
				err = dir.Remove("material.json")
			case "corrupt":
				err = dir.WriteFile("material.json", []byte("broken"), 0600)
			case "permissions":
				err = dir.Chmod("material.json", 0644)
			case "symlink":
				err = dir.Remove("material.json")
				if err == nil {
					err = dir.Symlink("other.json", "material.json")
				}
			case "binding":
				v, e := readMaterial(dir, b, m)
				if e != nil {
					t.Fatal(e)
				}
				v.Binding.Revision = strings.Repeat("f", 64)
				err = writeMaterial(dir, v)
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := s.Enroll(b, m); err == nil {
				t.Fatal("repaired/replaced uncertain key material")
			}
		})
	}
}

func TestNoEnrollmentForAnotherNodeGenerationOrOwner(t *testing.T) {
	s, b, m, _, _, _ := fixture(t)
	for _, change := range []func(*Binding){func(b *Binding) { b.Owner = "f_bbbbbbbbbbbbbbbb" }, func(b *Binding) { b.Node = "n_second" }, func(b *Binding) { b.Generation = 3 }, func(b *Binding) { b.Instance = "../escape" }} {
		changed := b
		change(&changed)
		if _, err := s.Enroll(changed, m); err == nil {
			t.Fatal("accepted foreign binding")
		}
	}
	if _, err := os.Stat(s.Root); !os.IsNotExist(err) {
		t.Fatal("invalid enrollment created files", err)
	}
}

func TestManifestCanonicalInteropAndRejectedAddresses(t *testing.T) {
	_, _, m, _, _, _ := fixture(t)
	data, _ := json.Marshal(m)
	var doc map[string]any
	json.Unmarshal(data, &doc)
	top := doc["topology"].(map[string]any)
	peers := top["members"].([]any)
	peers[0], peers[1] = peers[1], peers[0]
	peers[1].(map[string]any)["address"] = "fd12:0:0:0:0:0:0:1"
	bytes, _ := json.Marshal(doc)
	reordered, err := ParseManifest(bytes)
	if err != nil || !reflect.DeepEqual(reordered, m) {
		t.Fatal("Go/Python canonicalization differs", err)
	}
	for _, address := range []string{"127.0.0.1", "::1", "0.0.0.0", "::", "8.8.8.8", "169.254.169.254", "fe80::1", "fd12::1%eth0", "::ffff:10.0.0.1", "192.0.2.1", "100.64.0.1", "host.invalid", "224.0.0.1"} {
		peers[1].(map[string]any)["address"] = address
		bytes, _ := json.Marshal(doc)
		if _, err := ParseManifest(bytes); err == nil {
			t.Fatalf("accepted %s", address)
		}
	}
	for _, bad := range []string{strings.Replace(string(data), `"protocol":1`, `"Protocol":1`, 1), strings.Replace(string(data), `"rank":0`, `"rank":0,"rank":1`, 1), strings.Replace(string(data), `"rank":0`, `"rank":null`, 1), strings.Replace(string(data), `"rank":0`, `"rank":true`, 1), strings.Replace(string(data), `"rank":0`, `"rank":1.0`, 1), strings.Replace(string(data), `"rank":0,`, "", 1), string(data) + "{}"} {
		if _, err := ParseManifest([]byte(bad)); err == nil {
			t.Fatal("accepted ambiguous manifest", bad)
		}
	}
}
