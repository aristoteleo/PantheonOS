// Package pyruntime is the Python the pantheon binary brings
// (docs/pantheon-cli.md, "Installation"): a pinned uv, the Python it installs
// and environments built from hash-pinned requirements, all under
// ~/.pantheon/runtime. Nothing depends on the machine's own python3.
package pyruntime

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
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
	"runtime"
	"strings"
	"time"
)

// UVVersion is the uv the binary installs, verified by uvSHA256.
const UVVersion = "0.12.18"

// PythonVersion is the interpreter uv installs for the runtime.
const PythonVersion = "3.12"

// uvSHA256 holds the published digests of the uv release archives.
var uvSHA256 = map[string]string{
	"aarch64-apple-darwin":      "cf40e0c6a202190ccd9e0406dcfdd5b2d6668a9a5c779b17948963df32aafe5b",
	"x86_64-apple-darwin":       "2e4108f5395397c8bc5d43bf83d3bdbb2d0e92b90d0efa607756be704905fa33",
	"x86_64-unknown-linux-gnu":  "89eadd7c76fc063887959510d5ba0ab1264dfd5f1143b925ddb73021a40acf16",
	"aarch64-unknown-linux-gnu": "afb6291f3f0a6b4521fc67b947822506c41dde5b60d2189dd8f3695b2ac8c9e7",
	"x86_64-pc-windows-msvc":    "cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff",
	"aarch64-pc-windows-msvc":   "17f27b1c64eacc757ae603579f116a014881e486c5e79ae81877980d4699e943",
}

// downloadBase is where uv's release archives are fetched (tests replace it).
var downloadBase = "https://github.com/astral-sh/uv/releases/download"

// Runtime is a ready uv and Python.
type Runtime struct {
	Root   string
	UV     string
	Python string
}

// Root is ~/.pantheon/runtime, or PANTHEON_RUNTIME_HOME.
func Root() (string, error) {
	if dir := os.Getenv("PANTHEON_RUNTIME_HOME"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".pantheon", "runtime"), nil
}

