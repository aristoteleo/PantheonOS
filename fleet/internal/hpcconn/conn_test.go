package hpcconn

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeSSH mimics a password + Duo login, a ControlMaster check/exit and remote commands.
const fakeSSH = `#!/bin/sh
state="$FAKE_STATE"
sock=""; prev=""
for a in "$@"; do [ "$prev" = "-S" ] && sock="$a"; prev="$a"; done
for a in "$@"; do
  case "$a" in
    check) [ -f "$state/up" ] && exit 0 || exit 1 ;;
    exit) rm -f "$state/up" "$sock"; exit 0 ;;
  esac
done
case " $* " in
  *" -M "*)
    printf 'Password: '
    read pw
    [ "$pw" = "hunter2" ] || { echo "Permission denied (keyboard-interactive)."; exit 255; }
    printf 'Duo two-factor login for u\n\nEnter a passcode or select one of the following options:\n\n 1. Duo Push to XXX-XXX-1234\n\nPasscode or option (1-1): '
    read opt
    [ "$opt" = "1" ] || exit 255
    touch "$state/up" "$sock"
    echo __PANTHEON_HPC_CONNECTED__
    exit 0 ;;
esac
[ -f "$state/up" ] || exit 255
eval "${@: -1}"
`

func seal(t *testing.T, answerKey, text string) Encrypted {
	pubBytes, _ := base64.StdEncoding.DecodeString(answerKey)
	pub, err := ecdh.P256().NewPublicKey(pubBytes)
	if err != nil {
		t.Fatal(err)
	}
	eph, _ := ecdh.P256().GenerateKey(rand.Reader)
	secret, _ := eph.ECDH(pub)
	k, _ := hkdf.Key(sha256.New, secret, nil, answerInfo, 32)
	block, _ := aes.NewCipher(k)
	gcm, _ := cipher.NewGCM(block)
	nonce := make([]byte, gcm.NonceSize())
	rand.Read(nonce)
	return Encrypted{EPK: base64.StdEncoding.EncodeToString(eph.PublicKey().Bytes()),
		Nonce: base64.StdEncoding.EncodeToString(nonce),
		Data:  base64.StdEncoding.EncodeToString(gcm.Seal(nil, nonce, []byte(text), nil))}
}

type memKeychain map[string]string

func (k memKeychain) available() bool                 { return true }
func (k memKeychain) has(c Cluster) bool              { _, ok := k[account(c)]; return ok }
func (k memKeychain) load(c Cluster) (string, bool)   { v, ok := k[account(c)]; return v, ok }
func (k memKeychain) save(c Cluster, pw string)       { k[account(c)] = pw }
func (k memKeychain) forget(c Cluster)                { delete(k, account(c)) }

func waitState(t *testing.T, m *Manager, id, want string) Status {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		st, _ := m.Status(id)
		if st.State == want {
			return st
		}
		if time.Now().After(deadline) {
			t.Fatalf("state %q, want %q (error %q)", st.State, want, st.Error)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func manager(t *testing.T) (*Manager, memKeychain) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "ssh")
	os.WriteFile(bin, []byte(strings.Replace(fakeSSH, "#!/bin/sh", "#!/bin/bash", 1)), 0o755)
	t.Setenv("FAKE_STATE", dir)
	kc := memKeychain{}
	return &Manager{Root: filepath.Join(dir, "clusters"), SSH: bin, kc: kc}, kc
}

func TestSignInRelaysPasswordAndDuoThenRunsCommands(t *testing.T) {
	m, kc := manager(t)
	c, err := m.Save(Cluster{Name: "", Host: "login.sherlock.stanford.edu", User: "u"})
	if err != nil || c.ID != "sherlock" || c.IdleMinutes != 30 {
		t.Fatalf("%+v %v", c, err)
	}
	if _, err := m.SignIn(c.ID); err != nil {
		t.Fatal(err)
	}
	st := waitState(t, m, c.ID, "prompt")
	if !st.Prompt.Secret || !strings.HasSuffix(st.Prompt.Text, "Password:") || !st.Prompt.Remember {
		t.Fatalf("password prompt: %+v", st.Prompt)
	}
	if _, err := m.Answer(c.ID, Encrypted{EPK: "AAAA", Nonce: "AAAA", Data: "AAAA"}, false); err == nil {
		t.Fatal("accepted an answer not sealed to the prompt key")
	}
	st, _ = m.Status(c.ID)
	if _, err := m.Answer(c.ID, seal(t, st.Prompt.AnswerKey, "hunter2"), true); err != nil {
		t.Fatal(err)
	}
	st = waitState(t, m, c.ID, "prompt")
	if st.Prompt.Secret || !strings.Contains(st.Prompt.Text, "Duo Push") {
		t.Fatalf("duo prompt: %+v", st.Prompt)
	}
	if _, err := m.Answer(c.ID, seal(t, st.Prompt.AnswerKey, "1"), false); err != nil {
		t.Fatal(err)
	}
	waitState(t, m, c.ID, "connected")
	if kc[account(c)] != "hunter2" {
		t.Fatal("the password was not remembered after a successful sign-in")
	}
	out, err := m.Run(context.Background(), c.ID, nil, "echo", "it's here")
	if err != nil || strings.TrimSpace(string(out)) != "it's here" {
		t.Fatalf("%q %v", out, err)
	}
	m.SignOut(c.ID)
	if _, err := m.Run(context.Background(), c.ID, nil, "echo", "x"); err != ErrSignedOut {
		t.Fatalf("ran signed out: %v", err)
	}
}

func TestRememberedPasswordIsTypedOnceAndForgottenWhenRejected(t *testing.T) {
	m, kc := manager(t)
	c, _ := m.Save(Cluster{Name: "Sherlock", Host: "login.sherlock.stanford.edu", User: "u"})
	kc[account(c)] = "hunter2"
	m.SignIn(c.ID)
	if st := waitState(t, m, c.ID, "prompt"); !strings.Contains(st.Prompt.Text, "Duo") {
		t.Fatalf("the remembered password was not used: %+v", st.Prompt)
	}
	m.SignOut(c.ID)
	kc[account(c)] = "wrong"
	m.SignIn(c.ID)
	st := waitState(t, m, c.ID, "signed_out")
	if !strings.Contains(st.Error, "denied") || kc.has(c) {
		t.Fatalf("%+v remembered=%v", st, kc.has(c))
	}
}
