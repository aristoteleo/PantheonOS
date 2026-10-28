package codingagents

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/childenv"
)

// Found is one agent detected on this machine.
type Found struct {
	ID       string `json:"id"`
	Label    string `json:"label"`
	Path     string `json:"path"`
	Terminal bool   `json:"terminal"`
	Headless bool   `json:"conversation"`
}

const (
	detectTTL       = 5 * time.Minute
	shellProbeLimit = 10 * time.Second
	pathMarker      = "__PANTHEON_PATH__"
)

var (
	cacheMu   sync.Mutex
	cached    []Found
	cachedAt  time.Time
	pathMu    sync.Mutex
	shellPath []string
	shellAt   time.Time
)

// Detect returns the agents installed here. Results are cached for five
// minutes; force detects again at once.
func Detect(force bool) []Found {
	cacheMu.Lock()
	defer cacheMu.Unlock()
	if !force && cachedAt.After(time.Now().Add(-detectTTL)) {
		return slices.Clone(cached)
	}
	cached = detectIn(SearchDirs(force), runtime.GOOS)
	cachedAt = time.Now()
	return slices.Clone(cached)
}

// Cached is the last detection result without probing; nil before the first.
func Cached() ([]Found, bool) {
	cacheMu.Lock()
	defer cacheMu.Unlock()
	return slices.Clone(cached), !cachedAt.IsZero() && cachedAt.After(time.Now().Add(-detectTTL))
}

func detectIn(dirs []string, goos string) []Found {
	var out []Found
	for _, a := range Catalog {
		path := findCommand(a.Commands, dirs, goos)
		if path == "" {
			continue
		}
		out = append(out, Found{ID: a.ID, Label: a.Label, Path: path,
			Terminal: goos != "windows", Headless: a.Headless != nil})
	}
	return out
}

// Path finds one agent's executable, detecting again when it is not cached.
func Path(id string) string {
	for _, f := range Detect(false) {
		if f.ID == id {
			return f.Path
		}
	}
	for _, f := range Detect(true) {
		if f.ID == id {
			return f.Path
		}
	}
	return ""
}

func findCommand(names, dirs []string, goos string) string {
	exts := []string{""}
	if goos == "windows" {
		exts = []string{".exe", ".cmd", ".bat", ""}
	}
	for _, name := range names {
		for _, dir := range dirs {
			for _, ext := range exts {
				p := filepath.Join(dir, name+ext)
				if executable(p, goos) {
					return p
				}
			}
		}
	}
	return ""
}

func executable(p, goos string) bool {
	st, err := os.Stat(p)
	if err != nil || !st.Mode().IsRegular() {
		return false
	}
	return goos == "windows" || st.Mode().Perm()&0o111 != 0
}

// SearchDirs is the login shell's PATH, then this process's PATH, then the
// directories version managers and package managers install into.
func SearchDirs(force bool) []string {
	home, _ := os.UserHomeDir()
	var dirs []string
	dirs = append(dirs, LoginPath(force)...)
	dirs = append(dirs, filepath.SplitList(os.Getenv("PATH"))...)
	dirs = append(dirs, installDirs(home, runtime.GOOS)...)
	return dedupe(dirs)
}

func dedupe(dirs []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, d := range dirs {
		if d == "" || seen[d] {
			continue
		}
		seen[d] = true
		out = append(out, d)
	}
	return out
}

// LoginPath is PATH as the user's login shell sets it, which is what a
// terminal on this machine sees. A node started by launchd, systemd or a
// container entrypoint has a much shorter PATH of its own.
func LoginPath(force bool) []string {
	if runtime.GOOS == "windows" {
		return nil
	}
	pathMu.Lock()
	defer pathMu.Unlock()
	if !force && shellAt.After(time.Now().Add(-detectTTL)) {
		return shellPath
	}
	shellPath = probeShellPath(UserShell())
	shellAt = time.Now()
	return shellPath
}

