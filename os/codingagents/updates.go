package codingagents

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Updates: where a detected agent was installed from (its resolved path
// says), the command that updates it there, and the newest version its own
// source publishes. See docs/apps/coding-agents.md, "Installing and
// updating".

// Origin is how an agent's command was installed, read from its resolved path.
type Origin struct {
	// Via is "npm", "bun", "brew" or "brew-cask"; empty for the vendor's own
	// installer (or one PantheonOS cannot tell), which its updater handles.
	Via string
	// Package is the npm package, or the Homebrew formula or cask.
	Package string
	// Prefix is npm's global prefix that holds it (<prefix>/lib/node_modules),
	// Bun's folder (~/.bun), or Homebrew's prefix.
	Prefix string
}

var (
	cellarRe   = regexp.MustCompile(`^(.*)/Cellar/([^/]+)/`)
	caskroomRe = regexp.MustCompile(`^(.*)/Caskroom/([^/]+)/`)
)

// OriginOf reads where the command at path (resolved through its links) was
// installed from.
func OriginOf(path string) Origin {
	real, err := filepath.EvalSymlinks(path)
	if err != nil {
		real = path
	}
	return originOfResolved(filepath.ToSlash(real))
}

func originOfResolved(real string) Origin {
	if i := strings.Index(real, "/install/global/node_modules/"); i >= 0 && strings.Contains(real[:i], "/.bun") {
		return Origin{Via: "bun", Package: npmPackageAt(real[i+len("/install/global/node_modules/"):]), Prefix: real[:i]}
	}
	if i := strings.Index(real, "/lib/node_modules/"); i >= 0 {
		if pkg := npmPackageAt(real[i+len("/lib/node_modules/"):]); pkg != "" {
			return Origin{Via: "npm", Package: pkg, Prefix: real[:i]}
		}
	}
	if m := cellarRe.FindStringSubmatch(real); m != nil {
		return Origin{Via: "brew", Package: m[2], Prefix: m[1]}
	}
	if m := caskroomRe.FindStringSubmatch(real); m != nil {
		return Origin{Via: "brew-cask", Package: m[2], Prefix: m[1]}
	}
	return Origin{}
}

// npmPackageAt is the package name at the start of a path inside node_modules.
func npmPackageAt(rest string) string {
	parts := strings.Split(rest, "/")
	if len(parts) == 0 || parts[0] == "" {
		return ""
	}
	if strings.HasPrefix(parts[0], "@") {
		if len(parts) < 2 || parts[1] == "" {
			return ""
		}
		return parts[0] + "/" + parts[1]
	}
	return parts[0]
}

// releaseSource is where an agent's own releases are announced, for an
// install whose package manager does not say (the vendor's own installer).
type releaseSource struct {
	// Package is the npm package its releases are published as, with the
	// same version numbers as its own installer's.
	Package string
	// Feed reads the newest version from the vendor's own release feed,
	// preferred over Package.
	Feed func(ctx context.Context) (string, error)
}

var releaseSources = map[string]releaseSource{
	// Its installer and `claude update` read this.
	"claude": {Package: "@anthropic-ai/claude-code", Feed: textFeed("https://downloads.claude.ai/claude-code-releases/latest")},
	"codex":  {Package: "@openai/codex"},
	"gemini": {Package: "@google/gemini-cli"},
	// Its installer script names the build it installs.
	"cursor":   {Feed: cursorFeed},
	"opencode": {Package: "opencode-ai"},
	// pi update reads this.
	"pi":      {Package: "@earendil-works/pi-coding-agent", Feed: jsonVersionFeed("https://pi.dev/api/latest-version")},
	"copilot": {Package: "@github/copilot"},
	"qwen":    {Package: "@qwen-code/qwen-code"},
}

// UpdatePlan is how a detected agent updates where it is installed.
type UpdatePlan struct {
	// Argv updates it in place; nil when PantheonOS knows no way.
	Argv []string
	// Latest is the newest version its source publishes, when known.
	Latest string
	// Newer: Latest is newer than the installed version.
	Newer bool
	// Note says why a newer version is not offered (it needs a newer
	// Node.js than the one that runs the agent).
	Note string
}

// latestWait is how long a detection waits for an agent's latest version
// the first time it asks; later ones answer from the cache at once.
const latestWait = 3 * time.Second

// PlanUpdate is how agent f, at version installed (its --version output),
// updates, and whether a newer version exists. It never waits more than
// latestWait (and then only the first time a source is asked).
func PlanUpdate(f Found, installed string) UpdatePlan {
	if f.Custom || f.Missing || f.ID == PantheonAgent {
		// The Pantheon Agent is the node's own command, updated with the node.
		return UpdatePlan{}
	}
	return planUpdate(f, installed, OriginOf(f.Path), latestWait)
}

func planUpdate(f Found, installed string, o Origin, wait time.Duration) UpdatePlan {
	var p UpdatePlan
	var rel release
	switch o.Via {
	case "npm":
		npm := filepath.Join(o.Prefix, "bin", "npm")
		if !executable(npm, runtime.GOOS) {
			npm = "npm"
		}
		p.Argv = []string{npm, "install", "-g", "--prefix", o.Prefix, o.Package + "@latest"}
		rel, _ = latestRelease("npm:"+o.Package, func(ctx context.Context) (release, error) { return npmLatest(ctx, o.Package) }, wait)
		if rel.Node != "" {
			if node := nodeVersion(o.Prefix); node != "" {
				if ok, known := satisfies(node, rel.Node); known && !ok {
					p.Note = fmt.Sprintf("%s %s needs Node.js %s; this computer has %s", f.Label, rel.Version, rel.Node, node)
					// @latest would install a version this Node.js cannot run.
					p.Argv = nil
				}
			}
		}
	case "bun":
		bun := filepath.Join(o.Prefix, "bin", "bun")
		if !executable(bun, runtime.GOOS) {
			bun = "bun"
		}
		p.Argv = []string{bun, "add", "-g", o.Package + "@latest"}
		rel, _ = latestRelease("npm:"+o.Package, func(ctx context.Context) (release, error) { return npmLatest(ctx, o.Package) }, wait)
	case "brew", "brew-cask":
		argv := []string{filepath.Join(o.Prefix, "bin", "brew"), "upgrade"}
		if o.Via == "brew-cask" {
			argv = append(argv, "--cask")
		}
		p.Argv = append(argv, o.Package)
		kind := strings.TrimPrefix(strings.TrimPrefix(o.Via, "brew"), "-")
		rel, _ = latestRelease(o.Via+":"+o.Package, func(ctx context.Context) (release, error) { return brewLatest(ctx, kind, o.Package) }, wait)
	default:
		// The vendor's installer: its own updater, and its own feed.
		if c, ok := Lookup(f.ID); ok && len(c.UpdateArgs) > 0 {
			p.Argv = append([]string{f.Path}, c.UpdateArgs...)
		}
		if src, ok := releaseSources[f.ID]; ok {
			switch {
			case src.Feed != nil:
				rel, _ = latestRelease("feed:"+f.ID, func(ctx context.Context) (release, error) {
					v, err := src.Feed(ctx)
					return release{Version: v}, err
				}, wait)
			case src.Package != "":
				rel, _ = latestRelease("npm:"+src.Package, func(ctx context.Context) (release, error) { return npmLatest(ctx, src.Package) }, wait)
			}
		}
	}
	p.Latest = rel.Version
	p.Newer = p.Argv != nil && newerVersion(rel.Version, installed)
	if p.Note != "" && !newerVersion(rel.Version, installed) {
		p.Note = ""
	}
	return p
}

// ---- versions ---------------------------------------------------------------

var versionRe = regexp.MustCompile(`\d+(?:\.\d+)+(?:-[0-9A-Za-z.-]+)?`)

// VersionNumber is the version in an agent's --version output ("2.1.3
// (Claude Code)", "codex-cli 0.5.0"), or "".
func VersionNumber(s string) string { return versionRe.FindString(s) }

// newerVersion: latest is a later version than installed (both as an agent
// prints them). Unknown either way is not newer.
func newerVersion(latest, installed string) bool {
	a, b := VersionNumber(latest), VersionNumber(installed)
	if a == "" || b == "" {
		return false
	}
	return compareVersions(a, b) > 0
}

// compareVersions orders two dotted versions numerically; a pre-release
// sorts before its release. Two different pre-release tags of the same
// numbers (Cursor's build hashes) count as the same version.
func compareVersions(a, b string) int {
	an, apre, _ := strings.Cut(a, "-")
	bn, bpre, _ := strings.Cut(b, "-")
	as, bs := strings.Split(an, "."), strings.Split(bn, ".")
	for i := 0; i < max(len(as), len(bs)); i++ {
		x, y := 0, 0
		if i < len(as) {
			x, _ = strconv.Atoi(as[i])
		}
		if i < len(bs) {
			y, _ = strconv.Atoi(bs[i])
		}
		if x != y {
			if x < y {
				return -1
			}
			return 1
		}
	}
	switch {
	case apre == "" && bpre != "":
		return 1
	case apre != "" && bpre == "":
		return -1
	}
	return 0
}

// satisfies says whether version v meets an npm engines range (">=22.19.0",
// "^20.17.0 || >=22.9.0"); known is false for a range it cannot read.
func satisfies(v, rng string) (ok, known bool) {
	v = strings.TrimPrefix(VersionNumber(v), "v")
	if v == "" {
		return false, false
	}
	v, _, _ = strings.Cut(v, "-")
	for _, alt := range strings.Split(rng, "||") {
		fields := strings.Fields(strings.ReplaceAll(strings.ReplaceAll(alt, ">= ", ">="), "< ", "<"))
		if len(fields) == 0 {
			return true, true
		}
		all := true
		for _, c := range fields {
			hit, ok := meets(v, c)
			if !ok {
				return false, false
			}
			all = all && hit
		}
		if all {
			return true, true
		}
	}
	return false, true
}

var comparatorRe = regexp.MustCompile(`^(>=|<=|>|<|=|\^|~)?v?(\d+)(?:\.(\d+|x|\*))?(?:\.(\d+|x|\*))?$`)

func meets(v, c string) (hit, ok bool) {
	if c == "*" || c == "x" {
		return true, true
	}
	m := comparatorRe.FindStringSubmatch(c)
	if m == nil {
		return false, false
	}
	parts := []string{m[2], m[3], m[4]}
	given := 1
	for i := 1; i < 3; i++ {
		if parts[i] != "" && parts[i] != "x" && parts[i] != "*" {
			given = i + 1
		} else {
			break
		}
	}
	nums := make([]string, 3)
	for i := range nums {
		nums[i] = "0"
		if i < given {
			nums[i] = parts[i]
		}
	}
	want := strings.Join(nums, ".")
	cmp := compareVersions(v, want)
	vParts := strings.Split(v, ".")
	switch m[1] {
	case ">=":
		return cmp >= 0, true
	case ">":
		return cmp > 0, true
	case "<=":
		return cmp <= 0, true
	case "<":
		return cmp < 0, true
	case "^":
		// Same major (or, for 0.x, same minor) and not lower.
		if nums[0] != "0" {
			return cmp >= 0 && vParts[0] == nums[0], true
		}
		return cmp >= 0 && vParts[0] == "0" && len(vParts) > 1 && vParts[1] == nums[1], true
	case "~":
		return cmp >= 0 && vParts[0] == nums[0] && (given < 2 || len(vParts) > 1 && vParts[1] == nums[1]), true
	default:
		// "=1.2.3", "1.2.3", "20" or "20.x": every number given matches.
		for i := 0; i < given; i++ {
			if i >= len(vParts) || vParts[i] != nums[i] {
				return false, true
			}
		}
		return true, true
	}
}

// ---- Node.js -----------------------------------------------------------------

var (
	nodeMu       sync.Mutex
	nodeVersions = map[versionKey]string{}
)

// nodeVersion is the Node.js an npm prefix's packages run on: its own node
// (nvm, ~/.local), else the one on the login PATH.
func nodeVersion(prefix string) string {
	node := filepath.Join(prefix, "bin", "node")
	if !executable(node, runtime.GOOS) {
		if node = Which("node"); node == "" {
			return ""
		}
	}
	st, err := os.Stat(node)
	if err != nil {
		return ""
	}
	key := versionKey{node, st.ModTime()}
	nodeMu.Lock()
	v, ok := nodeVersions[key]
	nodeMu.Unlock()
	if ok {
		return v
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, node, "--version")
	cmd.Env = AgentEnv("")
	out, err := cmd.Output()
	if v = strings.TrimSpace(string(out)); err != nil || v == "" {
		return ""
	}
	nodeMu.Lock()
	nodeVersions[key] = v
	nodeMu.Unlock()
	return v
}

// ---- latest versions -----------------------------------------------------------

// release is a source's newest version and, for an npm package, the Node.js
// range it needs.
type release struct {
	Version string
	Node    string
}

type latestEntry struct {
	rel      release
	okAt     time.Time // the last answer
	failedAt time.Time // the last failure since
	busy     chan struct{}
}

const (
	// latestTTL: a source is asked again after this long; latestRetry: after
	// a failure (offline), not sooner than this. Detection never waits on a
	// source that has answered before.
	latestTTL   = 6 * time.Hour
	latestRetry = 20 * time.Minute
)

var (
	latestMu    sync.Mutex
	latestCache = map[string]*latestEntry{}
	// latestSlots caps the lookups in flight at once.
	latestSlots = make(chan struct{}, 4)
	latestNow   = time.Now
	latestHTTP  = &http.Client{Timeout: 10 * time.Second}
)

// latestRelease returns what is known of key's newest release (possibly
// old) and starts asking its source again when due, waiting up to wait for
// the answer only when nothing is known yet. ok is false when nothing is.
// PANTHEON_NO_UPDATE_CHECK=1 turns lookups off.
func latestRelease(key string, fetch func(context.Context) (release, error), wait time.Duration) (release, bool) {
	if os.Getenv("PANTHEON_NO_UPDATE_CHECK") != "" {
		return release{}, false
	}
	latestMu.Lock()
	e := latestCache[key]
	if e == nil {
		e = &latestEntry{}
		latestCache[key] = e
	}
	now := latestNow()
	due := e.busy == nil && now.Sub(e.okAt) >= latestTTL && now.Sub(e.failedAt) >= latestRetry
	if due {
		e.busy = make(chan struct{})
		go refreshLatest(key, e, e.busy, fetch)
	}
	busy, known := e.busy, !e.okAt.IsZero()
	latestMu.Unlock()
	if !known && busy != nil && wait > 0 {
		select {
		case <-busy:
		case <-time.After(wait):
		}
	}
	latestMu.Lock()
	defer latestMu.Unlock()
	return e.rel, !e.okAt.IsZero()
}

func refreshLatest(key string, e *latestEntry, done chan struct{}, fetch func(context.Context) (release, error)) {
	latestSlots <- struct{}{}
	defer func() { <-latestSlots }()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	rel, err := fetch(ctx)
	latestMu.Lock()
	defer latestMu.Unlock()
	if err == nil && VersionNumber(rel.Version) == "" {
		err = fmt.Errorf("%s: no version in the answer", key)
	}
	if err != nil {
		// Offline, or the source is down: keep what was known.
		e.failedAt = latestNow()
	} else {
		e.rel, e.okAt, e.failedAt = rel, latestNow(), time.Time{}
	}
	e.busy = nil
	close(done)
}

// npmRegistry is npm's registry, or the one the node's environment names.
func npmRegistry() string {
	for _, name := range []string{"npm_config_registry", "NPM_CONFIG_REGISTRY"} {
		if r := os.Getenv(name); r != "" {
			return strings.TrimRight(r, "/")
		}
	}
	return "https://registry.npmjs.org"
}

func npmLatest(ctx context.Context, pkg string) (release, error) {
	body, err := get(ctx, npmRegistry()+"/"+url.PathEscape(pkg)+"/latest")
	if err != nil {
		return release{}, err
	}
	var m struct {
		Version string `json:"version"`
		Engines struct {
			Node string `json:"node"`
		} `json:"engines"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return release{}, err
	}
	return release{Version: m.Version, Node: m.Engines.Node}, nil
}

// brewAPI is Homebrew's formula and cask index.
var brewAPI = "https://formulae.brew.sh/api"

func brewLatest(ctx context.Context, kind, name string) (release, error) {
	if kind == "" {
		kind = "formula"
	}
	body, err := get(ctx, brewAPI+"/"+kind+"/"+url.PathEscape(name)+".json")
	if err != nil {
		return release{}, err
	}
	var m struct {
		Version  string `json:"version"`
		Versions struct {
			Stable string `json:"stable"`
		} `json:"versions"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return release{}, err
	}
	v := m.Versions.Stable
	if kind == "cask" {
		v, _, _ = strings.Cut(m.Version, ",")
	}
	return release{Version: v}, nil
}

func textFeed(u string) func(context.Context) (string, error) {
	return func(ctx context.Context) (string, error) {
		body, err := get(ctx, u)
		return strings.TrimSpace(string(body)), err
	}
}

func jsonVersionFeed(u string) func(context.Context) (string, error) {
	return func(ctx context.Context) (string, error) {
		body, err := get(ctx, u)
		if err != nil {
			return "", err
		}
		var m struct {
			Version string `json:"version"`
		}
		err = json.Unmarshal(body, &m)
		return m.Version, err
	}
}

var cursorBuildRe = regexp.MustCompile(`downloads\.cursor\.com/lab/([0-9][0-9A-Za-z.-]*)/`)

// cursorFeed reads the build Cursor's installer script installs.
func cursorFeed(ctx context.Context) (string, error) {
	body, err := get(ctx, "https://cursor.com/install")
	if err != nil {
		return "", err
	}
	m := cursorBuildRe.FindSubmatch(body)
	if m == nil {
		return "", errors.New("cursor.com/install names no build")
	}
	return string(m[1]), nil
}

func get(ctx context.Context, u string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "PantheonOS coding-agents update check")
	resp, err := latestHTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s: %s", u, resp.Status)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 1<<20))
}

// InstalledVersion is the --version output of the command at path, as
// Inspect reports it (cached until the binary changes).
func InstalledVersion(path string) string { return version(path) }
