package pantheoncli

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/pyruntime"
)

var revisionRE = regexp.MustCompile(`^[a-f0-9]{64}$`)

// agentChat runs the Agent App's terminal frontend for the owner's Agent
// (docs/os/pantheon-cli.md): the frontend of the exact revision that runs, in an
// environment of the managed Python, talking to the Agent through a loopback
// this command serves with the owner's grant. The frontend holds no grant.
func (r *runner) agentChat(args []string) error {
	dep, err := r.s.Deployment(r.ctx)
	if err != nil {
		return err
	}
	b, err := dep.AppBinding("agent")
	if err != nil {
		return err
	}
	if !revisionRE.MatchString(b.Revision) {
		return errors.New("the Agent reports no release revision")
	}
	root, err := pyruntime.Root()
	if err != nil {
		return err
	}
	progress := func(text string) { fmt.Fprintln(r.stderr, text) }
	dir := filepath.Join(root, "terminals", b.Revision)
	if err := r.fetchTerminal(b, dir, progress); err != nil {
		return err
	}
	rt, err := pyruntime.Ensure(r.ctx, root, progress)
	if err != nil {
		return err
	}
	python, err := rt.Environment(r.ctx, filepath.Join(dir, "env"), filepath.Join(dir, "requirements.txt"), progress)
	if err != nil {
		return err
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	secret := make([]byte, 24)
	if _, err := rand.Read(secret); err != nil {
		return err
	}
	token := hex.EncodeToString(secret)
	server := &http.Server{Handler: r.agentProxy(b, token), ReadHeaderTimeout: 10 * time.Second}
	go server.Serve(ln)
	defer server.Close()

	cmd := exec.CommandContext(r.ctx, python, append([]string{filepath.Join(dir, "terminal.py")}, args...)...)
	cmd.Dir, _ = os.Getwd()
	cmd.Env = append(withoutCredentials(os.Environ()),
		"PANTHEON_AGENT_RPC=http://"+ln.Addr().String()+"/rpc", "PANTHEON_AGENT_RPC_TOKEN="+token)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = r.stdin, r.stdout, r.stderr
	if err := cmd.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return &exitCode{code: exit.ExitCode()}
		}
		return err
	}
	return nil
}

// exitCode carries the terminal frontend's exit status out of Main.
type exitCode struct{ code int }

func (e *exitCode) Error() string { return fmt.Sprintf("exit status %d", e.code) }

// withoutCredentials keeps the owner's keys out of the frontend's process.
func withoutCredentials(env []string) []string {
	out := env[:0:0]
	for _, kv := range env {
		name, _, _ := strings.Cut(kv, "=")
		switch name {
		case "PANTHEON_API_KEY", "PANTHEON_KEY", "PANTHEON_TOKEN", "PANTHEON_GRANT", "PANTHEON_ENDPOINT", "FLEET_KEY":
			continue
		}
		out = append(out, kv)
	}
	return out
}

// fetchTerminal stores the Agent's terminal frontend for its revision once:
// read from the running Agent in chunks, checked against its digest.
func (r *runner) fetchTerminal(b Binding, dir string, progress func(string)) error {
	if info, err := os.Stat(filepath.Join(dir, "terminal.py")); err == nil && info.Mode().IsRegular() {
		return nil
	}
	progress("Fetching the Agent's terminal chat for this version (once)…")
	var data []byte
	digest := ""
	for {
		raw, err := r.s.Call(r.ctx, b, "agent_terminal_frontend", map[string]any{"offset": len(data)}, 120*time.Second)
		if err != nil {
			return fmt.Errorf("fetching the terminal chat: %w", err)
		}
		var part struct {
			SHA256 string `json:"sha256"`
			Size   int    `json:"size"`
			Offset int    `json:"offset"`
			Data   string `json:"data"`
		}
		if err := json.Unmarshal(raw, &part); err != nil || part.Offset != len(data) || part.Size > 512<<20 {
			return errors.New("the Agent sent an invalid terminal chat")
		}
		if digest == "" {
			digest = part.SHA256
		} else if digest != part.SHA256 {
			return errors.New("the Agent changed while its terminal chat was fetched; try again")
		}
		chunk, err := base64.StdEncoding.DecodeString(part.Data)
		if err != nil || (len(chunk) == 0 && len(data) < part.Size) {
			return errors.New("the Agent sent an invalid terminal chat")
		}
		data = append(data, chunk...)
		if len(data) >= part.Size {
			break
		}
	}
	sum := sha256.Sum256(data)
	if hex.EncodeToString(sum[:]) != digest {
		return errors.New("the terminal chat does not match its digest")
	}
	if err := os.MkdirAll(filepath.Dir(dir), 0o755); err != nil {
		return err
	}
	staging, err := os.MkdirTemp(filepath.Dir(dir), ".terminal-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(staging)
	if err := untar(data, staging); err != nil {
		return err
	}
	_ = os.RemoveAll(dir)
	return os.Rename(staging, dir)
}

// untar extracts regular files and directories, never outside dir.
func untar(data []byte, dir string) error {
	gz, err := gzip.NewReader(bytes.NewReader(data))
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
		if filepath.IsAbs(name) || name == ".." || strings.HasPrefix(name, ".."+string(filepath.Separator)) {
			return fmt.Errorf("the terminal chat names a path outside its folder: %s", h.Name)
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
			f, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
			if err != nil {
				return err
			}
			_, err = io.Copy(f, io.LimitReader(tr, 256<<20))
			f.Close()
			if err != nil {
				return err
			}
		}
	}
}

// agentProxy serves the frontend's RPC calls with the owner's grant, on
// loopback and only with the token this command gave it. When the Agent
// moves (an upgrade or restart), it follows to the running instance.
func (r *runner) agentProxy(b Binding, token string) http.Handler {
	var mu sync.Mutex
	current := b
	reply := func(w http.ResponseWriter, v any) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(v)
	}
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Method != http.MethodPost || req.URL.Path != "/rpc" || req.Header.Get("Authorization") != "Bearer "+token {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		var call struct {
			Method   string         `json:"method"`
			Args     map[string]any `json:"args"`
			TimeoutS int            `json:"timeout_s"`
		}
		if err := json.NewDecoder(io.LimitReader(req.Body, 64<<20)).Decode(&call); err != nil || call.Method == "" {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		timeout := time.Duration(min(max(call.TimeoutS, 1), 600)) * time.Second
		ctx, cancel := context.WithCancel(req.Context())
		defer cancel()
		mu.Lock()
		binding := current
		mu.Unlock()
		result, err := r.s.Call(ctx, binding, call.Method, call.Args, timeout)
		var rpcErr *RPCError
		if err != nil && !errors.As(err, &rpcErr) && ctx.Err() == nil {
			// The instance may have moved: find the running one, once.
			if dep, derr := r.s.Deployment(ctx); derr == nil {
				if moved, berr := dep.AppBinding("agent"); berr == nil && moved != binding {
					mu.Lock()
					current = moved
					mu.Unlock()
					result, err = r.s.Call(ctx, moved, call.Method, call.Args, timeout)
				}
			}
		}
		if err != nil {
			reply(w, map[string]any{"success": false, "error": err.Error()})
			return
		}
		reply(w, map[string]any{"success": true, "result": json.RawMessage(result)})
	})
}
