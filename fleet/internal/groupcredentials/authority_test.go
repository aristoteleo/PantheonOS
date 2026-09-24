package groupcredentials

import (
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func authorityFixture(t *testing.T) (AuthorityStore, Manifest, AuthorityStatus) {
	t.Helper()
	_, _, m, _, _, _ := fixture(t)
	a := AuthorityStore{Root: filepath.Join(t.TempDir(), "authorities"), Owner: m.Topology.Owner, Node: m.Topology.Members[0].Node}
	status, err := a.Prepare(m.Topology)
	if err != nil {
		t.Fatal(err)
	}
	m.CAHash = status.CAHash
	return a, m, status
}

func enrollClaim(t *testing.T, m Manifest, rank int) (Store, Binding, Manifest, Claim) {
	t.Helper()
	m.Rank = &rank
	p := m.Topology.Members[rank]
	b := Binding{Owner: m.Topology.Owner, Node: p.Node, Revision: strings.Repeat("e", 64), Generation: p.Generation, Preparation: "prepare-original"}
	b.Instance = hash([]byte(b.Owner + "\x00" + b.Node + "\x00" + b.Revision + "\x00group"))[:32]
	s := Store{Root: filepath.Join(t.TempDir(), "peers")}
	e, err := s.Enroll(b, m)
	if err != nil {
		t.Fatal(err)
	}
	c := Claim{Rank: &rank, Node: b.Node, Instance: b.Instance, Revision: b.Revision, Scope: "group", Generation: b.Generation, Preparation: b.Preparation, CSR: e.CSR}
	return s, b, m, c
}

func TestAuthorityPersistsOneCertificatePerExactRankAndKey(t *testing.T) {
	a, m, status := authorityFixture(t)
	s, b, m, c := enrollClaim(t, m, 0)
	first, err := a.Issue(m.Topology.Group, m.Fingerprint(), c)
	if err != nil {
		t.Fatal(err)
	}
	// Simulates losing the reply and replacing the controller/Store object.
	restarted := AuthorityStore{Root: a.Root, Owner: a.Owner, Node: a.Node}
	again, err := restarted.Prepare(m.Topology)
	if err != nil || again.CA != status.CA || again.CAHash != status.CAHash || again.Issued != 1 {
		t.Fatal("authority rotated after restart", err)
	}
	second, err := restarted.Issue(m.Topology.Group, m.Fingerprint(), c)
	if err != nil || second != first {
		t.Fatal("retry minted a new certificate", err)
	}
	c.CSR = "\n" + c.CSR + "\n"
	second, err = restarted.Issue(m.Topology.Group, m.Fingerprint(), c)
	if err != nil || second != first {
		t.Fatal("PEM whitespace changed issuance", err)
	}
	installed, err := s.Install(b, m, first.Certificate, first.CA)
	if err != nil || !installed.Installed {
		t.Fatal("production node rejected issued certificate", err)
	}
	public, _ := json.Marshal([]any{status, first, installed})
	if strings.Contains(string(public), "PRIVATE KEY") || strings.Contains(string(public), a.Root) {
		t.Fatal("authority private state leaked")
	}
	_, _, _, replacement := enrollClaim(t, m, 0)
	if _, err := restarted.Issue(m.Topology.Group, m.Fingerprint(), replacement); err == nil {
		t.Fatal("rank key replaced")
	}
	for _, change := range []func(*Claim){func(c *Claim) { c.Preparation = "different" }, func(c *Claim) { c.Generation++ }, func(c *Claim) { c.Instance = strings.Repeat("a", 32) }, func(c *Claim) { c.Node = "foreign" }, func(c *Claim) { c.Scope = "other" }} {
		changed := c
		change(&changed)
		if _, err := restarted.Issue(m.Topology.Group, m.Fingerprint(), changed); err == nil {
			t.Fatal("rank binding replaced")
		}
	}
}

func TestAuthorityCertificateWorksForMutualTLSBetweenDistinctNodeKeys(t *testing.T) {
	a, m, _ := authorityFixture(t)
	var pairs []tls.Certificate
	roots := x509.NewCertPool()
	for rank := 0; rank < 2; rank++ {
		s, b, manifest, c := enrollClaim(t, m, rank)
		issued, err := a.Issue(m.Topology.Group, m.Fingerprint(), c)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := s.Install(b, manifest, issued.Certificate, issued.CA); err != nil {
			t.Fatal(err)
		}
		dir, _, err := s.directory(b, manifest, false)
		if err != nil {
			t.Fatal(err)
		}
		v, err := readMaterial(dir, b, manifest)
		dir.Close()
		if err != nil {
			t.Fatal(err)
		}
		pair, err := tls.X509KeyPair([]byte(v.Certificate), []byte(v.Key))
		if err != nil {
			t.Fatal(err)
		}
		pairs = append(pairs, pair)
		roots.AppendCertsFromPEM([]byte(v.CA))
	}
	listener, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{MinVersion: tls.VersionTLS13, Certificates: pairs[:1], ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	server := make(chan error, 1)
	go func() {
		conn, err := listener.Accept()
		if err != nil {
			server <- err
			return
		}
		defer conn.Close()
		conn.SetDeadline(time.Now().Add(3 * time.Second))
		tlsConn := conn.(*tls.Conn)
		if err := tlsConn.Handshake(); err != nil {
			server <- err
			return
		}
		if tlsConn.ConnectionState().PeerCertificates[0].DNSNames[0] != authorityManifest(m.Topology, 1).Name() {
			server <- io.ErrUnexpectedEOF
			return
		}
		_, err = conn.Write([]byte("ok"))
		server <- err
	}()
	conn, err := tls.DialWithDialer(&net.Dialer{Timeout: 3 * time.Second}, "tcp", listener.Addr().String(), &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots, Certificates: pairs[1:], ServerName: authorityManifest(m.Topology, 0).Name()})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(3 * time.Second))
	b := make([]byte, 2)
	if _, err := io.ReadFull(conn, b); err != nil || string(b) != "ok" {
		t.Fatal("mutual TLS failed", err)
	}
	if err := <-server; err != nil {
		t.Fatal(err)
	}
}

