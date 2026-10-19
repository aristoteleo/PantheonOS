package groupcredentials

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"os"
	"reflect"
	"strconv"
	"time"
)

// AuthorityStore is owned by rank zero's Runner. Only the authenticated Fleet
// owner may invoke it; neither Apps nor narrow node credentials can issue peers.
// The manager serializes access and holds its exclusive node-state lock.
type AuthorityStore struct{ Root, Owner, Node string }

type AuthorityStatus struct {
	Protocol     int    `json:"protocol"`
	Owner        string `json:"owner"`
	Node         string `json:"node_id"`
	Group        string `json:"group_id"`
	TopologyHash string `json:"topology_sha256"`
	State        string `json:"state"`
	CAHash       string `json:"ca_sha256,omitempty"`
	CA           string `json:"ca_pem,omitempty"`
	Expires      string `json:"expires_at,omitempty"`
	Issued       int    `json:"issued_ranks"`
}

// Claim pins the exact enrollment, including an instance derived from
// owner/node/revision/scope and its original preparation operation. CSR proves
// possession of the node-local key. The owner coordinator verifies Fleet's
// enrollment response against its durable group intent before requesting issue.
type Claim struct {
	Rank        *int   `json:"rank"`
	Node        string `json:"node_id"`
	Instance    string `json:"instance_id"`
	Revision    string `json:"revision"`
	Scope       string `json:"scope"`
	Generation  uint64 `json:"generation"`
	Preparation string `json:"preparation_id"`
	CSR         string `json:"csr_pem"`
}

type Issuance struct {
	Protocol     int    `json:"protocol"`
	Group        string `json:"group_id"`
	TopologyHash string `json:"topology_sha256"`
	Rank         int    `json:"rank"`
	Node         string `json:"node_id"`
	Instance     string `json:"instance_id"`
	Revision     string `json:"revision"`
	Generation   uint64 `json:"generation"`
	Certificate  string `json:"certificate_pem"`
	CAHash       string `json:"ca_sha256"`
	CA           string `json:"ca_pem"`
}

type issuedRank struct {
	Claim       Claim  `json:"claim"`
	Certificate string `json:"certificate_pem"`
}
type authorityRecord struct {
	Protocol     int                   `json:"protocol"`
	Owner        string                `json:"owner"`
	Node         string                `json:"node_id"`
	Group        string                `json:"group_id"`
	TopologyHash string                `json:"topology_sha256"`
	Topology     *Topology             `json:"topology,omitempty"`
	State        string                `json:"state"`
	Key          string                `json:"key_pem,omitempty"`
	CA           string                `json:"ca_pem,omitempty"`
	Issued       map[string]issuedRank `json:"issued"`
}

func (s AuthorityStore) directory(group, fingerprint string, create bool) (*os.Root, bool, error) {
	if !ownerRE.MatchString(s.Owner) || !nodeRE.MatchString(s.Node) || !groupRE.MatchString(group) || !digestRE.MatchString(fingerprint) {
		return nil, false, fmt.Errorf("invalid group authority identity")
	}
	// The directory is keyed by group ID, not topology: a changed plan must not
	// silently create a second root for the same durable group intent.
	return openPrivateState(s.Root, hash([]byte(s.Owner+"\x00"+s.Node+"\x00"+group)), create, 128)
}

func authorityManifest(t Topology, rank int) Manifest {
	return Manifest{Protocol: 1, Rank: &rank, Topology: t}
}

func (s AuthorityStore) validateTopology(t Topology) (string, error) {
	data, _ := json.Marshal(t)
	parsed, err := ParseTopology(data)
	if err != nil || !reflect.DeepEqual(parsed, t) || t.Owner != s.Owner || t.Members[0].Node != s.Node {
		return "", fmt.Errorf("group authority requires this node as the exact roster leader")
	}
	return authorityManifest(t, 0).Fingerprint(), nil
}

