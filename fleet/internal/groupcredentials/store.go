package groupcredentials

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"time"
)

// Binding is supplied by the lifecycle manager after a generation-CAS check,
// never by an App. Generation is the exact future started generation.
type Binding struct {
	Owner       string `json:"owner"`
	Node        string `json:"node_id"`
	Instance    string `json:"instance_id"`
	Revision    string `json:"revision"`
	Generation  uint64 `json:"generation"`
	Preparation string `json:"preparation_id"`
}

type Enrollment struct {
	Protocol   int    `json:"protocol"`
	Instance   string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation uint64 `json:"generation"`
	Topology   string `json:"topology_sha256"`
	Name       string `json:"dns_name"`
	CSR        string `json:"csr_pem"`
	Installed  bool   `json:"certificate_installed"`
}

type material struct {
	Protocol    int      `json:"protocol"`
	Binding     Binding  `json:"binding"`
	Manifest    Manifest `json:"manifest"`
	Key         string   `json:"key_pem"`
	CSR         string   `json:"csr_pem"`
	Certificate string   `json:"certificate_pem,omitempty"`
	CA          string   `json:"ca_pem,omitempty"`
}

// Store is serialized by the lifecycle manager, which also holds the exclusive
// node-state lock. Only Unix private-file storage is enabled; Windows needs its
// own protected storage implementation before advertising this capability.
type Store struct{ Root string }

