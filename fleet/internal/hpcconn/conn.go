// Package hpcconn connects a Fleet node to HPC clusters over SSH sessions the
// user signs in to from PantheonOS.
//
// The session runs the system ssh client on this machine with a ControlMaster
// socket; every later cluster operation reuses it. Sign-in prompts (password,
// Duo, one-time codes — whatever the server asks) are relayed to the UI as
// they appear, and answers come back encrypted to a key only this process
// holds. Nothing here stores a second factor: a session that drops, or that
// nobody has used for the cluster's idle limit, ends and needs a new sign-in.
package hpcconn

import (
	"bytes"
	"context"
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
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/creack/pty"
)

// connectedMarker is printed by the first command once authentication succeeds.
const connectedMarker = "__PANTHEON_HPC_CONNECTED__"

// answerInfo binds derived answer keys to this protocol (the UI uses the same).
const answerInfo = "pantheon-fleet hpc answer v1"

var (
	hostRE = regexp.MustCompile(`^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$`)
	userRE = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$`)
	idRE   = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`)
	// A prompt is a last line waiting for input: "Password:", "(1-3):", "passcode?".
	promptRE = regexp.MustCompile(`[:?]\s*$`)
)

// Cluster is one HPC login endpoint.
type Cluster struct {
	AppEnvironment *AppEnvironment `json:"app_environment,omitempty"`
	ID             string          `json:"id"`
	Name           string          `json:"name"`
	Host           string          `json:"host"`
	User           string          `json:"user"`
	Port           int             `json:"port,omitempty"`
	Scheduler      string          `json:"scheduler"`      // slurm
	Access         string          `json:"access"`         // session: work goes through the signed-in session
	IdleMinutes    int             `json:"idle_minutes"`   // sign out after this long without use
	KeepConnected  bool            `json:"keep_connected"` // opt out of Fleet idle sign-out; server limits still apply
}

func (c *Cluster) normalize() error {
	if c.AppEnvironment != nil {
		if err := c.AppEnvironment.Validate(); err != nil {
			return err
		}
	}
	c.Host = strings.TrimSpace(c.Host)
	c.User = strings.TrimSpace(c.User)
	if c.Name = strings.TrimSpace(c.Name); c.Name == "" {
		c.Name = c.Host
	}
	switch {
	case !idRE.MatchString(c.ID):
		return errors.New("id: lowercase letters, digits and dashes")
	case !hostRE.MatchString(c.Host):
		return errors.New("host must be a hostname such as login.sherlock.stanford.edu")
	case !userRE.MatchString(c.User):
		return errors.New("invalid username")
	case c.Port != 0 && (c.Port < 1 || c.Port > 65535):
		return errors.New("invalid port")
	case len(c.Name) > 80:
		return errors.New("name is too long")
	}
	if c.Scheduler == "" {
		c.Scheduler = "slurm"
	}
	if c.Scheduler != "slurm" {
		return errors.New("only Slurm clusters are supported so far")
	}
	c.Access = "session"
	if c.IdleMinutes <= 0 {
		c.IdleMinutes = 30
	}
	c.IdleMinutes = min(c.IdleMinutes, 12*60)
	return nil
}

// Prompt is what the server is asking for right now.
type Prompt struct {
	Text      string `json:"text"`       // the server's words, last lines only
	Secret    bool   `json:"secret"`     // mask the input (passwords)
	AnswerKey string `json:"answer_key"` // P-256 public key (base64) to encrypt the answer to
	Remember  bool   `json:"remember"`   // a password this machine can keep in its keychain
}

// Status is a cluster and its session as the UI shows them.
type Status struct {
	Cluster
	State       string    `json:"state"` // signed_out | connecting | prompt | connected
	Prompt      *Prompt   `json:"prompt,omitempty"`
	Error       string    `json:"error,omitempty"`
	ConnectedAt time.Time `json:"connected_at,omitzero"`
	LastUsed    time.Time `json:"last_used,omitzero"`
	Remembered  bool      `json:"password_remembered"`
}

