package localmode

import (
	"archive/tar"
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"golang.org/x/term"
)

// Local product bundles are published as GitHub release assets. The
// pantheon-local-latest release holds one small manifest naming the current
// versioned release and each platform's bundle with its sha256.
var releaseBase = "https://github.com/aristoteleo/PantheonOS/releases/download/"

const manifestAsset = "pantheon-local-latest/pantheon-local.json"

// Manifest is the published description of the current local product.
type Manifest struct {
	Protocol int    `json:"protocol"`
	Version  string `json:"version"`
	Tag      string `json:"tag"`
	// Store is the Store the product's Desktop browses by default.
	Store   string                    `json:"store"`
	Bundles map[string]ManifestBundle `json:"bundles"`
}

// ManifestBundle is one platform's bundle archive.
type ManifestBundle struct {
	Asset  string `json:"asset"`
	SHA256 string `json:"sha256"`
	Bytes  int64  `json:"bytes"`
}

var httpClient = &http.Client{Timeout: 30 * time.Minute}

func fetch(ctx context.Context, url string, limit int64) (io.ReadCloser, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		return nil, fmt.Errorf("%s: %s", url, resp.Status)
	}
	return struct {
		io.Reader
		io.Closer
	}{io.LimitReader(resp.Body, limit), resp.Body}, nil
}

func readManifest(ctx context.Context) (Manifest, ManifestBundle, error) {
	var m Manifest
	body, err := fetch(ctx, releaseBase+manifestAsset, 64<<10)
	if err != nil {
		return m, ManifestBundle{}, fmt.Errorf("the local product's manifest: %w", err)
	}
	defer body.Close()
	if err := json.NewDecoder(body).Decode(&m); err != nil || m.Protocol != 1 || !validName(m.Tag) || !validName(m.Version) ||
		!strings.HasPrefix(m.Store, "https://") {
		return m, ManifestBundle{}, errors.New("the local product's manifest is invalid")
	}
	platform := runtime.GOOS + "-" + runtime.GOARCH
	b, ok := m.Bundles[platform]
	if !ok {
		return m, b, fmt.Errorf("the local product has no bundle for %s yet", platform)
	}
	if !validName(b.Asset) || len(b.SHA256) != 64 || b.Bytes <= 0 || b.Bytes > 4<<30 {
		return m, b, errors.New("the local product's manifest is invalid")
	}
	return m, b, nil
}

func validName(s string) bool {
	if s == "" || len(s) > 128 {
		return false
	}
	for _, r := range s {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || strings.ContainsRune("._-", r)) {
			return false
		}
	}
	return s != "." && s != ".."
}

// bundlesDir keeps downloaded bundles, one directory per version, beside the
// setup in Home (which local-setup creates new).
func bundlesDir() (string, error) {
	home, err := Home()
	if err != nil {
		return "", err
	}
	return filepath.Join(filepath.Dir(home), "bundles"), nil
}

// downloadBundle fetches the current bundle for this machine once, checked
// against the manifest's sha256, and returns its directory and the manifest.
func downloadBundle(ctx context.Context, progress func(string)) (string, Manifest, error) {
	m, b, err := readManifest(ctx)
	if err != nil {
		return "", m, err
	}
	root, err := bundlesDir()
	if err != nil {
		return "", m, err
	}
	dir := filepath.Join(root, m.Version)
	if _, err := os.Stat(filepath.Join(dir, "local-bundle.json")); err == nil {
		return dir, m, nil
	}
	if err := os.MkdirAll(root, 0o755); err != nil {
		return "", m, err
	}
	progress(fmt.Sprintf("Downloading PantheonOS %s for this machine (%d MB, once)…", m.Version, b.Bytes>>20))
	body, err := fetch(ctx, releaseBase+m.Tag+"/"+b.Asset, b.Bytes+1)
	if err != nil {
		return "", m, err
	}
	defer body.Close()
	archive, err := os.CreateTemp(root, ".download-")
	if err != nil {
		return "", m, err
	}
	defer os.Remove(archive.Name())
	defer archive.Close()
	hash := sha256.New()
	n, err := io.Copy(io.MultiWriter(archive, hash), body)
	if err != nil {
		return "", m, err
	}
	if n != b.Bytes || hex.EncodeToString(hash.Sum(nil)) != b.SHA256 {
		return "", m, errors.New("the downloaded bundle does not match the published checksum")
	}
	if _, err := archive.Seek(0, io.SeekStart); err != nil {
		return "", m, err
	}
	staging, err := os.MkdirTemp(root, ".bundle-")
	if err != nil {
		return "", m, err
	}
	defer os.RemoveAll(staging)
	if err := extract(archive, staging); err != nil {
		return "", m, fmt.Errorf("unpacking the bundle: %w", err)
	}
	if _, err := os.Stat(filepath.Join(staging, "local-bundle.json")); err != nil {
		return "", m, errors.New("the downloaded archive is not a local product bundle")
	}
	if err := os.Rename(staging, dir); err != nil {
		return "", m, err
	}
	return dir, m, nil
}

