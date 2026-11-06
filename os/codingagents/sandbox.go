package codingagents

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

// A shared Team Chat agent's turns run in an OS sandbox around the whole
// runtime process tree (docs/apps/team-chat.md, "Shared agents run
// sandboxed"). The runtimes' own sandboxes are not enough: Claude Code's
// file tools are outside its Bash sandbox, and Codex sandboxes only the
// commands it runs. A sandboxed turn reads and writes its own folder (the
// folder's .team only through its inbox), a home of its own (the runtime's
// config home, sessions and temp files, with none of the user's sign-ins),
// and reads what the runtime needs to run (the system, developer tools,
// Homebrew, the runtime's own install). It reaches the network, but on
// this computer's loopback only the node's model gateway. It cannot signal
// or inspect processes outside its turn, and cannot reach the Keychain.

// SandboxRefusal starts every error that refuses a turn because it cannot
// run in its sandbox; Team Chat shows the rest of the text as it is.
const SandboxRefusal = "Team Chat sandbox: "

// SandboxRuntimes are the runtimes a sandboxed turn can run: the others
// (Gemini, Cursor, OpenCode, Pi, Pantheon Agent) are refused, not run
// unsandboxed.
var SandboxRuntimes = map[string]bool{"claude": true, "codex": true}

// ErrNoSandbox: this computer cannot run a sandbox.
var ErrNoSandbox = errors.New("This computer can't run shared agents in a sandbox. The owner can turn the sandbox off in the agent's settings")

// Sandbox is one sandboxed turn.
type Sandbox struct {
	// Folder is the agent's folder (read and write); TeamDir its .team
	// (read only) and Inbox the turn's inbox there (read and write).
	Folder, TeamDir, Inbox string
	// Dir holds the agent's own home and temp folder (read and write).
	Dir string
	// Program is the runtime's executable; Reads are the folders of its
	// install (read only).
	Program string
	Reads   []string
	// Gateway is the node's model gateway port on 127.0.0.1: the only
	// loopback port the turn may connect to (0: none).
	Gateway int
}

// SandboxDir is where a Team Chat agent's sandbox home lives on this node.
func SandboxDir(mid string) (string, error) {
	if !regexp.MustCompile(`^[a-z0-9]{1,40}$`).MatchString(mid) {
		return "", fmt.Errorf("%sthe agent's id %q cannot name a folder", SandboxRefusal, mid)
	}
	return filepath.Join(Root(), "team-sandbox", mid), nil
}

// Home is the turn's HOME (the runtime's config home is under it).
func (s Sandbox) Home() string { return filepath.Join(s.Dir, "home") }

// Tmp is the turn's temp folder.
func (s Sandbox) Tmp() string { return filepath.Join(s.Dir, "tmp") }

// Prepare makes the sandbox's own folders and resolves every path to the
// one the system checks (symlinks followed: /tmp is /private/tmp).
func (s *Sandbox) Prepare() error {
	for _, d := range []string{s.Dir, s.Home(), s.Tmp(), filepath.Join(s.Home(), ".claude"), filepath.Join(s.Home(), ".codex")} {
		if err := os.MkdirAll(d, 0o700); err != nil {
			return fmt.Errorf("%scannot make the agent's sandbox home: %v", SandboxRefusal, err)
		}
	}
	real := func(p string) (string, error) {
		if p == "" {
			return "", nil
		}
		r, err := filepath.EvalSymlinks(p)
		if err != nil {
			return "", err
		}
		return filepath.Abs(r)
	}
	var err error
	for _, p := range []*string{&s.Folder, &s.TeamDir, &s.Inbox, &s.Dir, &s.Program} {
		if *p, err = real(*p); err != nil {
			return fmt.Errorf("%s%v", SandboxRefusal, err)
		}
	}
	var reads []string
	for _, p := range s.Reads {
		if r, err := real(p); err == nil && r != "" {
			reads = append(reads, r)
		}
	}
	s.Reads = reads
	for _, p := range append([]string{s.Folder, s.TeamDir, s.Inbox, s.Dir, s.Program}, s.Reads...) {
		if p != "" && !profilePath(p) {
			return fmt.Errorf("%sthe path %q cannot go in a sandbox profile", SandboxRefusal, p)
		}
	}
	if s.Folder == "" || s.Dir == "" || s.Program == "" {
		return fmt.Errorf("%sthe turn's folder, home or runtime is missing", SandboxRefusal)
	}
	if s.Folder == "/" || underDir(s.Dir, s.Folder) {
		return fmt.Errorf("%sthe agent's folder %s holds its sandbox home", SandboxRefusal, s.Folder)
	}
	return nil
}

