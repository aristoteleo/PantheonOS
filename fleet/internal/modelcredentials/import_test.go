package modelcredentials

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func encryptImport(t *testing.T, q ImportChallenge, key string) ImportEnvelope {
	t.Helper()
	private, err := ecdh.P256().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := base64.StdEncoding.DecodeString(q.PublicKey)
	peer, err := ecdh.P256().NewPublicKey(raw)
	if err != nil {
		t.Fatal(err)
	}
	shared, err := private.ECDH(peer)
	if err != nil {
		t.Fatal(err)
	}
	derived, err := hkdf.Key(sha256.New, shared, nil, importInfo, 32)
	if err != nil {
		t.Fatal(err)
	}
	block, _ := aes.NewCipher(derived)
	gcm, _ := cipher.NewGCM(block)
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		t.Fatal(err)
	}
	aad, _ := base64.StdEncoding.DecodeString(q.Context)
	return ImportEnvelope{base64.StdEncoding.EncodeToString(private.PublicKey().Bytes()), base64.StdEncoding.EncodeToString(nonce), base64.StdEncoding.EncodeToString(gcm.Seal(nil, nonce, []byte(key), aad))}
}

func TestImportRetryConflictAndTranscript(t *testing.T) {
	root := filepath.Join(t.TempDir(), "vault")
	s := NewImporter(root, "owner", "node")
	const ref = "node-secret://budget"
	const endpoint = "https://budget.test/proxy/v1"
	const key = "test-private-budget-key"
	var original []byte
	for _, value := range []string{key, key, "different-key"} {
		q, err := s.Prepare(ref, endpoint)
		if err != nil {
			t.Fatal(err)
		}
		aad, _ := base64.StdEncoding.DecodeString(q.Context)
		var transcript ImportChallenge
		if json.Unmarshal(aad, &transcript) != nil {
			t.Fatal("invalid transcript")
		}
		expected := q
		expected.Context = ""
		if transcript != expected {
			t.Fatal("transcript does not bind complete challenge")
		}
		envelope := encryptImport(t, q, value)
		body, _ := json.Marshal(envelope)
		if strings.Contains(string(body), value) {
			t.Fatal("plaintext in envelope")
		}
		err = s.Ensure(q.ID, envelope)
		if value == key && err != nil || value != key && err != ErrImport {
			t.Fatalf("unexpected ensure: %v", err)
		}
		if s.Ensure(q.ID, envelope) != ErrImport {
			t.Fatal("replay accepted")
		}
		got, err := Read(root, ref, endpoint)
		if err != nil || got != key {
			t.Fatal("wrong stored credential")
		}
		bytes, _ := os.ReadFile(filepath.Join(root, "credential-budget.json"))
		if original != nil && string(bytes) != string(original) {
			t.Fatal("existing credential rewritten")
		}
		original = bytes
	}
	q, _ := s.Prepare(ref, "https://other.test/v1")
	if s.Ensure(q.ID, encryptImport(t, q, key)) != ErrImport {
		t.Fatal("endpoint replacement accepted")
	}
}

func TestImportRejectsInvalidDelivery(t *testing.T) {
	for _, mode := range []string{"expired", "public-key", "nonce", "cipher", "context", "other-node", "other-owner", "close", "empty-key", "oversized-key"} {
		t.Run(mode, func(t *testing.T) {
			root := filepath.Join(t.TempDir(), "vault")
			s := NewImporter(root, "owner", "node")
			now := time.Now()
			s.now = func() time.Time { return now }
			q, err := s.Prepare("node-secret://budget", "https://api.test")
			if err != nil {
				t.Fatal(err)
			}
			value := "key"
			if mode == "empty-key" {
				value = ""
			}
			if mode == "oversized-key" {
				value = strings.Repeat("a", 8193)
			}
			if mode == "context" {
				q.Context = base64.StdEncoding.EncodeToString([]byte("different transcript"))
			}
			envelope := encryptImport(t, q, value)
			switch mode {
			case "expired":
				now = now.Add(121 * time.Second)
			case "public-key":
				envelope.PublicKey = "invalid"
			case "nonce":
				envelope.Nonce = "AA=="
			case "cipher":
				envelope.Data = "AA=="
			case "other-node":
				s = NewImporter(root, "owner", "other")
			case "other-owner":
				s = NewImporter(root, "other", "node")
			case "close":
				s.Close()
			}
			if s.Ensure(q.ID, envelope) != ErrImport {
				t.Fatal("invalid delivery accepted")
			}
			if _, err := Read(root, "node-secret://budget", "https://api.test"); err == nil {
				t.Fatal("unexpected write")
			}
		})
	}
}

func TestImportBoundsAndShutdown(t *testing.T) {
	s := NewImporter(t.TempDir(), "owner", "node")
	now := time.Now()
	s.now = func() time.Time { return now }
	if _, err := s.Prepare("bad-ref", "https://api.test"); err != ErrImport {
		t.Fatal("bad ref accepted")
	}
	if _, err := s.Prepare("node-secret://key", "http://remote.test"); err != ErrImport {
		t.Fatal("insecure endpoint accepted")
	}
	for i := 0; i < 16; i++ {
		if _, err := s.Prepare("node-secret://key", "https://api.test"); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := s.Prepare("node-secret://key", "https://api.test"); err != ErrImport {
		t.Fatal("unbounded pending challenges")
	}
	now = now.Add(121 * time.Second)
	if _, err := s.Prepare("node-secret://key", "https://api.test"); err != nil {
		t.Fatal(err)
	}
	s.Close()
	if len(s.pending) != 0 {
		t.Fatal("pending challenges retained")
	}
	if _, err := s.Prepare("node-secret://key", "https://api.test"); err != ErrImport {
		t.Fatal("prepare after close")
	}
}