// extract unpacks a tar.gz of regular files and directories into dir, keeping
// executable bits; nothing outside dir, no links.
func extract(r io.Reader, dir string) error {
	gz, err := gzip.NewReader(r)
	if err != nil {
		return err
	}
	tr := tar.NewReader(gz)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		name := filepath.Clean(filepath.FromSlash(h.Name))
		if name == "." {
			continue
		}
		if filepath.IsAbs(name) || name == ".." || strings.HasPrefix(name, ".."+string(filepath.Separator)) {
			return fmt.Errorf("the archive names a path outside it: %s", h.Name)
		}
		target := filepath.Join(dir, name)
		switch h.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			mode := os.FileMode(0o644)
			if h.Mode&0o111 != 0 {
				mode = 0o755
			}
			f, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_EXCL, mode)
			if err != nil {
				return err
			}
			_, err = io.Copy(f, tr)
			if cerr := f.Close(); err == nil {
				err = cerr
			}
			if err != nil {
				return err
			}
		default:
			return fmt.Errorf("the archive holds a link or special file: %s", h.Name)
		}
	}
}

// interactive reports whether the first start can ask its questions.
var interactive = func(in io.Reader) bool {
	f, ok := in.(*os.File)
	return ok && term.IsTerminal(int(f.Fd()))
}

// readSecret reads a key without echoing it on a terminal.
var readSecret = func(in io.Reader, line func() (string, error)) (string, error) {
	if f, ok := in.(*os.File); ok && term.IsTerminal(int(f.Fd())) {
		b, err := term.ReadPassword(int(f.Fd()))
		return strings.TrimSpace(string(b)), err
	}
	return line()
}

// engine is a model service the first start can set up.
type engine struct {
	Name, Label, Endpoint string
	Models                []string
}

// localEngines finds Ollama and LM Studio running on this machine.
var localEngines = func(ctx context.Context) []engine {
	probe := func(url string, parse func([]byte) []string) ([]string, bool) {
		ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
		defer cancel()
		body, err := fetch(ctx, url, 1<<20)
		if err != nil {
			return nil, false
		}
		defer body.Close()
		data, _ := io.ReadAll(body)
		return parse(data), true
	}
	var found []engine
	if models, ok := probe("http://127.0.0.1:11434/api/tags", func(b []byte) []string {
		var v struct {
			Models []struct {
				Name string `json:"name"`
			} `json:"models"`
		}
		json.Unmarshal(b, &v)
		var out []string
		for _, m := range v.Models {
			out = append(out, m.Name)
		}
		return out
	}); ok {
		found = append(found, engine{"ollama", "Ollama on this machine", "http://127.0.0.1:11434", models})
	}
	if models, ok := probe("http://127.0.0.1:1234/v1/models", func(b []byte) []string {
		var v struct {
			Data []struct {
				ID string `json:"id"`
			} `json:"data"`
		}
		json.Unmarshal(b, &v)
		var out []string
		for _, m := range v.Data {
			out = append(out, m.ID)
		}
		return out
	}); ok {
		found = append(found, engine{"lmstudio", "LM Studio on this machine", "http://127.0.0.1:1234", models})
	}
	return found
}