func parseAuthorityCertificate(record authorityRecord) (*x509.Certificate, error) {
	der, err := parsePEM(record.CA, "CERTIFICATE")
	if err != nil {
		return nil, err
	}
	ca, err := x509.ParseCertificate(der)
	if err != nil || !ca.IsCA || !ca.BasicConstraintsValid || !ca.MaxPathLenZero || ca.MaxPathLen != 0 ||
		ca.KeyUsage != x509.KeyUsageCertSign || ca.CheckSignatureFrom(ca) != nil {
		return nil, fmt.Errorf("invalid stored group authority")
	}
	constraint := record.TopologyHash[:32] + "." + record.TopologyHash[32:] + ".fleet-model.invalid"
	if !ca.PermittedDNSDomainsCritical || len(ca.PermittedDNSDomains) != 1 || ca.PermittedDNSDomains[0] != constraint {
		return nil, fmt.Errorf("stored authority does not constrain this exact group")
	}
	return ca, nil
}

func (s AuthorityStore) read(root *os.Root, group, fingerprint string) (authorityRecord, error) {
	var record authorityRecord
	info, err := root.Lstat("authority.json")
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 131072 {
		return record, fmt.Errorf("group authority record is missing or invalid; never recreate this group")
	}
	f, err := root.Open("authority.json")
	if err != nil {
		return record, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, 131073))
	if err != nil || len(data) > 131072 {
		return record, fmt.Errorf("invalid group authority record")
	}
	if err := decode(data, &record); err != nil || record.Protocol != 1 || record.Owner != s.Owner || record.Node != s.Node ||
		record.Group != group || record.TopologyHash != fingerprint || record.Issued == nil || len(record.Issued) > 16 ||
		(record.State != "open" && record.State != "closed") {
		return record, fmt.Errorf("group authority identity changed or record is corrupt")
	}
	if record.Topology == nil {
		if record.State != "closed" || record.CA != "" || record.Key != "" || len(record.Issued) != 0 {
			return record, fmt.Errorf("invalid group authority tombstone")
		}
		return record, nil
	}
	actual, err := s.validateTopology(*record.Topology)
	if err != nil || actual != fingerprint || record.Topology.Group != group {
		return record, fmt.Errorf("stored group roster changed")
	}
	ca, err := parseAuthorityCertificate(record)
	if err != nil {
		return record, err
	}
	if record.State == "open" {
		key, err := parseKey(record.Key)
		if err != nil || !samePublic(&key.PublicKey, ca.PublicKey) {
			return record, fmt.Errorf("stored group authority key is missing or invalid")
		}
	} else if record.Key != "" {
		return record, fmt.Errorf("closed group retained a signing key")
	}
	for rank, issued := range record.Issued {
		claim, csr, err := validatedClaim(*record.Topology, issued.Claim)
		if err != nil || rank != strconv.Itoa(*claim.Rank) {
			return record, fmt.Errorf("stored issuance binding is invalid")
		}
		der, err := parsePEM(issued.Certificate, "CERTIFICATE")
		if err != nil {
			return record, err
		}
		cert, err := x509.ParseCertificate(der)
		if err != nil || cert.CheckSignatureFrom(ca) != nil || !samePublic(cert.PublicKey, csr.PublicKey) ||
			len(cert.DNSNames) != 1 || cert.DNSNames[0] != authorityManifest(*record.Topology, *claim.Rank).Name() {
			return record, fmt.Errorf("stored issuance no longer matches its original key")
		}
	}
	return record, nil
}

func writeAuthority(root *os.Root, record authorityRecord) error {
	data, err := json.Marshal(record)
	if err != nil || len(data) > 131072 {
		return fmt.Errorf("group authority exceeds its storage bound")
	}
	return writePrivateFile(root, "authority.json", data)
}

func authorityStatus(record authorityRecord) AuthorityStatus {
	status := AuthorityStatus{Protocol: 1, Owner: record.Owner, Node: record.Node, Group: record.Group, TopologyHash: record.TopologyHash, State: record.State, Issued: len(record.Issued)}
	if record.CA != "" {
		// Records have already been verified, including the pinned root.
		ca, _ := parseAuthorityCertificate(record)
		status.CA, status.CAHash, status.Expires = record.CA, hash(ca.Raw), ca.NotAfter.UTC().Format(time.RFC3339)
	}
	return status
}