type session struct {
	cmd       *exec.Cmd
	pty       *os.File
	state     string
	prompt    *Prompt
	key       *ecdh.PrivateKey
	err       string
	connected time.Time
	active    int
	used      time.Time
	autofill  bool // the remembered password was already tried once
	remember  bool
	lastPass  string // the password just typed, kept only until the result is known
	out       bytes.Buffer
	done      chan struct{}
}

// Manager owns the clusters configured on this node and their sessions.
type Manager struct {
	Root string // cluster profiles and known_hosts
	// SSH is the ssh binary (tests replace it).
	SSH string
	mu  sync.Mutex
	ses map[string]*session
	kc  keychain
}

// Supported reports whether this OS can hold ControlMaster sessions.
func Supported() bool {
	if runtime.GOOS == "windows" {
		return false
	}
	_, err := exec.LookPath("ssh")
	return err == nil
}

func (m *Manager) ssh() string {
	if m.SSH != "" {
		return m.SSH
	}
	return "ssh"
}

func (m *Manager) init() {
	if m.ses == nil {
		m.ses = map[string]*session{}
	}
	if m.kc == nil {
		m.kc = systemKeychain()
	}
}

func (m *Manager) profile(id string) (Cluster, error) {
	var c Cluster
	if !idRE.MatchString(id) {
		return c, errors.New("unknown cluster")
	}
	b, err := os.ReadFile(filepath.Join(m.Root, id+".json"))
	if err != nil {
		return c, errors.New("unknown cluster")
	}
	err = json.Unmarshal(b, &c)
	return c, err
}

// Save adds or updates a cluster profile.
func (m *Manager) Save(c Cluster) (Cluster, error) {
	if c.ID == "" {
		c.ID = slug(c.Name, c.Host)
	}
	if err := c.normalize(); err != nil {
		return c, err
	}
	if old, err := m.profile(c.ID); err == nil && (old.Host != c.Host || old.User != c.User || old.Port != c.Port) {
		return c, errors.New("create a new cluster profile to change its host, user or port")
	}
	if err := os.MkdirAll(m.Root, 0o700); err != nil {
		return c, err
	}
	b, _ := json.MarshalIndent(c, "", "  ")
	return c, os.WriteFile(filepath.Join(m.Root, c.ID+".json"), b, 0o600)
}

// Remove signs out, forgets a remembered password and deletes the profile.
func (m *Manager) Remove(id string) error {
	c, err := m.profile(id)
	if err != nil {
		return err
	}
	m.SignOut(id)
	m.mu.Lock()
	m.init()
	m.kc.forget(c)
	m.mu.Unlock()
	return os.Remove(filepath.Join(m.Root, id+".json"))
}

// List returns every cluster with its session state.
func (m *Manager) List() []Status {
	entries, _ := os.ReadDir(m.Root)
	var out []Status
	for _, e := range entries {
		if id, ok := strings.CutSuffix(e.Name(), ".json"); ok {
			if st, err := m.Status(id); err == nil {
				out = append(out, st)
			}
		}
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Name < out[b].Name })
	return out
}

// Status reports one cluster's session.
func (m *Manager) Status(id string) (Status, error) {
	c, err := m.profile(id)
	if err != nil {
		return Status{}, err
	}
	m.mu.Lock()
	m.init()
	if m.ses[id] == nil {
		m.mu.Unlock()
		// A master that outlived this process (Fleet restarted into an update).
		if m.alive(c) {
			m.mu.Lock()
			if m.ses[id] == nil {
				m.ses[id] = &session{state: "connected", connected: time.Now(), used: time.Now(), done: closed()}
			}
		} else {
			m.mu.Lock()
		}
	}
	defer m.mu.Unlock()
	st := Status{Cluster: c, State: "signed_out", Remembered: m.kc.has(c)}
	if s := m.ses[id]; s != nil {
		st.State, st.Prompt, st.Error, st.ConnectedAt, st.LastUsed = s.state, s.prompt, s.err, s.connected, s.used
	}
	return st, nil
}