// UserShell is the user's shell, or the first common one present.
func UserShell() string {
	if s := os.Getenv("SHELL"); s != "" && filepath.IsAbs(s) {
		if _, err := os.Stat(s); err == nil {
			return s
		}
	}
	for _, c := range []string{"/bin/zsh", "/bin/bash", "/usr/bin/bash", "/bin/sh"} {
		if _, err := os.Stat(c); err == nil {
			return c
		}
	}
	return "/bin/sh"
}

func probeShellPath(shell string) []string {
	ctx, cancel := context.WithTimeout(context.Background(), shellProbeLimit)
	defer cancel()
	flag := "-ilc"
	if base := filepath.Base(shell); base == "sh" || base == "dash" {
		flag = "-lc"
	}
	script := "printf '%s' '" + pathMarker + "'; printf '%s' \"$PATH\"; printf '%s' '" + pathMarker + "'"
	cmd := exec.CommandContext(ctx, shell, flag, script)
	cmd.Env = append(childenv.Workspace(), "PANTHEON_SHELL_PATH_PROBE=1")
	// An interactive shell attached to the node's terminal would take it
	// over (and stop on SIGTTOU); give it no controlling terminal.
	detach(cmd)
	out, _ := cmd.Output()
	return parseMarkedPath(out)
}

func parseMarkedPath(out []byte) []string {
	m := []byte(pathMarker)
	first := bytes.Index(out, m)
	if first < 0 {
		return nil
	}
	rest := out[first+len(m):]
	end := bytes.Index(rest, m)
	if end < 0 {
		return nil
	}
	var dirs []string
	for _, d := range filepath.SplitList(string(rest[:end])) {
		if filepath.IsAbs(d) {
			dirs = append(dirs, d)
		}
	}
	return dirs
}

func installDirs(home, goos string) []string {
	if goos == "windows" {
		return []string{
			filepath.Join(home, ".local", "bin"),
			filepath.Join(home, "AppData", "Roaming", "npm"),
			filepath.Join(home, "AppData", "Local", "pnpm"),
			filepath.Join(home, ".bun", "bin"),
		}
	}
	var dirs []string
	dirs = append(dirs, nvmDirs(home)...)
	dirs = append(dirs,
		filepath.Join(home, ".volta", "bin"),
		filepath.Join(home, ".asdf", "shims"),
		filepath.Join(home, ".local", "share", "fnm", "aliases", "default", "bin"),
		filepath.Join(home, ".local", "share", "mise", "shims"),
		filepath.Join(home, ".local", "bin"),
		filepath.Join(home, ".npm-global", "bin"),
		filepath.Join(home, ".claude", "local"),
		filepath.Join(home, ".yarn", "bin"),
		filepath.Join(home, ".bun", "bin"),
		filepath.Join(home, "bin"),
	)
	if goos == "darwin" {
		dirs = append(dirs, filepath.Join(home, "Library", "pnpm"), "/opt/homebrew/bin")
	} else {
		dirs = append(dirs, filepath.Join(home, ".local", "share", "pnpm"),
			"/home/linuxbrew/.linuxbrew/bin", "/snap/bin")
	}
	return append(dirs, "/usr/local/bin", "/nix/var/nix/profiles/default/bin",
		filepath.Join(home, ".nix-profile", "bin"), "/opt/local/bin")
}

// nvmDirs lists nvm's Node installs, the default alias first.
func nvmDirs(home string) []string {
	root := filepath.Join(home, ".nvm", "versions", "node")
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	var versions []string
	for _, e := range entries {
		if e.IsDir() {
			versions = append(versions, e.Name())
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(versions)))
	if b, err := os.ReadFile(filepath.Join(home, ".nvm", "alias", "default")); err == nil {
		want := strings.TrimPrefix(strings.TrimSpace(string(b)), "v")
		for i, v := range versions {
			if strings.HasPrefix(strings.TrimPrefix(v, "v"), want) {
				versions[0], versions[i] = versions[i], versions[0]
				break
			}
		}
	}
	dirs := make([]string, len(versions))
	for i, v := range versions {
		dirs[i] = filepath.Join(root, v, "bin")
	}
	return dirs
}
