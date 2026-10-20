// Package localmode runs PantheonOS on this machine (docs/os/pantheon-cli.md,
// "Local mode"): `pantheon up`, `pantheon down` and `pantheon status`, and the
// local product's own commands (`pantheon local`, `local-setup`,
// `local-update`, `local-release`).
//
// The controller, the broker and the node are this binary. The owner that
// composes the deployment and supervises them is the product bundle's own
// Python (pantheonos.local, in the bundle's owner/), run in the managed
// interpreter with its hash-pinned requirements. Nothing is installed with pip
// by hand.
package localmode

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"

	"github.com/aristoteleo/pantheonos/os/internal/pyruntime"
)

// FleetBinaryEnv tells the bundle's owner that this binary runs the Fleet
// (pantheonos/apps/local_agent.py, FLEET_BINARY).
const FleetBinaryEnv = "PANTHEON_FLEET_BINARY"

// productCommands are the local product's commands, served by its Python.
var productCommands = map[string]bool{"local": true, "local-setup": true, "local-update": true, "local-release": true}

// IO is where a command reads and writes.
type IO struct {
	Stdin          io.Reader
	Stdout, Stderr io.Writer
}

// Handles reports whether this package runs args: `up`, `down`, `status`,
// and a product command that names its bundle or launch. Without one, a
// product command is the installed agent package's (pantheon-agent).
func Handles(args []string) bool {
	if len(args) == 0 {
		return false
	}
	switch args[0] {
	case "up", "down", "status":
		return true
	}
	if !productCommands[args[0]] {
		return false
	}
	_, hasBundle := option(args[1:], "--bundle")
	_, hasLaunch := option(args[1:], "--launch")
	return hasBundle || hasLaunch
}

// Main runs one of the commands Handles accepts and returns its exit code.
func Main(ctx context.Context, args []string, std IO) int {
	var err error
	code := 0
	switch args[0] {
	case "up":
		code, err = up(ctx, args[1:], std)
	case "down":
		err = down(ctx, args[1:], std)
	case "status":
		err = status(ctx, args[1:], std)
	default:
		code, err = product(ctx, args, std)
	}
	if err != nil {
		fmt.Fprintln(std.Stderr, "pantheon:", err)
		if code == 0 {
			code = 1
		}
	}
	return code
}

// Home holds `pantheon up`'s own setup and launch: ~/.pantheon/local, or
// PANTHEON_LOCAL_HOME.
func Home() (string, error) {
	if dir := os.Getenv("PANTHEON_LOCAL_HOME"); dir != "" {
		return filepath.Abs(dir)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".pantheon", "local"), nil
}

// Launch is a saved local launch (pantheonos/local/launch.py).
type Launch struct {
	Protocol    int      `json:"protocol"`
	Launcher    []string `json:"launcher"`
	Bundle      string   `json:"bundle"`
	Setup       string   `json:"setup"`
	Profile     string   `json:"profile"`
	Workspace   string   `json:"workspace"`
	Credentials string   `json:"credentials,omitempty"`
}

func readLaunch(path string) (Launch, error) {
	var l Launch
	data, err := os.ReadFile(path)
	if err != nil {
		return l, err
	}
	if err := json.Unmarshal(data, &l); err != nil || l.Protocol != 1 {
		return l, fmt.Errorf("%s is not a local launch", path)
	}
	for _, p := range []string{l.Bundle, l.Setup, l.Profile, l.Workspace} {
		if !filepath.IsAbs(p) {
			return l, fmt.Errorf("%s names a relative path", path)
		}
	}
	return l, nil
}

// launchPath is --launch, or the one `pantheon up` keeps in Home.
func launchPath(args []string) (string, error) {
	if path, ok := option(args, "--launch"); ok {
		return filepath.Abs(path)
	}
	home, err := Home()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, "launch.json"), nil
}

// option finds --name VALUE or --name=VALUE.
func option(args []string, name string) (string, bool) {
	for i, arg := range args {
		if arg == "--" {
			break
		}
		if arg == name && i+1 < len(args) {
			return args[i+1], true
		}
		if value, ok := strings.CutPrefix(arg, name+"="); ok {
			return value, true
		}
	}
	return "", false
}

// up runs the local Apps in the foreground: `pantheon local` with the saved
// launch, or with the launch and bundle options given. Ctrl-C stops them.
func up(ctx context.Context, args []string, std IO) (int, error) {
	if _, ok := option(args, "--bundle"); !ok {
		if _, ok := option(args, "--launch"); !ok {
			path, err := launchPath(nil)
			if err != nil {
				return 0, err
			}
			if _, err := os.Stat(path); err != nil {
				// First start: download the product and set it up.
				if err := firstStart(ctx, std); err != nil {
					return 0, err
				}
			}
			args = append([]string{"--launch", path}, args...)
		}
	}
	return product(ctx, append([]string{"local"}, args...), std)
}