func (m *Manager) socket(c Cluster) string {
	// Unix socket paths are short (104 bytes on macOS): keep it in /tmp.
	sum := sha256.Sum256([]byte(m.Root + "\x00" + c.ID))
	return filepath.Join("/tmp", fmt.Sprintf("pfhpc-%d-%s", os.Getuid(), hex.EncodeToString(sum[:6])))
}

func (m *Manager) baseArgs(c Cluster) []string {
	args := []string{"-S", m.socket(c), "-o", "BatchMode=no",
		"-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + filepath.Join(m.Root, "known_hosts"),
		"-o", "ServerAliveInterval=60", "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=20"}
	if c.Port != 0 {
		args = append(args, "-p", strconv.Itoa(c.Port))
	}
	return append(args, "-l", c.User, c.Host)
}

// SignIn starts a session; prompts appear in Status as the server asks.
func (m *Manager) SignIn(id string) (Status, error) {
	c, err := m.profile(id)
	if err != nil {
		return Status{}, err
	}
	if m.alive(c) {
		m.mu.Lock()
		m.init()
		if s := m.ses[id]; s == nil || s.state != "connected" {
			m.ses[id] = &session{state: "connected", connected: time.Now(), used: time.Now(), done: closed()}
		}
		m.mu.Unlock()
		return m.Status(id)
	}
	m.mu.Lock()
	m.init()
	if s := m.ses[id]; s != nil && (s.state == "connecting" || s.state == "prompt") {
		m.mu.Unlock()
		return m.Status(id)
	}
	args := append([]string{"-M", "-o", "ControlPersist=yes", "-o", "NumberOfPasswordPrompts=1"}, m.baseArgs(c)...)
	args = append(args, "echo", connectedMarker)
	cmd := exec.Command(m.ssh(), args...)
	cmd.Env = append(os.Environ(), "SSH_ASKPASS=", "DISPLAY=") // prompts come to the terminal we own
	f, err := pty.Start(cmd)
	if err != nil {
		m.mu.Unlock()
		return Status{}, err
	}
	s := &session{cmd: cmd, pty: f, state: "connecting", done: make(chan struct{})}
	m.ses[id] = s
	m.mu.Unlock()
	go m.read(c, s)
	return m.Status(id)
}

func closed() chan struct{} {
	ch := make(chan struct{})
	close(ch)
	return ch
}

// read follows the sign-in conversation until the master is up or ssh gives up.
func (m *Manager) read(c Cluster, s *session) {
	defer close(s.done)
	buf := make([]byte, 4096)
	quiet := time.NewTimer(time.Hour)
	data := make(chan []byte)
	go func() {
		for {
			n, err := s.pty.Read(buf)
			if n > 0 {
				data <- append([]byte(nil), buf[:n]...)
			}
			if err != nil {
				close(data)
				return
			}
		}
	}()
	for {
		select {
		case b, ok := <-data:
			if !ok {
				m.finish(c, s)
				return
			}
			m.mu.Lock()
			s.out.Write(b)
			if s.out.Len() > 64<<10 {
				rest := append([]byte(nil), s.out.Bytes()[s.out.Len()-16<<10:]...)
				s.out.Reset()
				s.out.Write(rest)
			}
			if strings.Contains(s.out.String(), connectedMarker) {
				m.connected(c, s)
			} else if s.state == "prompt" {
				s.state, s.prompt = "connecting", nil
			}
			m.mu.Unlock()
			quiet.Reset(400 * time.Millisecond)
		case <-quiet.C:
			m.maybePrompt(c, s)
		}
	}
}

func (m *Manager) connected(c Cluster, s *session) {
	if s.state == "connected" {
		return
	}
	s.state, s.prompt, s.err, s.key = "connected", nil, "", nil
	s.connected, s.used = time.Now(), time.Now()
	if s.remember && s.lastPass != "" {
		m.kc.save(c, s.lastPass)
	}
	s.lastPass = ""
}