func (s AuthorityStore) Prepare(t Topology) (AuthorityStatus, error) {
	fingerprint, err := s.validateTopology(t)
	if err != nil {
		return AuthorityStatus{}, err
	}
	dir, fresh, err := s.directory(t.Group, fingerprint, true)
	if err != nil {
		return AuthorityStatus{}, err
	}
	defer dir.Close()
	if fresh {
		key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			return AuthorityStatus{}, err
		}
		serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
		if err != nil {
			return AuthorityStatus{}, err
		}
		now := time.Now().UTC()
		ca := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: "Fleet group " + t.Group},
			NotBefore: now.Add(-time.Minute), NotAfter: now.Add(24 * time.Hour), IsCA: true, BasicConstraintsValid: true,
			KeyUsage: x509.KeyUsageCertSign, MaxPathLen: 0, MaxPathLenZero: true, PermittedDNSDomainsCritical: true,
			PermittedDNSDomains: []string{fingerprint[:32] + "." + fingerprint[32:] + ".fleet-model.invalid"}}
		der, err := x509.CreateCertificate(rand.Reader, ca, ca, &key.PublicKey, key)
		if err != nil {
			return AuthorityStatus{}, err
		}
		private, err := x509.MarshalPKCS8PrivateKey(key)
		if err != nil {
			return AuthorityStatus{}, err
		}
		record := authorityRecord{Protocol: 1, Owner: s.Owner, Node: s.Node, Group: t.Group, TopologyHash: fingerprint, Topology: &t, State: "open", Issued: map[string]issuedRank{},
			Key: string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: private})), CA: string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))}
		if err := writeAuthority(dir, record); err != nil {
			return AuthorityStatus{}, err
		}
	}
	record, err := s.read(dir, t.Group, fingerprint)
	if err != nil {
		return AuthorityStatus{}, err
	}
	return authorityStatus(record), nil
}

func (s AuthorityStore) Status(group, fingerprint string) (AuthorityStatus, error) {
	dir, _, err := s.directory(group, fingerprint, false)
	if err != nil {
		return AuthorityStatus{}, err
	}
	defer dir.Close()
	record, err := s.read(dir, group, fingerprint)
	if err != nil {
		return AuthorityStatus{}, err
	}
	return authorityStatus(record), nil
}

// Close is an irreversible issuance fence for this group ID. It also creates a
// tombstone if Prepare has not arrived, preventing a delayed creation after stop.
// It does not revoke existing certificates or stop engines; the coordinator must
// still fence/stop every original member and confirm resource release.
func (s AuthorityStore) Close(group, fingerprint string) (AuthorityStatus, error) {
	dir, fresh, err := s.directory(group, fingerprint, true)
	if err != nil {
		return AuthorityStatus{}, err
	}
	defer dir.Close()
	record := authorityRecord{Protocol: 1, Owner: s.Owner, Node: s.Node, Group: group, TopologyHash: fingerprint, State: "closed", Issued: map[string]issuedRank{}}
	if !fresh {
		record, err = s.read(dir, group, fingerprint)
		if err != nil {
			return AuthorityStatus{}, err
		}
	}
	if fresh || record.State != "closed" {
		record.State, record.Key = "closed", ""
		if err := writeAuthority(dir, record); err != nil {
			return AuthorityStatus{}, err
		}
	}
	return authorityStatus(record), nil
}

func validatedClaim(t Topology, c Claim) (Claim, *x509.CertificateRequest, error) {
	if c.Rank == nil || *c.Rank < 0 || *c.Rank >= len(t.Members) || !digestRE.MatchString(c.Revision) ||
		!preparationRE.MatchString(c.Scope) || !preparationRE.MatchString(c.Preparation) {
		return c, nil, fmt.Errorf("pin the exact group enrollment and preparation")
	}
	p := t.Members[*c.Rank]
	instance := hash([]byte(t.Owner + "\x00" + p.Node + "\x00" + c.Revision + "\x00" + c.Scope))[:32]
	if c.Node != p.Node || c.Generation != p.Generation || c.Instance != instance {
		return c, nil, fmt.Errorf("enrollment does not match this rank's exact instance")
	}
	der, err := parsePEM(c.CSR, "CERTIFICATE REQUEST")
	if err != nil || len(der) > 2048 {
		return c, nil, fmt.Errorf("invalid bounded group CSR")
	}
	csr, err := x509.ParseCertificateRequest(der)
	if err != nil || csr.CheckSignature() != nil || csr.SignatureAlgorithm != x509.ECDSAWithSHA256 ||
		len(csr.Subject.Names) != 0 || len(csr.Extensions) != 1 || !csr.Extensions[0].Id.Equal([]int{2, 5, 29, 17}) ||
		len(csr.DNSNames) != 1 || csr.DNSNames[0] != authorityManifest(t, *c.Rank).Name() ||
		len(csr.IPAddresses)+len(csr.EmailAddresses)+len(csr.URIs) != 0 {
		return c, nil, fmt.Errorf("CSR does not prove the exact group's node key and SAN")
	}
	key, ok := csr.PublicKey.(*ecdsa.PublicKey)
	if !ok || key.Curve != elliptic.P256() {
		return c, nil, fmt.Errorf("group CSR requires a P256 key")
	}
	c.CSR = string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der}))
	return c, csr, nil
}