func TestAuthorityCloseFencesDelayedPrepareAndNeverReopens(t *testing.T) {
	for _, before := range []bool{true, false} {
		t.Run(map[bool]string{true: "before creation", false: "after issuance"}[before], func(t *testing.T) {
			_, _, m, _, _, _ := fixture(t)
			a := AuthorityStore{Root: filepath.Join(t.TempDir(), "authority"), Owner: m.Topology.Owner, Node: m.Topology.Members[0].Node}
			var claim Claim
			if !before {
				status, err := a.Prepare(m.Topology)
				if err != nil {
					t.Fatal(err)
				}
				m.CAHash = status.CAHash
				_, _, _, claim = enrollClaim(t, m, 0)
				if _, err := a.Issue(m.Topology.Group, m.Fingerprint(), claim); err != nil {
					t.Fatal(err)
				}
			}
			closed, err := a.Close(m.Topology.Group, m.Fingerprint())
			if err != nil || closed.State != "closed" {
				t.Fatal(err)
			}
			for i := 0; i < 2; i++ {
				if same, err := a.Prepare(m.Topology); err != nil || same != closed {
					t.Fatal("reopened group", err)
				}
				if same, err := a.Close(m.Topology.Group, m.Fingerprint()); err != nil || same != closed {
					t.Fatal("close retry changed group", err)
				}
			}
			if _, err := a.Issue(m.Topology.Group, m.Fingerprint(), claim); err == nil {
				t.Fatal("issued after terminal close")
			}
			dir, _, _ := a.directory(m.Topology.Group, m.Fingerprint(), false)
			defer dir.Close()
			data, _ := dir.ReadFile("authority.json")
			if strings.Contains(string(data), "PRIVATE KEY") {
				t.Fatal("closed record retained signing key")
			}
			changed := m.Topology
			changed.Model = strings.Repeat("f", 64)
			if _, err := a.Prepare(changed); err == nil {
				t.Fatal("replaced closed group topology")
			}
		})
	}
}