// maybePrompt turns a quiet, unterminated last line into a prompt for the UI.
func (m *Manager) maybePrompt(c Cluster, s *session) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if s.state != "connecting" {
		return
	}
	text := strings.ReplaceAll(s.out.String(), "\r", "")
	if strings.HasSuffix(text, "\n") || !promptRE.MatchString(text) {
		return
	}
	lines := strings.Split(strings.TrimSpace(text), "\n")
	last := lines[len(lines)-1]
	secret := regexp.MustCompile(`(?i)password|passphrase`).MatchString(last)
	if secret && !s.autofill {
		if pw, ok := m.kc.load(c); ok {
			s.autofill = true
			s.out.Reset()
			s.lastPass = pw
			io.WriteString(s.pty, pw+"\n") //nolint:errcheck
			return
		}
	}
	// Show the question with what the server said since the last answer (Duo
	// lists its options there), up to the last 10 non-empty lines.
	var shown []string
	for _, l := range lines {
		if l = strings.TrimRight(l, " \t"); strings.TrimSpace(l) != "" {
			shown = append(shown, l)
		}
	}
	shown = shown[max(0, len(shown)-10):]
	key, err := ecdh.P256().GenerateKey(rand.Reader)
	if err != nil {
		return
	}
	s.key = key
	s.state = "prompt"
	s.prompt = &Prompt{Text: strings.Join(shown, "\n"), Secret: secret,
		AnswerKey: base64.StdEncoding.EncodeToString(key.PublicKey().Bytes()), Remember: secret && m.kc.available()}
}

// finish records why ssh exited without a session.
func (m *Manager) finish(c Cluster, s *session) {
	s.cmd.Wait() //nolint:errcheck
	s.pty.Close()
	m.mu.Lock()
	defer m.mu.Unlock()
	if strings.Contains(s.out.String(), connectedMarker) {
		m.connected(c, s) // ControlPersist moved the master to the background
		return
	}
	if s.autofill && strings.Contains(strings.ToLower(s.out.String()), "denied") {
		m.kc.forget(c) // the remembered password stopped working
	}
	s.state, s.prompt, s.key, s.lastPass = "signed_out", nil, nil, ""
	s.err = lastLines(strings.ReplaceAll(s.out.String(), "\r", ""), 4)
	if s.err == "" {
		s.err = "The connection closed before sign-in finished."
	}
}

// Encrypted is an answer sealed to the prompt's answer key.
type Encrypted struct {
	EPK   string `json:"epk"`   // the UI's ephemeral P-256 public key
	Nonce string `json:"nonce"` // AES-GCM nonce
	Data  string `json:"data"`  // ciphertext
}

// Answer types the (decrypted) answer into the waiting prompt.
func (m *Manager) Answer(id string, enc Encrypted, remember bool) (Status, error) {
	if _, err := m.profile(id); err != nil {
		return Status{}, err
	}
	m.mu.Lock()
	m.init()
	s := m.ses[id]
	if s == nil || s.state != "prompt" || s.key == nil {
		m.mu.Unlock()
		return Status{}, errors.New("nothing is waiting for an answer")
	}
	text, err := open(s.key, enc)
	if err != nil {
		m.mu.Unlock()
		return Status{}, err
	}
	if strings.ContainsAny(text, "\r\n\x00") || len(text) > 512 {
		m.mu.Unlock()
		return Status{}, errors.New("invalid answer")
	}
	if s.prompt.Secret {
		s.remember, s.lastPass = remember && m.kc.available(), text
	}
	s.state, s.prompt, s.key = "connecting", nil, nil
	s.out.Reset()
	_, err = io.WriteString(s.pty, text+"\n")
	m.mu.Unlock()
	if err != nil {
		return Status{}, err
	}
	return m.Status(id)
}