// underDir: p is dir or inside it.
func underDir(p, dir string) bool {
	return p == dir || strings.HasPrefix(p, strings.TrimRight(dir, "/")+"/")
}

// profilePath: a path that can be written into a profile as it is.
func profilePath(p string) bool {
	return filepath.IsAbs(p) && !strings.ContainsAny(p, "\"\\\x00\n\r") && filepath.Clean(p) == p
}

// Env is the turn's environment in its sandbox: its own home, the
// runtimes' config homes and temp folders in it (never the user's: they
// hold the user's own sign-ins). zsh writes here-documents under
// TMPPREFIX (/tmp/zsh by default), not TMPDIR: pantheon-team's
// `- <<'EOF'` needs it inside the sandbox.
func (s Sandbox) Env(env []string) []string {
	home, tmp := s.Home(), s.Tmp()
	return mergeEnv(env, "HOME="+home, "CLAUDE_CONFIG_DIR="+filepath.Join(home, ".claude"),
		"CODEX_HOME="+filepath.Join(home, ".codex"), "TMPDIR="+tmp+"/", "TMP="+tmp, "TEMP="+tmp,
		"CLAUDE_CODE_TMPDIR="+tmp, "TMPPREFIX="+filepath.Join(tmp, "zsh"), "XDG_CONFIG_HOME="+filepath.Join(home, ".config"),
		"XDG_CACHE_HOME="+filepath.Join(home, ".cache"), "XDG_DATA_HOME="+filepath.Join(home, ".local", "share"))
}

// GatewayPort is the port of a gateway base URL on 127.0.0.1 (0 when it is
// not one).
func GatewayPort(base string) int {
	u, err := url.Parse(base)
	if err != nil || u.Hostname() != "127.0.0.1" {
		return 0
	}
	port, _ := strconv.Atoi(u.Port())
	return port
}

// InstallReads are the folders a runtime's install needs to be read: its
// executable's folder, the package around a bin/ folder, the global
// node_modules it lives in, and the interpreter a script names (#!).
func InstallReads(program string, path []string) []string {
	var out []string
	home, _ := os.UserHomeDir()
	if h, err := filepath.EvalSymlinks(home); err == nil {
		home = h
	}
	keep := func(p string) {
		// Never / or a folder of one name (/bin is the system's anyway),
		// never the home folder or a folder that holds it.
		if strings.Count(p, "/") < 2 || (home != "" && underDir(home, p)) {
			return
		}
		out = append(out, p)
	}
	add := func(p string) {
		if p == "" {
			return
		}
		real, err := filepath.EvalSymlinks(p)
		if err != nil {
			return
		}
		dir := filepath.Dir(real)
		keep(real)
		keep(dir)
		if filepath.Base(dir) == "bin" {
			keep(filepath.Dir(dir))
		}
		if i := strings.Index(real, "/node_modules/"); i >= 0 {
			keep(real[:i+len("/node_modules")])
		}
	}
	add(program)
	if real, err := filepath.EvalSymlinks(program); err == nil {
		if f, err := os.Open(real); err == nil {
			head := make([]byte, 256)
			n, _ := f.Read(head)
			f.Close()
			if line, ok := strings.CutPrefix(string(head[:n]), "#!"); ok {
				line, _, _ = strings.Cut(line, "\n")
				fields := strings.Fields(line)
				if len(fields) > 0 {
					interp := fields[0]
					if filepath.Base(interp) == "env" && len(fields) > 1 {
						for _, dir := range path {
							if fi, err := os.Stat(filepath.Join(dir, fields[1])); err == nil && !fi.IsDir() {
								interp = filepath.Join(dir, fields[1])
								break
							}
						}
					}
					add(interp)
				}
			}
		}
	}
	slices.Sort(out)
	return slices.Compact(out)
}

// sbplString quotes a path for a profile (profilePath checked it).
func sbplString(p string) string { return `"` + p + `"` }

// ancestors are the folders above p, / excluded.
func ancestors(p string) []string {
	var out []string
	for d := filepath.Dir(p); d != "/" && d != "."; d = filepath.Dir(d) {
		out = append(out, d)
	}
	return out
}

// sandboxSystemReads are the system folders a sandboxed turn may read on
// macOS: the system, developer tools, Homebrew and /usr/local (programs,
// not their data), and the system's settings in /private/etc.
var sandboxSystemReads = []string{"/usr", "/bin", "/sbin", "/System", "/Library", "/private/etc", "/opt/homebrew",
	"/private/var/select", "/Applications/Xcode.app"}