// product runs one of the bundle's own commands with its Python.
func product(ctx context.Context, args []string, std IO) (int, error) {
	bundle, err := bundleOf(args[1:])
	if err != nil {
		return 0, err
	}
	o, err := ownerOf(bundle)
	if err != nil {
		return 0, err
	}
	progress := func(text string) { fmt.Fprintln(std.Stderr, text) }
	python, err := pythonFor(ctx, o, progress)
	if err != nil {
		return 0, err
	}
	self, err := executable()
	if err != nil {
		return 0, err
	}
	if args[0] == "local-setup" {
		_, py := option(args, "--python")
		_, bin := option(args, "--pantheon")
		if !py && !bin {
			// The launch then runs `pantheon local` (the native Desktop too).
			args = append(args, "--pantheon", self)
		}
	}
	// -I: none of the machine's Python settings or user packages apply.
	const bootstrap = "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); sys.argv[0] = 'pantheon'; " +
		"runpy.run_module('pantheon', run_name='__main__')"
	cmd := exec.Command(python, append([]string{"-I", "-c", bootstrap, o.Dir}, args...)...)
	cmd.Env = append(ownerEnv(os.Environ()), FleetBinaryEnv+"="+self)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = std.Stdin, std.Stdout, std.Stderr
	return runForwarding(ctx, cmd)
}

// runForwarding runs the owner in the foreground. Ctrl-C reaches it from the
// terminal; a SIGTERM or SIGHUP sent to this process is passed on, so the
// owner stops its Apps before exiting.
func runForwarding(ctx context.Context, cmd *exec.Cmd) (int, error) {
	signals := make(chan os.Signal, 4)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM, syscall.SIGHUP)
	defer signal.Stop(signals)
	if err := cmd.Start(); err != nil {
		return 0, err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	for {
		select {
		case s := <-signals:
			if s != os.Interrupt {
				_ = cmd.Process.Signal(s)
			}
		case <-ctx.Done():
			_ = cmd.Process.Signal(syscall.SIGTERM)
		case err := <-done:
			var exit *exec.ExitError
			if errors.As(err, &exit) {
				return exit.ExitCode(), nil
			}
			return 0, err
		}
	}
}

// ownerEnv is this environment without the owner's keys and without another
// Python environment's settings.
func ownerEnv(env []string) []string {
	out := env[:0:0]
	for _, kv := range env {
		name, _, _ := strings.Cut(kv, "=")
		switch name {
		case "PANTHEON_API_KEY", "PANTHEON_KEY", "PANTHEON_TOKEN", "PANTHEON_GRANT", "FLEET_KEY",
			"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", FleetBinaryEnv:
			continue
		}
		out = append(out, kv)
	}
	return out
}

func bundleOf(args []string) (string, error) {
	if bundle, ok := option(args, "--bundle"); ok {
		return filepath.Abs(bundle)
	}
	if path, ok := option(args, "--launch"); ok {
		l, err := readLaunch(path)
		if err != nil {
			return "", err
		}
		return l.Bundle, nil
	}
	return "", errors.New("name the product bundle (--bundle) or a launch (--launch)")
}

// owner is the bundle's owner: the pantheon package it runs with and the
// owner's hash-pinned requirements (pantheonos/apps/local_agent.py, OWNER).
type owner struct {
	Dir, Requirements, Digest string
}

// ownerOf finds a bundle's owner, for this machine.
func ownerOf(bundle string) (owner, error) {
	var index struct {
		Platform string `json:"platform"`
		Owner    string `json:"owner"`
	}
	if err := readJSON(filepath.Join(bundle, "local-bundle.json"), &index); err != nil {
		return owner{}, fmt.Errorf("%s is not a local product bundle: %w", bundle, err)
	}
	if want := runtime.GOOS + "-" + runtime.GOARCH; index.Platform != want {
		return owner{}, fmt.Errorf("the bundle is for %s, not this %s machine", index.Platform, want)
	}
	if index.Owner == "" {
		return owner{}, errors.New("the bundle carries no owner; rebuild it with this release's local_agent")
	}
	dir, err := inside(bundle, index.Owner)
	if err != nil {
		return owner{}, err
	}
	o := owner{Dir: dir, Requirements: filepath.Join(dir, "requirements.txt")}
	if _, err := os.Stat(filepath.Join(dir, "pantheon", "__main__.py")); err != nil {
		return owner{}, errors.New("the bundle's owner carries no pantheon package")
	}
	spec, err := os.ReadFile(o.Requirements)
	if err != nil {
		return owner{}, errors.New("the bundle's owner has no requirements.txt")
	}
	sum := sha256.Sum256(spec)
	o.Digest = hex.EncodeToString(sum[:])
	return o, nil
}

// inside joins a bundle's relative path, never leaving root.
func inside(root, rel string) (string, error) {
	clean := filepath.Clean(filepath.FromSlash(rel))
	if rel == "" || filepath.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("the bundle names a path outside it: %q", rel)
	}
	return filepath.Join(root, clean), nil
}

func readJSON(path string, v any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, v)
}

// pythonFor is the managed interpreter with the owner's requirements, kept
// per requirements digest (bundles with the same requirements share it).
var pythonFor = func(ctx context.Context, o owner, progress func(string)) (string, error) {
	root, err := pyruntime.Root()
	if err != nil {
		return "", err
	}
	rt, err := pyruntime.Ensure(ctx, root, progress)
	if err != nil {
		return "", err
	}
	return rt.Environment(ctx, filepath.Join(root, "owners", o.Digest[:16]), o.Requirements, progress)
}

var executable = func() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	return filepath.EvalSymlinks(exe)
}

// flags parses --launch for down and status.
func flags(name string, args []string, out io.Writer) (*flag.FlagSet, *string, *bool, error) {
	fs := flag.NewFlagSet("pantheon "+name, flag.ContinueOnError)
	fs.SetOutput(out)
	launch := fs.String("launch", "", "the local launch (default: the one `pantheon up` keeps)")
	asJSON := fs.Bool("json", false, "print JSON")
	return fs, launch, asJSON, fs.Parse(args)
}