func open(key *ecdh.PrivateKey, enc Encrypted) (string, error) {
	epk, err1 := base64.StdEncoding.DecodeString(enc.EPK)
	nonce, err2 := base64.StdEncoding.DecodeString(enc.Nonce)
	data, err3 := base64.StdEncoding.DecodeString(enc.Data)
	if err := errors.Join(err1, err2, err3); err != nil {
		return "", errors.New("malformed answer")
	}
	peer, err := ecdh.P256().NewPublicKey(epk)
	if err != nil {
		return "", errors.New("malformed answer key")
	}
	secret, err := key.ECDH(peer)
	if err != nil {
		return "", err
	}
	k, err := hkdf.Key(sha256.New, secret, nil, answerInfo, 32)
	if err != nil {
		return "", err
	}
	block, _ := aes.NewCipher(k)
	gcm, _ := cipher.NewGCM(block)
	if len(nonce) != gcm.NonceSize() {
		return "", errors.New("malformed answer")
	}
	plain, err := gcm.Open(nil, nonce, data, nil)
	if err != nil {
		return "", errors.New("the answer could not be decrypted; sign in again")
	}
	return string(plain), nil
}

// SignOut ends the session (and a sign-in in progress).
func (m *Manager) SignOut(id string) {
	c, err := m.profile(id)
	if err != nil {
		return
	}
	m.mu.Lock()
	m.init()
	s := m.ses[id]
	delete(m.ses, id)
	m.mu.Unlock()
	if s != nil && s.cmd != nil && s.cmd.Process != nil {
		s.cmd.Process.Kill() //nolint:errcheck
	}
	exec.Command(m.ssh(), append([]string{"-O", "exit"}, m.baseArgs(c)...)...).Run() //nolint:errcheck
}

// alive asks the master whether it is still up.
func (m *Manager) alive(c Cluster) bool {
	if _, err := os.Stat(m.socket(c)); err != nil {
		return false
	}
	return exec.Command(m.ssh(), append([]string{"-O", "check"}, m.baseArgs(c)...)...).Run() == nil
}

// Touch records use by a person or the Agent; idle sessions sign out.
func (m *Manager) Touch(id string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if s := m.ses[id]; s != nil && s.state == "connected" {
		s.used = time.Now()
	}
}

// Watch signs out sessions that dropped or sat idle past their limit.
func (m *Manager) Watch(ctx context.Context) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			for _, st := range m.List() {
				if st.State == "connected" {
					m.SignOut(st.ID)
				}
			}
			return
		case <-t.C:
		}
		m.checkSessions(time.Now())
	}
}

// checkSessions always detects dead masters, even when idle sign-out is disabled.
func (m *Manager) checkSessions(now time.Time) {
	for _, st := range m.List() {
		if st.State != "connected" {
			continue
		}
		m.mu.Lock()
		active := m.ses[st.ID] != nil && m.ses[st.ID].active > 0
		m.mu.Unlock()
		if !st.KeepConnected && !active && now.Sub(st.LastUsed) > time.Duration(st.IdleMinutes)*time.Minute {
			m.SignOut(st.ID)
			m.note(st.ID, fmt.Sprintf("Signed out after %d minutes without use.", st.IdleMinutes))
		} else if !m.alive(st.Cluster) {
			m.SignOut(st.ID)
			m.note(st.ID, "The session ended (network change, sleep or the cluster closed it). Sign in again.")
		}
	}
}

func (m *Manager) note(id, msg string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.ses[id] = &session{state: "signed_out", err: msg, done: closed()}
}

// ErrSignedOut means the cluster needs a sign-in before it can be used.
var ErrSignedOut = errors.New("sign in to this cluster first")

// Run executes a command on the login node through the session. The argv is
// quoted for the remote shell; stdin may be nil.
func (m *Manager) Run(ctx context.Context, id string, stdin []byte, argv ...string) ([]byte, error) {
	return m.runBuffered(ctx, id, true, stdin, argv...)
}