// sandboxDenied are folders inside those it may not read: the system
// keychains, and the data folders Homebrew and /usr/local programs keep
// (databases, caches).
var sandboxDenied = []string{"/Library/Keychains", "/opt/homebrew/var", "/usr/local/var", "/Library/Application Support/com.apple.TCC"}

// SeatbeltProfile is the macOS sandbox profile (sandbox-exec) of a turn.
func (s Sandbox) SeatbeltProfile() string {
	var b strings.Builder
	w := func(format string, args ...any) { fmt.Fprintf(&b, format+"\n", args...) }
	list := func(kind string, paths []string) string {
		var parts []string
		for _, p := range paths {
			parts = append(parts, "("+kind+" "+sbplString(p)+")")
		}
		return strings.Join(parts, " ")
	}
	reads := append(slices.Clone(sandboxSystemReads), s.Reads...)
	own := []string{s.Folder, s.Dir}
	w(";; A shared Team Chat agent's turn (PantheonOS): docs/apps/team-chat.md, \"Shared agents run sandboxed\".")
	w("(version 1)")
	w("(deny default)")
	w(`(import "system.sb")`)
	w("(allow process-fork)")
	// Programs run only from what it may read.
	w("(allow process-exec %s %s)", list("subpath", reads), list("subpath", own))
	w("(allow file-read* file-map-executable %s)", list("subpath", reads))
	w("(deny file-read* file-write* process-exec %s)", list("subpath", sandboxDenied))
	// Its own folder and home: read and write.
	w("(allow file-read* file-write* file-map-executable %s)", list("subpath", own))
	if s.TeamDir != "" {
		// .team is the node's: the turn only writes requests in its inbox.
		w("(deny file-write* %s)", list("subpath", []string{s.TeamDir}))
		if s.Inbox != "" {
			w("(allow file-write* %s)", list("subpath", []string{s.Inbox}))
		}
	}
	// Where those are: the folders above them, by name only (no listing).
	var above []string
	for _, p := range append(slices.Clone(reads), own...) {
		above = append(above, ancestors(p)...)
	}
	slices.Sort(above)
	above = slices.Compact(above)
	w("(allow file-read-metadata %s %s)", list("literal", above), list("literal", []string{"/tmp", "/var", "/etc", "/private/tmp", "/private/var"}))
	w(`(allow file-read* (literal "/private/var/run/resolv.conf"))`)
	w(`(allow file-read* file-write-data file-ioctl (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty") (literal "/dev/dtracehelper") (subpath "/dev/fd"))`)
	w(`(allow file-read* (literal "/dev/random") (literal "/dev/urandom"))`)
	// Its own processes only: no signal, inspection or debugging of others.
	w("(allow signal (target same-sandbox))")
	w("(allow process-info* (target same-sandbox))")
	// The network, but on this computer's loopback only the model gateway.
	w(`(allow network-outbound (remote ip "*:*"))`)
	w(`(deny network-outbound (remote ip "localhost:*"))`)
	if s.Gateway > 0 {
		w(`(allow network-outbound (remote ip "localhost:%d"))`, s.Gateway)
	}
	w(`(allow network-outbound (literal "/private/var/run/mDNSResponder"))`)
	w(`(allow network-bind network-inbound (local ip "localhost:*"))`)
	// Name resolution and proxy settings; nothing else of the system's
	// services (no Keychain, pasteboard, Apple Events or launchd jobs).
	w(`(allow mach-lookup (global-name "com.apple.SystemConfiguration.configd") (global-name "com.apple.SystemConfiguration.DNSConfiguration") (global-name "com.apple.dnssd.service"))`)
	w(`(deny mach-lookup (global-name "com.apple.SecurityServer") (global-name "com.apple.securityd.xpc") (global-name "com.apple.security.keychain-db") (global-name "com.apple.secd") (global-name "com.apple.pasteboard.1") (global-name "com.apple.coreservices.appleevents") (global-name "com.apple.coreservices.launchservicesd"))`)
	w(`(allow ipc-posix-shm-read-data (ipc-posix-name "apple.shm.notification_center"))`)
	return b.String()
}

// SandboxKind is the sandbox this computer runs shared agents' turns in
// ("seatbelt" on macOS, "bubblewrap" on Linux) or "" and why there is none;
// a variable so tests can take it away.
var SandboxKind = sandboxKind
