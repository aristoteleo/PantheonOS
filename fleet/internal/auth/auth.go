// Package auth implements the Fleet's NATS security: a single decentralized-JWT
// Authority (operator + system account + one shared FLEET account with
// JetStream) that mints per-fleet, subject-scoped user credentials.
//
// Isolation is by construction: every fleet's Registry lives in a JetStream KV
// bucket named FLEET_<fid>_NODES (stream KV_FLEET_<fid>_NODES) and its control
// subjects under fleet.<fid>.>. A minted user JWT only allows those subjects for
// its own <fid>, so a credential for fleet A literally cannot name fleet B's
// stream or subjects. The Controller holds the account signing key and mints a
// credential on each /join.
package auth

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/nats-io/jwt/v2"
	"github.com/nats-io/nkeys"
)

// AccessTTL bounds how long a minted node/agent credential is valid. The holder
// must refresh before it expires (fleet up runs a refresh loop), so a legit node
// stays online while a *leaked* credential dies fast. Configurable via
// FLEET_ACCESS_TTL (e.g. "30s") for testing/tuning. See docs/fleet-security-model.md.
var AccessTTL = func() time.Duration {
	if s := os.Getenv("FLEET_ACCESS_TTL"); s != "" {
		if d, err := time.ParseDuration(s); err == nil && d > 0 {
			return d
		}
	}
	return time.Hour
}()

// Authority holds the persisted keys and the (regenerated-each-boot) JWTs.
type Authority struct {
	opKP   nkeys.KeyPair
	opPub  string
	opJWT  string
	sysKP  nkeys.KeyPair
	sysPub string
	sysJWT string
	accKP  nkeys.KeyPair // the shared FLEET account — signs every user JWT
	accPub string
	accJWT string
}

// Bootstrap loads (or creates and persists) the operator, system, and FLEET
// account seeds in stateDir, then regenerates their JWTs. Only the seeds are
// persisted; the JWTs are deterministic enough to regenerate each boot (the
// account public key is stable, so previously-minted user creds stay valid).
func Bootstrap(stateDir string) (*Authority, error) {
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return nil, err
	}
	a := &Authority{}
	var err error
	if a.opKP, err = loadOrCreateSeed(filepath.Join(stateDir, "operator.seed"), nkeys.CreateOperator); err != nil {
		return nil, fmt.Errorf("operator key: %w", err)
	}
	if a.sysKP, err = loadOrCreateSeed(filepath.Join(stateDir, "sys.seed"), nkeys.CreateAccount); err != nil {
		return nil, fmt.Errorf("sys key: %w", err)
	}
	if a.accKP, err = loadOrCreateSeed(filepath.Join(stateDir, "account.seed"), nkeys.CreateAccount); err != nil {
		return nil, fmt.Errorf("account key: %w", err)
	}
	if a.opPub, err = a.opKP.PublicKey(); err != nil {
		return nil, err
	}
	if a.sysPub, err = a.sysKP.PublicKey(); err != nil {
		return nil, err
	}
	if a.accPub, err = a.accKP.PublicKey(); err != nil {
		return nil, err
	}

	// System account.
	sysClaims := jwt.NewAccountClaims(a.sysPub)
	sysClaims.Name = "SYS"
	if a.sysJWT, err = sysClaims.Encode(a.opKP); err != nil {
		return nil, err
	}

	// Shared FLEET account — JetStream enabled (server-level JS must also be on).
	accClaims := jwt.NewAccountClaims(a.accPub)
	accClaims.Name = "FLEET"
	accClaims.Limits.JetStreamLimits.DiskStorage = jwt.NoLimit
	accClaims.Limits.JetStreamLimits.MemoryStorage = jwt.NoLimit
	accClaims.Limits.JetStreamLimits.Streams = jwt.NoLimit
	accClaims.Limits.JetStreamLimits.Consumer = jwt.NoLimit
	if a.accJWT, err = accClaims.Encode(a.opKP); err != nil {
		return nil, err
	}

	// Operator JWT — self-signed, names the system account.
	opClaims := jwt.NewOperatorClaims(a.opPub)
	opClaims.Name = "fleet-operator"
	opClaims.SystemAccount = a.sysPub
	if a.opJWT, err = opClaims.Encode(a.opKP); err != nil {
		return nil, err
	}
	return a, nil
}

