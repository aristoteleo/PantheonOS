// Package selfupdate replaces the running Fleet node with a newer official
// release and restarts it.
//
// Trust: a request names only a release tag. Every URL is derived here from
// the official PantheonOS release location, and each file is checked against
// that same release's SHA256SUMS, so whoever can send the request can at most
// move this node to another genuine, newer Fleet release. Older or equal
// versions are refused, and the node waits until it is idle.
package selfupdate

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
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

// ReleaseBase is where official Fleet releases are published.
var ReleaseBase = "https://github.com/aristoteleo/PantheonOS/releases/download/"

var tagRE = regexp.MustCompile(`^fleet-v(\d+)\.(\d+)\.(\d+)(?:-([a-z]+)\.(\d+))?$`)
var versionRE = regexp.MustCompile(`^(\d+)\.(\d+)\.(\d+)(?:-([a-z]+)\.(\d+))?$`)

const maxAssetBytes = 400 << 20

// Version is a Fleet release version such as 0.5.0-model.6.
type Version struct {
	nums [3]int
	pre  string
	preN int
}

func parse(m []string) Version {
	var v Version
	for i := 0; i < 3; i++ {
		v.nums[i], _ = strconv.Atoi(m[i+1])
	}
	v.pre = m[4]
	v.preN, _ = strconv.Atoi(m[5])
	return v
}

// ParseTag reads a release tag (fleet-v0.5.0-model.6).
func ParseTag(tag string) (Version, error) {
	m := tagRE.FindStringSubmatch(tag)
	if m == nil {
		return Version{}, fmt.Errorf("not a Fleet release tag: %q", tag)
	}
	return parse(m), nil
}

// ParseVersion reads a runner version (0.5.0-model.6).
func ParseVersion(s string) (Version, error) {
	m := versionRE.FindStringSubmatch(s)
	if m == nil {
		return Version{}, fmt.Errorf("unrecognised Fleet version %q", s)
	}
	return parse(m), nil
}

// Newer reports whether a is a later release than b. A release without a
// pre-release suffix is later than any pre-release of the same numbers.
func Newer(a, b Version) bool {
	for i := 0; i < 3; i++ {
		if a.nums[i] != b.nums[i] {
			return a.nums[i] > b.nums[i]
		}
	}
	switch {
	case a.pre == b.pre:
		return a.preN > b.preN
	case a.pre == "":
		return true
	case b.pre == "":
		return false
	default:
		return a.pre > b.pre
	}
}

// Target is what an update replaces on this machine.
type Target struct {
	Executable string   // the running binary
	Bundle     string   // the macOS .app holding it, when launched from one
	Asset      string   // release file for this platform
	Companions []string // sibling helper binaries updated with it (Windows capture helper)
}

// Locate finds the running binary and the release asset that replaces it.
func Locate() (Target, error) {
	exe, err := os.Executable()
	if err != nil {
		return Target{}, err
	}
	if resolved, err := filepath.EvalSymlinks(exe); err == nil {
		exe = resolved
	}
	t := Target{Executable: exe}
	arch := runtime.GOARCH
	switch runtime.GOOS {
	case "darwin":
		if i := strings.Index(exe, ".app/Contents/MacOS/"); i >= 0 {
			t.Bundle = exe[:i+len(".app")]
			t.Asset = "Fleet-" + arch + ".app.zip"
		} else {
			t.Asset = "fleet-darwin-" + arch
		}
	case "windows":
		t.Asset = "fleet-windows-" + arch + ".exe"
		capture := "fleet-native-capture-windows-" + arch + ".exe"
		if _, err := os.Stat(filepath.Join(filepath.Dir(exe), capture)); err == nil {
			t.Companions = []string{capture}
		}
	case "linux":
		t.Asset = "fleet-linux-" + arch
	default:
		return Target{}, fmt.Errorf("self-update is not available on %s", runtime.GOOS)
	}
	return t, nil
}

// Result describes one update request.
type Result struct {
	Status string `json:"status"` // up_to_date | deferred | updated
	From   string `json:"from"`
	To     string `json:"to"`
	Reason string `json:"reason,omitempty"`
}

// Updater applies one update at a time.
type Updater struct {
	Current string                 // the running version
	Busy    func() string          // why the node cannot restart now ("" when idle)
	Client  *http.Client           // defaults to a client with a generous timeout
	Locate  func() (Target, error) // defaults to Locate
	mu      sync.Mutex
}