// firstStart sets up local mode on a machine that has none: it downloads the
// product, asks where to work and which model to use, and saves the setup
// (`local-setup`) where `pantheon up` keeps it.
func firstStart(ctx context.Context, std IO) error {
	home, err := Home()
	if err != nil {
		return err
	}
	if !interactive(std.Stdin) {
		return fmt.Errorf("no local setup yet (%s). Run `pantheon up` in a terminal to set one up, or create one:\n"+
			"  pantheon local-setup --bundle BUNDLE --workspace DIR --output %s < choices.json", home, home)
	}
	if _, err := os.Stat(home); err == nil {
		return fmt.Errorf("%s exists without a launch (an interrupted setup?); move it aside and run `pantheon up` again", home)
	}
	say := func(format string, a ...any) { fmt.Fprintf(std.Stderr, format, a...) }
	say("PantheonOS local mode: first start.\n")
	bundle, m, err := downloadBundle(ctx, func(s string) { say("%s\n", s) })
	if err != nil {
		return err
	}
	in := bufio.NewReader(std.Stdin)
	line := func() (string, error) {
		s, err := in.ReadString('\n')
		if err != nil && (err != io.EOF || s == "") {
			return "", err
		}
		return strings.TrimSpace(s), nil
	}
	ask := func(question, fallback string) (string, error) {
		if fallback != "" {
			say("%s [%s]: ", question, fallback)
		} else {
			say("%s: ", question)
		}
		answer, err := line()
		if err != nil {
			return "", err
		}
		if answer == "" {
			answer = fallback
		}
		return answer, nil
	}
	cwd, _ := os.Getwd()
	workspace, err := ask("\nWorkspace folder (the Agent works here)", cwd)
	if err != nil {
		return err
	}
	if workspace, err = filepath.Abs(expandHome(workspace)); err != nil {
		return err
	}
	if info, err := os.Stat(workspace); err != nil || !info.IsDir() {
		return fmt.Errorf("%s is not a folder", workspace)
	}
	choices := map[string]any{"protocol": 1, "project_name": filepath.Base(workspace), "store_origin": m.Store}

	engines := localEngines(ctx)
	say("\nModel service:\n")
	for i, e := range engines {
		say("  %d) %s (%d models)\n", i+1, e.Label, len(e.Models))
	}
	say("  %d) An OpenAI-compatible API (endpoint and key)\n", len(engines)+1)
	pick, err := ask("Choose", "1")
	if err != nil {
		return err
	}
	n, err := strconv.Atoi(pick)
	if err != nil || n < 1 || n > len(engines)+1 {
		return fmt.Errorf("choose 1 to %d", len(engines)+1)
	}
	var model string
	if n <= len(engines) {
		e := engines[n-1]
		choices["engine"], choices["endpoint"] = e.Name, e.Endpoint
		if len(e.Models) == 0 {
			return fmt.Errorf("%s has no models yet; add one, then run `pantheon up` again", e.Label)
		}
		say("\nModels:\n")
		for i, name := range e.Models {
			if i == 20 {
				say("  …\n")
				break
			}
			say("  %d) %s\n", i+1, name)
		}
		answer, err := ask("Model (number or id)", "1")
		if err != nil {
			return err
		}
		model = answer
		if i, err := strconv.Atoi(answer); err == nil && i >= 1 && i <= len(e.Models) {
			model = e.Models[i-1]
		}
	} else {
		endpoint, err := ask("API endpoint", "https://openrouter.ai/api/v1")
		if err != nil {
			return err
		}
		say("API key (not shown): ")
		key, err := readSecret(std.Stdin, line)
		say("\n")
		if err != nil {
			return err
		}
		if model, err = ask("Model id", ""); err != nil {
			return err
		}
		choices["engine"], choices["endpoint"], choices["key"] = "api", endpoint, key
	}
	if model == "" {
		return errors.New("choose a model")
	}
	choices["tiers"] = map[string]string{"low": model, "normal": model, "high": model}

	raw, _ := json.Marshal(choices)
	setup := std
	setup.Stdin = bytes.NewReader(raw)
	setup.Stdout = io.Discard
	code, err := product(ctx, []string{"local-setup", "--bundle", bundle, "--workspace", workspace, "--output", home}, setup)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("the setup was not saved (exit %d)", code)
	}
	say("Saved to %s. Starting…\n\n", home)
	return nil
}

func expandHome(path string) string {
	if path == "~" || strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(path, "~"))
		}
	}
	return path
}