func issuance(record authorityRecord, issued issuedRank) Issuance {
	s := authorityStatus(record)
	c := issued.Claim
	return Issuance{Protocol: 1, Group: record.Group, TopologyHash: record.TopologyHash, Rank: *c.Rank, Node: c.Node, Instance: c.Instance, Revision: c.Revision,
		Generation: c.Generation, Certificate: issued.Certificate, CA: s.CA, CAHash: s.CAHash}
}

func (s AuthorityStore) Issue(group, fingerprint string, c Claim) (Issuance, error) {
	dir, _, err := s.directory(group, fingerprint, false)
	if err != nil {
		return Issuance{}, err
	}
	defer dir.Close()
	record, err := s.read(dir, group, fingerprint)
	if err != nil {
		return Issuance{}, err
	}
	if record.State != "open" {
		return Issuance{}, fmt.Errorf("group certificate issuance is closed")
	}
	c, csr, err := validatedClaim(*record.Topology, c)
	if err != nil {
		return Issuance{}, err
	}
	ca, err := parseAuthorityCertificate(record)
	if err != nil {
		return Issuance{}, err
	}
	now := time.Now().UTC()
	if now.Before(ca.NotBefore) || !now.Add(time.Minute).Before(ca.NotAfter) {
		return Issuance{}, fmt.Errorf("group authority is expired or not currently valid; create an explicit new group")
	}
	rank := strconv.Itoa(*c.Rank)
	if existing, ok := record.Issued[rank]; ok {
		if !reflect.DeepEqual(existing.Claim, c) {
			return Issuance{}, fmt.Errorf("rank already has its original key and binding; replacement requires a new group")
		}
		der, _ := parsePEM(existing.Certificate, "CERTIFICATE")
		cert, _ := x509.ParseCertificate(der)
		if !now.Add(30 * time.Second).Before(cert.NotAfter) {
			return Issuance{}, fmt.Errorf("original group certificate has expired; no implicit renewal")
		}
		return issuance(record, existing), nil
	}
	for _, existing := range record.Issued {
		_, other, _ := validatedClaim(*record.Topology, existing.Claim)
		if samePublic(csr.PublicKey, other.PublicKey) {
			return Issuance{}, fmt.Errorf("each rank requires its own node-local key")
		}
	}
	key, err := parseKey(record.Key)
	if err != nil {
		return Issuance{}, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return Issuance{}, err
	}
	expires := now.Add(12 * time.Hour)
	if expires.After(ca.NotAfter) {
		expires = ca.NotAfter
	}
	cert := &x509.Certificate{SerialNumber: serial, DNSNames: csr.DNSNames, NotBefore: now.Add(-time.Minute), NotAfter: expires,
		KeyUsage: x509.KeyUsageDigitalSignature, BasicConstraintsValid: true, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, cert, ca, csr.PublicKey, key)
	if err != nil {
		return Issuance{}, err
	}
	issued := issuedRank{Claim: c, Certificate: string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))}
	record.Issued[rank] = issued
	// Persist the exact certificate before returning it. A lost acknowledgement
	// never results in a second issuance or a new key for the same rank.
	if err := writeAuthority(dir, record); err != nil {
		return Issuance{}, err
	}
	return issuance(record, issued), nil
}