// Apply installs tag when it is newer than the running version and the node
// is idle. On "updated" the caller restarts the process (Restart).
func (u *Updater) Apply(ctx context.Context, tag string) (Result, string, error) {
	if !u.mu.TryLock() {
		return Result{Status: "deferred", From: u.Current, To: tag, Reason: "an update is already in progress"}, "", nil
	}
	defer u.mu.Unlock()
	want, err := ParseTag(tag)
	if err != nil {
		return Result{}, "", err
	}
	have, err := ParseVersion(u.Current)
	if err != nil {
		return Result{}, "", err
	}
	to := strings.TrimPrefix(tag, "fleet-v")
	if !Newer(want, have) {
		return Result{Status: "up_to_date", From: u.Current, To: to}, "", nil
	}
	if u.Busy != nil {
		if reason := u.Busy(); reason != "" {
			return Result{Status: "deferred", From: u.Current, To: to, Reason: reason}, "", nil
		}
	}
	locate := u.Locate
	if locate == nil {
		locate = Locate
	}
	target, err := locate()
	if err != nil {
		return Result{}, "", err
	}
	client := u.Client
	if client == nil {
		client = &http.Client{Timeout: 15 * time.Minute}
	}
	sums, err := fetchSums(ctx, client, ReleaseBase+tag+"/SHA256SUMS")
	if err != nil {
		return Result{}, "", err
	}
	if target.Bundle != "" {
		err = u.installBundle(ctx, client, tag, target, sums)
	} else {
		err = installFile(ctx, client, tag, target.Asset, target.Executable, sums)
		for _, companion := range target.Companions {
			if err != nil {
				break
			}
			err = installFile(ctx, client, tag, companion, filepath.Join(filepath.Dir(target.Executable), companion), sums)
		}
	}
	if err != nil {
		return Result{}, "", err
	}
	return Result{Status: "updated", From: u.Current, To: to}, target.Executable, nil
}

func fetchSums(ctx context.Context, client *http.Client, url string) (map[string]string, error) {
	body, err := get(ctx, client, url, 64<<10)
	if err != nil {
		return nil, fmt.Errorf("release checksums: %w", err)
	}
	defer body.Close()
	sums := map[string]string{}
	scanner := bufio.NewScanner(body)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) == 2 && len(fields[0]) == 64 {
			sums[strings.TrimPrefix(fields[1], "*")] = strings.ToLower(fields[0])
		}
	}
	return sums, scanner.Err()
}

func get(ctx context.Context, client *http.Client, url string, limit int64) (io.ReadCloser, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		return nil, fmt.Errorf("GET %s: %s", url, resp.Status)
	}
	return struct {
		io.Reader
		io.Closer
	}{io.LimitReader(resp.Body, limit+1), resp.Body}, nil
}

// download writes asset next to dest (same filesystem) and verifies its sha256.
func download(ctx context.Context, client *http.Client, tag, asset, dir string, sums map[string]string) (string, error) {
	want := sums[asset]
	if want == "" {
		return "", fmt.Errorf("release %s has no checksum for %s", tag, asset)
	}
	body, err := get(ctx, client, ReleaseBase+tag+"/"+asset, maxAssetBytes)
	if err != nil {
		return "", err
	}
	defer body.Close()
	out, err := os.CreateTemp(dir, ".fleet-update-*")
	if err != nil {
		return "", err
	}
	hash := sha256.New()
	n, err := io.Copy(io.MultiWriter(out, hash), body)
	if cerr := out.Close(); err == nil {
		err = cerr
	}
	if err == nil && n > maxAssetBytes {
		err = errors.New("release file is too large")
	}
	if err == nil && hex.EncodeToString(hash.Sum(nil)) != want {
		err = fmt.Errorf("checksum mismatch for %s", asset)
	}
	if err != nil {
		os.Remove(out.Name())
		return "", err
	}
	return out.Name(), nil
}

// installFile replaces a binary. The running one moves to <name>.previous
// first: Windows cannot overwrite a running executable but can rename it.
func installFile(ctx context.Context, client *http.Client, tag, asset, dest string, sums map[string]string) error {
	tmp, err := download(ctx, client, tag, asset, filepath.Dir(dest), sums)
	if err != nil {
		return err
	}
	if err := os.Chmod(tmp, 0o755); err != nil {
		os.Remove(tmp)
		return err
	}
	previous := dest + ".previous"
	os.Remove(previous)
	if err := os.Rename(dest, previous); err != nil && !errors.Is(err, os.ErrNotExist) {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, dest); err != nil {
		os.Rename(previous, dest) // put the working binary back
		os.Remove(tmp)
		return err
	}
	return nil
}

// installBundle swaps the whole signed .app (binary, helper and signature).
func (u *Updater) installBundle(ctx context.Context, client *http.Client, tag string, t Target, sums map[string]string) error {
	parent := filepath.Dir(t.Bundle)
	zip, err := download(ctx, client, tag, t.Asset, parent, sums)
	if err != nil {
		return err
	}
	defer os.Remove(zip)
	stage, err := os.MkdirTemp(parent, ".fleet-update-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	// ditto keeps permissions, extended attributes and the code signature intact.
	if out, err := exec.CommandContext(ctx, "/usr/bin/ditto", "-x", "-k", zip, stage).CombinedOutput(); err != nil {
		return fmt.Errorf("unpack %s: %v: %s", t.Asset, err, strings.TrimSpace(string(out)))
	}
	apps, _ := filepath.Glob(filepath.Join(stage, "*.app"))
	if len(apps) != 1 {
		return fmt.Errorf("%s does not hold exactly one app", t.Asset)
	}
	if _, err := os.Stat(filepath.Join(apps[0], "Contents", "MacOS", filepath.Base(t.Executable))); err != nil {
		return fmt.Errorf("%s has no %s binary", t.Asset, filepath.Base(t.Executable))
	}
	previous := t.Bundle + ".previous"
	os.RemoveAll(previous)
	if err := os.Rename(t.Bundle, previous); err != nil {
		return err
	}
	if err := os.Rename(apps[0], t.Bundle); err != nil {
		os.Rename(previous, t.Bundle)
		return err
	}
	return nil
}