func TestAuthorityRejectsForeignLeaderAndCorruptedDurableState(t *testing.T) {
	a, m, _ := authorityFixture(t)
	for _, change := range []func(*AuthorityStore){func(s *AuthorityStore) { s.Owner = "f_bbbbbbbbbbbbbbbb" }, func(s *AuthorityStore) { s.Node = "n_second" }} {
		foreign := a
		change(&foreign)
		if _, err := foreign.Prepare(m.Topology); err == nil {
			t.Fatal("foreign authority accepted")
		}
	}
	for _, kind := range []string{"missing", "corrupt", "permissions", "symlink", "key", "roster"} {
		t.Run(kind, func(t *testing.T) {
			a, m, _ := authorityFixture(t)
			dir, _, err := a.directory(m.Topology.Group, m.Fingerprint(), false)
			if err != nil {
				t.Fatal(err)
			}
			defer dir.Close()
			switch kind {
			case "missing":
				err = dir.Remove("authority.json")
			case "corrupt":
				err = dir.WriteFile("authority.json", []byte("broken"), 0600)
			case "permissions":
				err = dir.Chmod("authority.json", 0644)
			case "symlink":
				err = dir.Remove("authority.json")
				if err == nil {
					err = dir.Symlink("other.json", "authority.json")
				}
			default:
				r, e := a.read(dir, m.Topology.Group, m.Fingerprint())
				if e != nil {
					t.Fatal(e)
				}
				if kind == "key" {
					r.Key = ""
				} else {
					r.Topology.Model = strings.Repeat("a", 64)
				}
				err = writeAuthority(dir, r)
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := a.Prepare(m.Topology); err == nil {
				t.Fatal("silently recreated broken authority")
			}
		})
	}
}

func TestAuthorityRefusesNewKeyForExpiredAuthorityAndBoundsState(t *testing.T) {
	a, m, status := authorityFixture(t)
	_, _, _, claim := enrollClaim(t, m, 0)
	dir, _, _ := a.directory(m.Topology.Group, m.Fingerprint(), false)
	defer dir.Close()
	r, err := a.read(dir, m.Topology.Group, m.Fingerprint())
	if err != nil {
		t.Fatal(err)
	}
	key, _ := parseKey(r.Key)
	ca, _ := parseAuthorityCertificate(r)
	ca.NotBefore = time.Now().Add(-2 * time.Hour)
	ca.NotAfter = time.Now().Add(-time.Hour)
	der, err := x509.CreateCertificate(rand.Reader, ca, ca, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	r.CA = string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
	if err := writeAuthority(dir, r); err != nil {
		t.Fatal(err)
	}
	expired, err := a.Prepare(m.Topology)
	if err != nil || expired.CAHash == status.CAHash {
		t.Fatal("fixture not expired", err)
	}
	if _, err := a.Issue(m.Topology.Group, m.Fingerprint(), claim); err == nil {
		t.Fatal("issued under expired authority")
	}
	if again, err := a.Prepare(m.Topology); err != nil || again != expired {
		t.Fatal("rotated expired authority", err)
	}
	// Empty pre-existing directories count toward the bound as uncertain state.
	for i := 0; i < 127; i++ {
		if err := os.Mkdir(filepath.Join(a.Root, hash([]byte{byte(i)})), 0700); err != nil {
			t.Fatal(err)
		}
	}
	newPlan := m.Topology
	newPlan.Group = "new-group"
	if _, err := a.Prepare(newPlan); err == nil || !strings.Contains(err.Error(), "limit") {
		t.Fatal("authority store grew without bound", err)
	}
}

func TestAuthorityRejectsCSRForWrongRankAndDuplicateNodeKey(t *testing.T) {
	a, m, _ := authorityFixture(t)
	s, b, manifest, c := enrollClaim(t, m, 0)
	if _, err := a.Issue(m.Topology.Group, m.Fingerprint(), c); err != nil {
		t.Fatal(err)
	}
	_, _, _, other := enrollClaim(t, m, 1)
	other.CSR = c.CSR
	if _, err := a.Issue(m.Topology.Group, m.Fingerprint(), other); err == nil {
		t.Fatal("wrong rank CSR accepted")
	}
	dir, _, _ := s.directory(b, manifest, false)
	defer dir.Close()
	v, _ := readMaterial(dir, b, manifest)
	key, _ := parseKey(v.Key)
	der, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{DNSNames: []string{authorityManifest(m.Topology, 1).Name()}}, key)
	if err != nil {
		t.Fatal(err)
	}
	other.CSR = string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der}))
	if _, err := a.Issue(m.Topology.Group, m.Fingerprint(), other); err == nil || !strings.Contains(err.Error(), "own node-local key") {
		t.Fatal("same key received two ranks", err)
	}
}