var instanceRE = regexp.MustCompile(`^[a-f0-9]{32}$`)
var preparationRE = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,79}$`)

func (s Store) directory(b Binding, manifest Manifest, create bool) (*os.Root, bool, error) {
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		return nil, false, fmt.Errorf("group credentials are not supported on this platform")
	}
	data, _ := json.Marshal(manifest)
	m, err := ParseManifest(data)
	if err != nil {
		return nil, false, err
	}
	if !reflect.DeepEqual(m, manifest) {
		return nil, false, fmt.Errorf("parse and canonicalize the group manifest before enrollment")
	}
	p := m.Topology.Members[*m.Rank]
	if b.Owner != m.Topology.Owner || b.Node != p.Node || b.Generation != p.Generation ||
		!instanceRE.MatchString(b.Instance) || !digestRE.MatchString(b.Revision) || !preparationRE.MatchString(b.Preparation) {
		return nil, false, fmt.Errorf("group credentials do not match the prepared instance")
	}
	if create {
		if err := os.Mkdir(s.Root, 0700); err == nil {
			parent, e := os.Open(filepath.Dir(s.Root))
			if e != nil {
				return nil, false, e
			}
			if e = errors.Join(parent.Sync(), parent.Close()); e != nil {
				return nil, false, e
			}
		} else if !errors.Is(err, os.ErrExist) {
			return nil, false, err
		}
	}
	if err := privateDirectory(s.Root); err != nil {
		return nil, false, err
	}
	root, err := os.OpenRoot(s.Root)
	if err != nil {
		return nil, false, err
	}
	defer root.Close()
	identity, _ := json.Marshal(b)
	name := hash(identity)
	fresh := false
	if create {
		if err := root.Mkdir(name, 0700); err == nil {
			fresh = true
		} else if !errors.Is(err, os.ErrExist) {
			return nil, false, err
		}
	}
	info, err := root.Lstat(name)
	if err != nil {
		return nil, false, err
	}
	if !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return nil, false, fmt.Errorf("group credential directory is not private")
	}
	dir, err := root.OpenRoot(name)
	return dir, fresh, err
}

func privateDirectory(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return fmt.Errorf("group credential root is not private")
	}
	return nil
}

func readMaterial(root *os.Root, b Binding, m Manifest) (material, error) {
	var value material
	info, err := root.Lstat("material.json")
	if err != nil {
		return value, fmt.Errorf("group key material is missing; do not re-enroll this attempt")
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 32768 {
		return value, fmt.Errorf("invalid private group key file")
	}
	f, err := root.Open("material.json")
	if err != nil {
		return value, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, 32769))
	if err != nil || len(data) > 32768 {
		return value, fmt.Errorf("invalid private group key file")
	}
	if err := decode(data, &value); err != nil || value.Protocol != 1 || value.Binding != b || !reflect.DeepEqual(value.Manifest, m) {
		return value, fmt.Errorf("group key material binding mismatch")
	}
	key, err := parseKey(value.Key)
	if err != nil {
		return value, fmt.Errorf("invalid private group key")
	}
	block, err := parsePEM(value.CSR, "CERTIFICATE REQUEST")
	if err != nil {
		return value, err
	}
	csr, err := x509.ParseCertificateRequest(block)
	if err != nil || csr.CheckSignature() != nil || !samePublic(csr.PublicKey, &key.PublicKey) ||
		len(csr.DNSNames) != 1 || csr.DNSNames[0] != m.Name() || len(csr.IPAddresses)+len(csr.EmailAddresses)+len(csr.URIs) != 0 {
		return value, fmt.Errorf("invalid stored group certificate request")
	}
	if (value.Certificate == "") != (value.CA == "") {
		return value, fmt.Errorf("incomplete installed group certificate")
	}
	return value, nil
}

func writeMaterial(root *os.Root, value material) error {
	data, err := json.Marshal(value)
	if err != nil || len(data) > 32768 {
		return fmt.Errorf("group key material exceeds its bound")
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return err
	}
	name := fmt.Sprintf(".write-%x", nonce)
	f, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer root.Remove(name)
	_, err = f.Write(data)
	err = errors.Join(err, f.Sync(), f.Close())
	if err != nil {
		return err
	}
	if err := root.Rename(name, "material.json"); err != nil {
		return err
	}
	dir, err := root.Open(".")
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}

func parsePEM(value, kind string) ([]byte, error) {
	if len(value) > 16384 {
		return nil, fmt.Errorf("group certificate material too large")
	}
	b, rest := pem.Decode([]byte(value))
	if b == nil || b.Type != kind || len(b.Headers) != 0 || len(bytes.TrimSpace(rest)) != 0 {
		return nil, fmt.Errorf("expected one %s PEM block", kind)
	}
	return b.Bytes, nil
}

func parseKey(value string) (*ecdsa.PrivateKey, error) {
	der, err := parsePEM(value, "PRIVATE KEY")
	if err != nil {
		return nil, err
	}
	k, err := x509.ParsePKCS8PrivateKey(der)
	if err != nil {
		return nil, err
	}
	key, ok := k.(*ecdsa.PrivateKey)
	if !ok || key.Curve != elliptic.P256() {
		return nil, fmt.Errorf("expected P256 group key")
	}
	return key, nil
}

func samePublic(a, b any) bool {
	x, err := x509.MarshalPKIXPublicKey(a)
	y, other := x509.MarshalPKIXPublicKey(b)
	return err == nil && other == nil && bytes.Equal(x, y)
}

func result(v material) Enrollment {
	return Enrollment{Protocol: 1, Instance: v.Binding.Instance, Revision: v.Binding.Revision, Generation: v.Binding.Generation,
		Topology: v.Manifest.Fingerprint(), Name: v.Manifest.Name(), CSR: v.CSR, Installed: v.Certificate != ""}
}

func (s Store) Enroll(b Binding, m Manifest) (Enrollment, error) {
	dir, fresh, err := s.directory(b, m, true)
	if err != nil {
		return Enrollment{}, err
	}
	defer dir.Close()
	if fresh {
		key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			return Enrollment{}, err
		}
		der, err := x509.MarshalPKCS8PrivateKey(key)
		if err != nil {
			return Enrollment{}, err
		}
		csr, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{DNSNames: []string{m.Name()}}, key)
		if err != nil {
			return Enrollment{}, err
		}
		value := material{Protocol: 1, Binding: b, Manifest: m, Key: string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})),
			CSR: string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: csr}))}
		if err := writeMaterial(dir, value); err != nil {
			return Enrollment{}, err
		}
		// Persist the new attempt directory before acknowledging its public CSR.
		parent, err := os.Open(s.Root)
		if err != nil {
			return Enrollment{}, err
		}
		err = errors.Join(parent.Sync(), parent.Close())
		if err != nil {
			return Enrollment{}, err
		}
	}
	v, err := readMaterial(dir, b, m)
	if err != nil {
		return Enrollment{}, err
	}
	return result(v), nil
}

func validateCertificate(v material, certificate, authority string) (string, string, error) {
	der, err := parsePEM(certificate, "CERTIFICATE")
	if err != nil {
		return "", "", err
	}
	caDER, err := parsePEM(authority, "CERTIFICATE")
	if err != nil {
		return "", "", err
	}
	if hash(caDER) != v.Manifest.CAHash {
		return "", "", fmt.Errorf("group certificate authority does not match the package")
	}
	ca, err := x509.ParseCertificate(caDER)
	if err != nil || !ca.IsCA || ca.CheckSignatureFrom(ca) != nil {
		return "", "", fmt.Errorf("invalid group certificate authority")
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		return "", "", fmt.Errorf("invalid group certificate")
	}
	key, err := parseKey(v.Key)
	if err != nil {
		return "", "", err
	}
	if cert.IsCA || !samePublic(cert.PublicKey, &key.PublicKey) || len(cert.DNSNames) != 1 || cert.DNSNames[0] != v.Manifest.Name() ||
		len(cert.IPAddresses)+len(cert.URIs)+len(cert.EmailAddresses) != 0 || cert.KeyUsage != x509.KeyUsageDigitalSignature ||
		len(cert.ExtKeyUsage) != 2 || len(cert.UnknownExtKeyUsage) != 0 || cert.NotAfter.Sub(cert.NotBefore) > 24*time.Hour {
		return "", "", fmt.Errorf("certificate does not match this group's node key and identity")
	}
	seen := map[x509.ExtKeyUsage]bool{}
	for _, usage := range cert.ExtKeyUsage {
		seen[usage] = true
	}
	if !seen[x509.ExtKeyUsageClientAuth] || !seen[x509.ExtKeyUsageServerAuth] {
		return "", "", fmt.Errorf("group certificate requires mutual TLS usages")
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	now := time.Now()
	if cert.NotAfter.Before(now.Add(30 * time.Second)) {
		return "", "", fmt.Errorf("group certificate is expired or about to expire")
	}
	for _, usage := range []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth} {
		if _, err := cert.Verify(x509.VerifyOptions{Roots: roots, DNSName: v.Manifest.Name(), CurrentTime: now, KeyUsages: []x509.ExtKeyUsage{usage}}); err != nil {
			return "", "", fmt.Errorf("group certificate verification failed")
		}
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})), string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER})), nil
}

func (s Store) Install(b Binding, m Manifest, certificate, authority string) (Enrollment, error) {
	dir, _, err := s.directory(b, m, false)
	if err != nil {
		return Enrollment{}, err
	}
	defer dir.Close()
	v, err := readMaterial(dir, b, m)
	if err != nil {
		return Enrollment{}, err
	}
	cert, ca, err := validateCertificate(v, certificate, authority)
	if err != nil {
		return Enrollment{}, err
	}
	if v.Certificate != "" {
		if cert != v.Certificate || ca != v.CA {
			return Enrollment{}, fmt.Errorf("certificate replacement requires a new group attempt")
		}
	} else {
		v.Certificate, v.CA = cert, ca
		if err := writeMaterial(dir, v); err != nil {
			return Enrollment{}, err
		}
	}
	return result(v), nil
}
