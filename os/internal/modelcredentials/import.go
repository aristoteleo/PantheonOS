package modelcredentials

// Importer accepts owner-authorized, short-lived encrypted deliveries. It uses
// the existing endpoint-bound vault and cannot read/export/delete/rotate keys.
// The authenticated Fleet control plane is trusted for node key discovery;
// encryption additionally keeps plaintext out of broker messages and logs.
import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sync"
	"time"
)

const importInfo = "pantheon/node-credential-import/v1"

var ErrImport = errors.New("credential delivery expired, invalid or conflicts; no existing credential was replaced")

type ImportChallenge struct {
	Protocol  int    `json:"protocol"`
	Owner     string `json:"owner"`
	Node      string `json:"node_id"`
	ID        string `json:"challenge_id"`
	Ref       string `json:"ref"`
	Endpoint  string `json:"endpoint"`
	Expires   int64  `json:"expires"`
	PublicKey string `json:"public_key"`
	Context   string `json:"context"`
}
type ImportEnvelope struct {
	PublicKey string `json:"public_key"`
	Nonce     string `json:"nonce"`
	Data      string `json:"data"`
}
type importAttempt struct {
	key       *ecdh.PrivateKey
	challenge ImportChallenge
	aad       []byte
}
type Importer struct {
	mu                sync.Mutex
	root, owner, node string
	pending           map[string]importAttempt
	now               func() time.Time
	closed            bool
}

func NewImporter(root, owner, node string) *Importer {
	return &Importer{root: root, owner: owner, node: node, pending: map[string]importAttempt{}, now: time.Now}
}
func (s *Importer) Prepare(ref, endpoint string) (ImportChallenge, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := Name(ref); err != nil || s.closed {
		return ImportChallenge{}, ErrImport
	}
	endpoint, err := Endpoint(endpoint)
	if err != nil {
		return ImportChallenge{}, ErrImport
	}
	now := s.now().Unix()
	for id, attempt := range s.pending {
		if attempt.challenge.Expires <= now {
			delete(s.pending, id)
		}
	}
	if len(s.pending) >= 16 {
		return ImportChallenge{}, ErrImport
	}
	key, err := ecdh.P256().GenerateKey(rand.Reader)
	if err != nil {
		return ImportChallenge{}, ErrImport
	}
	id := make([]byte, 16)
	if _, err = rand.Read(id); err != nil {
		return ImportChallenge{}, ErrImport
	}
	challenge := ImportChallenge{Protocol: 1, Owner: s.owner, Node: s.node, ID: hex.EncodeToString(id), Ref: ref,
		Endpoint: endpoint, Expires: now + 120, PublicKey: base64.StdEncoding.EncodeToString(key.PublicKey().Bytes())}
	// Context is a canonical, opaque authenticated transcript for cross-language
	// callers. It binds the destination, endpoint, reference, expiry and public key.
	aad, err := json.Marshal(challenge)
	if err != nil {
		return ImportChallenge{}, ErrImport
	}
	challenge.Context = base64.StdEncoding.EncodeToString(aad)
	s.pending[challenge.ID] = importAttempt{key, challenge, aad}
	return challenge, nil
}
func (s *Importer) Ensure(id string, envelope ImportEnvelope) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	attempt, ok := s.pending[id]
	delete(s.pending, id) // Every submission consumes the one-time challenge.
	if !ok || attempt.challenge.Expires <= s.now().Unix() || len(envelope.PublicKey) > 128 || len(envelope.Nonce) > 32 || len(envelope.Data) > 12000 {
		return ErrImport
	}
	epk, e1 := base64.StdEncoding.DecodeString(envelope.PublicKey)
	nonce, e2 := base64.StdEncoding.DecodeString(envelope.Nonce)
	data, e3 := base64.StdEncoding.DecodeString(envelope.Data)
	if e1 != nil || e2 != nil || e3 != nil {
		return ErrImport
	}
	peer, err := ecdh.P256().NewPublicKey(epk)
	if err != nil {
		return ErrImport
	}
	secret, err := attempt.key.ECDH(peer)
	if err != nil {
		return ErrImport
	}
	defer clear(secret)
	derived, err := hkdf.Key(sha256.New, secret, nil, importInfo, 32)
	if err != nil {
		return ErrImport
	}
	defer clear(derived)
	block, err := aes.NewCipher(derived)
	if err != nil {
		return ErrImport
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil || len(nonce) != gcm.NonceSize() {
		return ErrImport
	}
	plain, err := gcm.Open(nil, nonce, data, attempt.aad)
	if err != nil {
		return ErrImport
	}
	defer clear(plain)
	if err := Ensure(s.root, attempt.challenge.Ref, attempt.challenge.Endpoint, string(plain)); err != nil {
		return ErrImport
	}
	return nil
}
func (s *Importer) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	clear(s.pending)
	s.closed = true
}
