package deployments

// Owner secrets the reconciler delivers to whichever node runs an App that
// references them ({"$secret": name}). The controller holds them sealed with
// a key kept only in its private state directory (AES-256-GCM, bound to fleet,
// name and endpoint). Values are never returned by the API; delivery to a node
// uses the node's one-time ECDH import challenge. Docs §7.

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
)

const maxSecretsPerFleet = 64

var ErrNoSecret = errors.New("secret not found")

// SecretInfo is what the API shows: never the value.
type SecretInfo struct {
	Name     string `json:"name"`
	Endpoint string `json:"endpoint"`
	Version  string `json:"version"`
	Updated  int64  `json:"updated"`
}

type sealedSecret struct {
	SecretInfo
	Nonce []byte `json:"nonce"`
	Data  []byte `json:"data"`
}

type Secrets struct {
	mu   sync.Mutex
	root *os.Root
	aead cipher.AEAD
	key  []byte
}

// OpenSecrets opens (creating) the sealed secret directory.
func OpenSecrets(path string) (*Secrets, error) {
	if err := os.MkdirAll(path, 0o700); err != nil {
		return nil, fmt.Errorf("secret store unavailable")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || !private(info) {
		return nil, fmt.Errorf("secret store must be a private directory")
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		return nil, err
	}
	key, err := sealKey(root)
	if err != nil {
		root.Close()
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		root.Close()
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		root.Close()
		return nil, err
	}
	return &Secrets{root: root, aead: aead, key: key}, nil
}

func sealKey(root *os.Root) ([]byte, error) {
	if st, err := root.Lstat("seal.key"); err == nil {
		if !st.Mode().IsRegular() || !private(st) || st.Size() != 32 {
			return nil, fmt.Errorf("secret seal key is not a private 32-byte file; recovery required")
		}
		f, err := root.Open("seal.key")
		if err != nil {
			return nil, err
		}
		defer f.Close()
		key := make([]byte, 32)
		if _, err := io.ReadFull(f, key); err != nil {
			return nil, err
		}
		return key, nil
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	f, err := root.OpenFile("seal.key", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	_, err = f.Write(key)
	if err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	return key, err
}

func aad(fleet, name, endpoint string) []byte {
	return []byte(fleet + "\x00" + name + "\x00" + endpoint)
}

// Put stores (or replaces) a secret. The version changes only with the value
// or endpoint, so repeating a setup does not restart dependents.
func (s *Secrets) Put(fleet, name, value, endpoint string) (SecretInfo, error) {
	if !fleetRE.MatchString(fleet) || !nameRE.MatchString(name) {
		return SecretInfo{}, fmt.Errorf("invalid fleet or secret name")
	}
	if !modelcredentials.ValidKey(value) {
		return SecretInfo{}, fmt.Errorf("a secret is 1 to 8192 printable characters without spaces")
	}
	endpoint, err := modelcredentials.Endpoint(endpoint)
	if err != nil {
		return SecretInfo{}, fmt.Errorf("a secret is bound to the endpoint it authenticates to")
	}
	mac := hmac.New(sha256.New, s.key)
	mac.Write(aad(fleet, name, endpoint))
	mac.Write([]byte{0})
	mac.Write([]byte(value))
	info := SecretInfo{Name: name, Endpoint: endpoint, Version: hex.EncodeToString(mac.Sum(nil))[:16], Updated: time.Now().Unix()}
	s.mu.Lock()
	defer s.mu.Unlock()
	if current, err := s.read(fleet, name); err == nil && current.Version == info.Version {
		return current.SecretInfo, nil
	}
	if len(s.list(fleet)) >= maxSecretsPerFleet {
		return SecretInfo{}, fmt.Errorf("too many secrets for this fleet")
	}
	record := sealedSecret{SecretInfo: info, Nonce: make([]byte, s.aead.NonceSize())}
	if _, err := rand.Read(record.Nonce); err != nil {
		return SecretInfo{}, err
	}
	record.Data = s.aead.Seal(nil, record.Nonce, []byte(value), aad(fleet, name, endpoint))
	raw, err := json.Marshal(record)
	if err != nil {
		return SecretInfo{}, err
	}
	if err := s.root.Mkdir(fleet, 0o700); err != nil && !errors.Is(err, os.ErrExist) {
		return SecretInfo{}, fmt.Errorf("secret store write failed")
	}
	tmp := filepath.Join(fleet, "."+name+"-"+nonce())
	f, err := s.root.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return SecretInfo{}, fmt.Errorf("secret store write failed")
	}
	defer s.root.Remove(tmp)
	_, err = f.Write(raw)
	if err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = s.root.Rename(tmp, filepath.Join(fleet, name+".json"))
	}
	if err == nil {
		err = syncDir(s.root, fleet)
	}
	if err != nil {
		return SecretInfo{}, fmt.Errorf("secret store write failed")
	}
	return info, nil
}

func (s *Secrets) read(fleet, name string) (sealedSecret, error) {
	var record sealedSecret
	rel := filepath.Join(fleet, name+".json")
	st, err := s.root.Lstat(rel)
	if err != nil {
		return record, ErrNoSecret
	}
	if !st.Mode().IsRegular() || !private(st) || st.Size() > 64<<10 {
		return record, fmt.Errorf("secret record is not a private file")
	}
	f, err := s.root.Open(rel)
	if err != nil {
		return record, err
	}
	defer f.Close()
	if err := json.NewDecoder(io.LimitReader(f, 64<<10)).Decode(&record); err != nil || record.Name != name {
		return record, fmt.Errorf("secret record is invalid")
	}
	return record, nil
}

// Get returns a secret's value for delivery to a node.
func (s *Secrets) Get(fleet, name string) (value string, info SecretInfo, err error) {
	if !fleetRE.MatchString(fleet) || !nameRE.MatchString(name) {
		return "", info, ErrNoSecret
	}
	s.mu.Lock()
	record, err := s.read(fleet, name)
	s.mu.Unlock()
	if err != nil {
		return "", info, err
	}
	plain, err := s.aead.Open(nil, record.Nonce, record.Data, aad(fleet, name, record.Endpoint))
	if err != nil {
		return "", info, fmt.Errorf("secret %s cannot be unsealed", name)
	}
	return string(plain), record.SecretInfo, nil
}

// Info returns a secret's public description.
func (s *Secrets) Info(fleet, name string) (SecretInfo, error) {
	if !fleetRE.MatchString(fleet) || !nameRE.MatchString(name) {
		return SecretInfo{}, ErrNoSecret
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	record, err := s.read(fleet, name)
	return record.SecretInfo, err
}

func (s *Secrets) list(fleet string) []SecretInfo {
	entries, err := fs(s.root, fleet)
	if err != nil {
		return nil
	}
	var out []SecretInfo
	for _, name := range entries {
		if record, err := s.read(fleet, name); err == nil {
			out = append(out, record.SecretInfo)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func fs(root *os.Root, fleet string) ([]string, error) {
	dir, err := root.Open(fleet)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	names, err := dir.Readdirnames(-1)
	if err != nil {
		return nil, err
	}
	var out []string
	for _, n := range names {
		if strings.HasSuffix(n, ".json") && !strings.HasPrefix(n, ".") {
			out = append(out, strings.TrimSuffix(n, ".json"))
		}
	}
	return out, nil
}

// List describes a fleet's secrets.
func (s *Secrets) List(fleet string) []SecretInfo {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.list(fleet)
}

func (s *Secrets) Delete(fleet, name string) error {
	if !fleetRE.MatchString(fleet) || !nameRE.MatchString(name) {
		return ErrNoSecret
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.root.Remove(filepath.Join(fleet, name+".json")); err != nil {
		return ErrNoSecret
	}
	return syncDir(s.root, fleet)
}

func (s *Secrets) Close() error { return s.root.Close() }

// VaultRef is the node vault reference a secret version is imported under. A
// node vault never replaces a value, so each version has its own reference.
func VaultRef(name, version string) string {
	sum := sha256.Sum256([]byte(name + "\x00" + version))
	return modelcredentials.Prefix + "s" + hex.EncodeToString(sum[:12])
}
