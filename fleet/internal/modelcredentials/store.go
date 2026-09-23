// Package modelcredentials stores node-local API credentials, never Fleet/Hub keys.
// The endpoint is stored with the key, so changing a connector URL cannot send it
// to another provider. Only the local node administrator can provision credentials.
package modelcredentials

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"regexp"
	"strings"
)

var namePattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,63}$`)
var ErrCredential = errors.New("node credential unavailable, invalid, or not authorized for this endpoint")

const Prefix = "node-secret://"
const maxRecord = 32768

type record struct {
	Protocol   int    `json:"protocol"`
	Protection string `json:"protection"`
	Payload    []byte `json:"payload"`
}
type credential struct {
	Endpoint string `json:"endpoint"`
	Key      string `json:"key"`
}

func Name(ref string) (string, error) {
	if !strings.HasPrefix(ref, Prefix) || !namePattern.MatchString(strings.TrimPrefix(ref, Prefix)) {
		return "", ErrCredential
	}
	return strings.TrimPrefix(ref, Prefix), nil
}
func Endpoint(value string) (string, error) {
	if len(value) > 2048 || strings.ContainsAny(value, "\r\n\t ") {
		return "", ErrCredential
	}
	value = strings.TrimRight(value, "/")
	p, err := url.Parse(value)
	if err != nil || p.Hostname() == "" || p.User != nil || p.RawQuery != "" || p.ForceQuery || p.Fragment != "" || strings.Contains(value, "#") {
		return "", ErrCredential
	}
	local := p.Hostname() == "localhost" || p.Hostname() == "127.0.0.1" || p.Hostname() == "::1"
	if p.Scheme != "https" && !(p.Scheme == "http" && local) {
		return "", ErrCredential
	}
	if p.Path == "" {
		value += "/v1"
	}
	return value, nil
}
func ValidKey(key string) bool {
	if len(key) == 0 || len(key) > 8192 {
		return false
	}
	for _, c := range []byte(key) {
		if c < 33 || c > 126 {
			return false
		}
	}
	return true
}
func open(root string, create bool) (*os.Root, error) {
	if create {
		if err := os.MkdirAll(root, 0700); err != nil {
			return nil, ErrCredential
		}
	}
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || !private(info) {
		return nil, ErrCredential
	}
	r, err := os.OpenRoot(root)
	if err != nil {
		return nil, ErrCredential
	}
	return r, nil
}
func filename(name string) string { return "credential-" + name + ".json" }

// Put never accepts keys on command lines. Replacement is an explicit local action.
func Put(root, ref, endpoint, key string, replace bool) error {
	name, err := Name(ref)
	if err != nil {
		return err
	}
	endpoint, err = Endpoint(endpoint)
	if err != nil || !ValidKey(key) {
		return ErrCredential
	}
	plain, _ := json.Marshal(credential{endpoint, key})
	defer clear(plain)
	payload, err := protect(plain)
	if err != nil {
		return ErrCredential
	}
	defer clear(payload)
	raw, err := json.Marshal(record{1, protection, payload})
	if err != nil || len(raw) > maxRecord {
		return ErrCredential
	}
	defer clear(raw)
	r, err := open(root, true)
	if err != nil {
		return err
	}
	defer r.Close()
	random := make([]byte, 16)
	if _, err = rand.Read(random); err != nil {
		return ErrCredential
	}
	tmp := ".credential-" + hex.EncodeToString(random)
	f, err := r.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return ErrCredential
	}
	defer r.Remove(tmp)
	_, err = f.Write(raw)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil || closeErr != nil {
		return ErrCredential
	}
	if replace {
		err = r.Rename(tmp, filename(name))
	} else {
		err = r.Link(tmp, filename(name))
	}
	if err != nil {
		return fmt.Errorf("credential already exists or cannot be stored; use explicit replacement only to rotate it")
	}
	return nil
}
func Read(root, ref, endpoint string) (string, error) {
	name, err := Name(ref)
	if err != nil {
		return "", err
	}
	endpoint, err = Endpoint(endpoint)
	if err != nil {
		return "", err
	}
	r, err := open(root, false)
	if err != nil {
		return "", err
	}
	defer r.Close()
	info, err := r.Lstat(filename(name))
	if err != nil || !info.Mode().IsRegular() || !private(info) {
		return "", ErrCredential
	}
	f, err := r.Open(filename(name))
	if err != nil {
		return "", ErrCredential
	}
	defer f.Close()
	info, err = f.Stat()
	if err != nil || !info.Mode().IsRegular() || !private(info) {
		return "", ErrCredential
	}
	raw, err := io.ReadAll(io.LimitReader(f, maxRecord+1))
	defer clear(raw)
	var sealed record
	if err != nil || len(raw) > maxRecord || json.Unmarshal(raw, &sealed) != nil || sealed.Protocol != 1 || sealed.Protection != protection {
		return "", ErrCredential
	}
	defer clear(sealed.Payload)
	plain, err := unprotect(sealed.Payload)
	if err != nil {
		return "", ErrCredential
	}
	defer clear(plain)
	var value credential
	if json.Unmarshal(plain, &value) != nil || value.Endpoint != endpoint || !ValidKey(value.Key) {
		return "", ErrCredential
	}
	return value.Key, nil
}
func Delete(root, ref string) error {
	name, err := Name(ref)
	if err != nil {
		return err
	}
	r, err := open(root, false)
	if err != nil {
		return err
	}
	defer r.Close()
	if err = r.Remove(filename(name)); err != nil {
		return ErrCredential
	}
	return nil
}
func List(root string) ([]string, error) {
	r, err := open(root, false)
	if err != nil {
		return nil, err
	}
	defer r.Close()
	f, err := r.Open(".")
	if err != nil {
		return nil, ErrCredential
	}
	defer f.Close()
	entries, err := f.ReadDir(1001)
	if err != nil && err != io.EOF {
		return nil, ErrCredential
	}
	if len(entries) > 1000 {
		return nil, errors.New("credential store exceeds listing limit")
	}
	refs := []string{}
	for _, e := range entries {
		name := strings.TrimSuffix(strings.TrimPrefix(e.Name(), "credential-"), ".json")
		if !e.IsDir() && e.Name() == filename(name) && namePattern.MatchString(name) {
			refs = append(refs, Prefix+name)
		}
	}
	return refs, nil
}