// RunQuery is for passive monitoring: it never extends the idle login lease.
func (m *Manager) RunQuery(ctx context.Context, id string, stdin []byte, argv ...string) ([]byte, error) {
	return m.runBuffered(ctx, id, false, stdin, argv...)
}
func (m *Manager) runBuffered(ctx context.Context, id string, activity bool, stdin []byte, argv ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	var out, stderr limitedOutput
	err := m.Stream(ctx, id, activity, bytes.NewReader(stdin), &out, &stderr, argv...)
	if errors.Is(err, ErrSignedOut) {
		return nil, ErrSignedOut
	}
	if err != nil {
		return out.Bytes(), fmt.Errorf("SSH command: %w: %s", err, commandDiagnostic(stderr.String()))
	}
	if out.truncated {
		return nil, errors.New("SSH query output exceeded 1 MiB")
	}
	return out.Bytes(), nil
}

type limitedOutput struct {
	bytes.Buffer
	truncated bool
}

func (b *limitedOutput) Write(p []byte) (int, error) {
	n := len(p)
	remaining := 1024*1024 - b.Len()
	if len(p) > remaining {
		p = p[:remaining]
		b.truncated = true
	}
	b.Buffer.Write(p)
	return n, nil
}

// Stream has caller-owned deadlines and bounded/streamed consumers. It reuses
// ONLY the authenticated master: ProxyCommand=false prevents SSH from silently
// opening a fresh authenticated transport when the control socket disappears.
func (m *Manager) Stream(ctx context.Context, id string, activity bool, stdin io.Reader, stdout, stderr io.Writer, argv ...string) error {
	if len(argv) == 0 {
		return errors.New("missing SSH command")
	}
	c, err := m.profile(id)
	if err != nil {
		return err
	}
	if st, _ := m.Status(id); st.State != "connected" || !m.alive(c) {
		return ErrSignedOut
	}
	if activity {
		m.mu.Lock()
		session := m.ses[id]
		session.active++
		session.used = time.Now()
		m.mu.Unlock()
		defer func() { m.mu.Lock(); session.active--; session.used = time.Now(); m.mu.Unlock() }()
	}
	quoted := make([]string, len(argv))
	for i, a := range argv {
		quoted[i] = shellQuote(a)
	}
	args := append([]string{"-o", "ControlMaster=no", "-o", "BatchMode=yes", "-o", "ProxyCommand=false", "-T"}, m.baseArgs(c)...)
	args = append(args, "--", strings.Join(quoted, " "))
	cmd := exec.CommandContext(ctx, m.ssh(), args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = stdin, stdout, stderr
	cmd.WaitDelay = 2 * time.Second
	return cmd.Run()
}

func shellQuote(s string) string {
	if s != "" && regexp.MustCompile(`^[A-Za-z0-9_./=:,@%+-]+$`).MatchString(s) {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

func lastLines(s string, n int) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.TrimSpace(strings.Join(lines, "\n"))
}

// Scheduler rejections often put the actionable reason before a footer. Keep
// both ends of bounded command diagnostics rather than just the final lines.
// This is only used after authentication, never for password/MFA transcripts.
func commandDiagnostic(s string) string {
	runes := []rune(strings.TrimSpace(s))
	const limit = 8192
	if len(runes) <= limit {
		return string(runes)
	}
	return string(runes[:limit/2]) + "\n… command output truncated …\n" + string(runes[len(runes)-limit/2:])
}

// slug derives a cluster id from its name, else from the host's first
// meaningful label (login.sherlock.stanford.edu -> sherlock).
func slug(name, host string) string {
	clean := func(s string) string {
		return strings.Trim(regexp.MustCompile(`[^a-z0-9]+`).ReplaceAllString(strings.ToLower(s), "-"), "-")
	}
	if s := clean(name); idRE.MatchString(s) {
		return s
	}
	for _, label := range strings.Split(host, ".") {
		if s := clean(label); idRE.MatchString(s) && s != "login" && s != "ssh" {
			return s
		}
	}
	return "cluster"
}