func target() (string, error) {
	arch := map[string]string{"arm64": "aarch64", "amd64": "x86_64"}[runtime.GOARCH]
	system := map[string]string{"darwin": "apple-darwin", "linux": "unknown-linux-gnu", "windows": "pc-windows-msvc"}[runtime.GOOS]
	if arch == "" || system == "" {
		return "", fmt.Errorf("no managed Python for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	return arch + "-" + system, nil
}

// Ensure installs uv and Python under root when they are missing, and
// returns them. progress, when not nil, hears what is being installed.
func Ensure(ctx context.Context, root string, progress func(string)) (*Runtime, error) {
	say := func(text string) {
		if progress != nil {
			progress(text)
		}
	}
	uv, err := ensureUV(ctx, root, say)
	if err != nil {
		return nil, err
	}
	r := &Runtime{Root: root, UV: uv}
	if r.Python, err = r.findPython(ctx); err != nil {
		say("Installing Python " + PythonVersion + " (once)…")
		if out, err := r.uvCommand(ctx, "python", "install", PythonVersion).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("installing Python %s: %v: %s", PythonVersion, err, tail(out))
		}
		if r.Python, err = r.findPython(ctx); err != nil {
			return nil, err
		}
	}
	return r, nil
}

func ensureUV(ctx context.Context, root string, say func(string)) (string, error) {
	t, err := target()
	if err != nil {
		return "", err
	}
	name := "uv"
	if runtime.GOOS == "windows" {
		name = "uv.exe"
	}
	dir := filepath.Join(root, "uv", UVVersion)
	path := filepath.Join(dir, name)
	if info, err := os.Stat(path); err == nil && info.Mode().IsRegular() {
		return path, nil
	}
	archive := "uv-" + t + ".tar.gz"
	if runtime.GOOS == "windows" {
		archive = "uv-" + t + ".zip"
	}
	say("Installing uv " + UVVersion + " (once)…")
	data, err := download(ctx, downloadBase+"/"+UVVersion+"/"+archive)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	if hex.EncodeToString(sum[:]) != uvSHA256[t] {
		return "", fmt.Errorf("uv %s for %s does not match its published checksum", UVVersion, t)
	}
	binary, err := extract(data, name, strings.HasSuffix(archive, ".zip"))
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	tmp, err := os.CreateTemp(dir, ".uv-")
	if err != nil {
		return "", err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(binary); err != nil {
		tmp.Close()
		return "", err
	}
	if err := tmp.Close(); err != nil {
		return "", err
	}
	if err := os.Chmod(tmp.Name(), 0o755); err != nil {
		return "", err
	}
	return path, os.Rename(tmp.Name(), path)
}

func download(ctx context.Context, url string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("downloading %s: %w", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("downloading %s: HTTP %d", url, resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 200<<20))
}

// extract returns the named file of a uv archive.
func extract(data []byte, name string, isZip bool) ([]byte, error) {
	if isZip {
		r, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			return nil, err
		}
		for _, f := range r.File {
			if filepath.Base(f.Name) == name {
				rc, err := f.Open()
				if err != nil {
					return nil, err
				}
				defer rc.Close()
				return io.ReadAll(io.LimitReader(rc, 200<<20))
			}
		}
		return nil, errors.New("uv is not in its archive")
	}
	gz, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	tr := tar.NewReader(gz)
	for {
		h, err := tr.Next()
		if err != nil {
			return nil, errors.New("uv is not in its archive")
		}
		if h.Typeflag == tar.TypeReg && filepath.Base(h.Name) == name {
			return io.ReadAll(io.LimitReader(tr, 200<<20))
		}
	}
}

func (r *Runtime) uvCommand(ctx context.Context, args ...string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, r.UV, args...)
	cmd.Env = append(os.Environ(),
		"UV_PYTHON_INSTALL_DIR="+filepath.Join(r.Root, "python"),
		"UV_CACHE_DIR="+filepath.Join(r.Root, "cache"),
		"UV_PYTHON_PREFERENCE=only-managed",
		"UV_NO_CONFIG=1",
	)
	return cmd
}

func (r *Runtime) findPython(ctx context.Context) (string, error) {
	out, err := r.uvCommand(ctx, "python", "find", PythonVersion).Output()
	path := strings.TrimSpace(string(out))
	if err != nil || path == "" {
		return "", fmt.Errorf("no managed Python %s yet", PythonVersion)
	}
	return path, nil
}

// Environment returns the Python of an environment at dir built from a
// hash-pinned requirements file, building it when missing or when the
// requirements changed.
func (r *Runtime) Environment(ctx context.Context, dir, requirements string, progress func(string)) (string, error) {
	spec, err := os.ReadFile(requirements)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(spec)
	digest := hex.EncodeToString(sum[:])
	python := filepath.Join(dir, "bin", "python")
	if runtime.GOOS == "windows" {
		python = filepath.Join(dir, "Scripts", "python.exe")
	}
	marker := filepath.Join(dir, ".pantheon-ready")
	if got, err := os.ReadFile(marker); err == nil && strings.TrimSpace(string(got)) == digest {
		return python, nil
	}
	if progress != nil {
		progress("Preparing its Python environment (once per version)…")
	}
	_ = os.RemoveAll(dir)
	if out, err := r.uvCommand(ctx, "venv", "--quiet", "--python", r.Python, dir).CombinedOutput(); err != nil {
		return "", fmt.Errorf("creating the environment: %v: %s", err, tail(out))
	}
	if out, err := r.uvCommand(ctx, "pip", "install", "--quiet", "--python", python,
		"--require-hashes", "-r", requirements).CombinedOutput(); err != nil {
		return "", fmt.Errorf("installing its requirements: %v: %s", err, tail(out))
	}
	return python, os.WriteFile(marker, []byte(digest+"\n"), 0o644)
}

func tail(out []byte) string {
	text := strings.TrimSpace(string(out))
	if len(text) > 1500 {
		text = "…" + text[len(text)-1500:]
	}
	return text
}