// ServerConfig returns a nats-server config that trusts this Authority: the
// operator JWT, the system account, JetStream, and a MEMORY resolver preloaded
// with the FLEET and SYS account JWTs.
func (a *Authority) ServerConfig(listen, jsStoreDir string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "listen: %s\n", listen)
	fmt.Fprintf(&b, "jetstream {\n  store_dir: %q\n  max_memory_store: 1G\n  max_file_store: 50G\n}\n", jsStoreDir)
	fmt.Fprintf(&b, "operator: %q\n", a.opJWT)
	fmt.Fprintf(&b, "system_account: %q\n", a.sysPub)
	fmt.Fprintf(&b, "resolver: MEMORY\n")
	fmt.Fprintf(&b, "resolver_preload: {\n  %s: %q\n  %s: %q\n}\n", a.accPub, a.accJWT, a.sysPub, a.sysJWT)
	return b.String()
}

// MintFleetUser issues a decorated .creds for fid, scoped to fleet.<fid>.> and
// the KV bucket FLEET_<fid>_NODES (stream KV_FLEET_<fid>_NODES) — nothing else.
func (a *Authority) MintFleetUser(fid string) ([]byte, error) {
	ukp, err := nkeys.CreateUser()
	if err != nil {
		return nil, err
	}
	upub, err := ukp.PublicKey()
	if err != nil {
		return nil, err
	}
	useed, err := ukp.Seed()
	if err != nil {
		return nil, err
	}

	uc := jwt.NewUserClaims(upub)
	uc.Name = "fleet-" + fid
	// Short-lived — the holder refreshes before this to stay connected.
	uc.Expires = time.Now().Add(AccessTTL).Unix()
	s := func(f string) string { return fmt.Sprintf(f, fid) }
	uc.Permissions.Pub.Allow = jwt.StringList{
		s("fleet.%s.>"),
		"$JS.API.INFO",                                   // account JetStream info (shared, not fleet data)
		s("$KV.FLEET_%s_NODES.>"),                        // KV data plane (put/del/purge-key)
		s("$JS.API.STREAM.CREATE.KV_FLEET_%s_NODES"),     // create bucket
		s("$JS.API.STREAM.UPDATE.KV_FLEET_%s_NODES"),     // CreateOrUpdate
		s("$JS.API.STREAM.INFO.KV_FLEET_%s_NODES"),       // bind/status
		s("$JS.API.STREAM.PURGE.KV_FLEET_%s_NODES"),      // purge bucket
		s("$JS.API.STREAM.DELETE.KV_FLEET_%s_NODES"),     // delete bucket
		s("$JS.API.DIRECT.GET.KV_FLEET_%s_NODES"),        // get-by-seq (bare, no trailing token)
		s("$JS.API.DIRECT.GET.KV_FLEET_%s_NODES.>"),      // get-latest-by-subject
		s("$JS.API.CONSUMER.CREATE.KV_FLEET_%s_NODES"),   // Keys/Watch — nats-py ephemeral (bare)
		s("$JS.API.CONSUMER.CREATE.KV_FLEET_%s_NODES.>"), // Keys/Watch — nats.go (named/filtered)
		s("$JS.API.CONSUMER.DELETE.KV_FLEET_%s_NODES.>"),
		s("$JS.API.CONSUMER.INFO.KV_FLEET_%s_NODES.>"),
		s("$JS.FC.*.*.KV_FLEET_%s_NODES.>"), // v2 flow-control replies (Watch/Keys)
		s("_INBOX_%s.>"),                    // replies to requests (e.g. a Node answering run_task)
	}
	uc.Permissions.Sub.Allow = jwt.StringList{
		s("_INBOX_%s.>"), // JS/PubAck/push/FC responses + our own request replies (per-fleet prefix)
		s("fleet.%s.>"),
	}
	userJWT, err := uc.Encode(a.accKP) // signed by the FLEET account
	if err != nil {
		return nil, err
	}
	return jwt.FormatUserConfig(userJWT, useed)
}

// AccountPubKey is the stable FLEET account id (handy for diagnostics).
func (a *Authority) AccountPubKey() string { return a.accPub }

// loadOrCreateSeed reads a persisted nkey seed, or creates one with mk and
// writes it 0600. Returning the KeyPair either way.
func loadOrCreateSeed(path string, mk func() (nkeys.KeyPair, error)) (nkeys.KeyPair, error) {
	if b, err := os.ReadFile(path); err == nil {
		return nkeys.FromSeed(b)
	}
	kp, err := mk()
	if err != nil {
		return nil, err
	}
	seed, err := kp.Seed()
	if err != nil {
		return nil, err
	}
	if err := os.WriteFile(path, seed, 0o600); err != nil {
		return nil, err
	}
	return kp, nil
}
