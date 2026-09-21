package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// macOS attributes privacy requests to responsible code, not just the binary
// making the request. A terminal-spawned Fleet inherits its launcher's identity.
// Launch the signed bundle through LaunchServices so Fleet and all its children
// share Fleet's own TCC grants. Keep the foreground CLI's logs and Ctrl-C.
type bundleLaunch struct {
	Args []string
	Env  []string
	Dir  string
}

var bundleExitPath string

func fleetBundle(executable string) string {
	resolved, err := filepath.EvalSymlinks(executable)
	if err != nil {
		return ""
	}
	macos := filepath.Dir(resolved)
	contents := filepath.Dir(macos)
	bundle := filepath.Dir(contents)
	if filepath.Base(resolved) != "fleet" || filepath.Base(macos) != "MacOS" || filepath.Base(contents) != "Contents" || !strings.HasSuffix(bundle, ".app") {
		return ""
	}
	if _, err := os.Stat(filepath.Join(contents, "Info.plist")); err != nil {
		return ""
	}
	return bundle
}

func needsBundleLaunch(args []string) bool {
	return len(args) > 0 && (args[0] == "up" ||
		(len(args) == 2 && args[0] == "capture" && (args[1] == "permissions" || args[1] == "doctor")))
}

func appLaunchBootstrap() (bool, int, error) {
	if len(os.Args) == 3 && os.Args[1] == "__app_launch" {
		return false, 0, acceptBundleLaunch(os.Args[2])
	}
	if !needsBundleLaunch(os.Args[1:]) {
		return false, 0, nil
	}
	executable, err := os.Executable()
	if err != nil {
		return false, 0, err
	}
	bundle := fleetBundle(executable)
	if bundle == "" {
		return false, 0, nil
	} // Unbundled developer/test builds.
	code, err := launchFleetBundle(bundle, os.Args[1:])
	return true, code, err
}

func acceptBundleLaunch(dir string) error {
	if !filepath.IsAbs(dir) {
		return fmt.Errorf("invalid Fleet launch directory")
	}
	info, err := os.Lstat(dir)
	if err != nil {
		return err
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	if !ok || int(owner.Uid) != os.Getuid() || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return fmt.Errorf("Fleet launch directory must be private to the current user")
	}
	payload := filepath.Join(dir, "launch.json")
	data, err := os.ReadFile(payload)
	if err != nil {
		return err
	}
	var launch bundleLaunch
	if err := json.Unmarshal(data, &launch); err != nil {
		return err
	}
	if !needsBundleLaunch(launch.Args) {
		return fmt.Errorf("unsupported Fleet bundle command")
	}
	if err := os.Remove(payload); err != nil {
		return err
	} // Do not retain join tokens/environment.
	if err := os.Chdir(launch.Dir); err != nil {
		return err
	}
	for _, entry := range launch.Env {
		key, value, ok := strings.Cut(entry, "=")
		// Preserve LaunchServices' application metadata, never the caller's.
		if !ok || key == "__CFBundleIdentifier" || strings.HasPrefix(key, "XPC_") {
			continue
		}
		if err := os.Setenv(key, value); err != nil {
			return err
		}
	}
	os.Args = append([]string{os.Args[0]}, launch.Args...)
	bundleExitPath = filepath.Join(dir, "exit")
	if err := syscall.Dup2(int(os.Stdout.Fd()), int(os.Stderr.Fd())); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "pid"), []byte(strconv.Itoa(os.Getpid())), 0600)
}

func finishAppLaunch(code int) {
	if bundleExitPath != "" {
		_ = os.WriteFile(bundleExitPath, []byte(strconv.Itoa(code)), 0600)
	}
}

func launchFleetBundle(bundle string, args []string) (int, error) {
	dir, err := os.MkdirTemp("", "fleet-launch-")
	if err != nil {
		return 1, err
	}
	defer os.RemoveAll(dir)
	cwd, err := os.Getwd()
	if err != nil {
		return 1, err
	}
	data, err := json.Marshal(bundleLaunch{Args: args, Env: os.Environ(), Dir: cwd})
	if err != nil {
		return 1, err
	}
	if err := os.WriteFile(filepath.Join(dir, "launch.json"), data, 0600); err != nil {
		return 1, err
	}
	logPath := filepath.Join(dir, "output")
	log, err := os.OpenFile(logPath, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return 1, err
	}
	defer log.Close()
	cmd := exec.Command("/usr/bin/open", "-n", "-W", "-g", "-a", bundle,
		"--stdout", logPath, "--stderr", logPath, "--args", "__app_launch", dir)
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return 1, err
	}
	wait := make(chan error, 1)
	go func() { wait <- cmd.Wait() }()
	signals := make(chan os.Signal, 2)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)
	defer signal.Stop(signals)
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	var pending os.Signal
	for {
		select {
		case err := <-wait:
			_, _ = io.Copy(os.Stdout, log)
			if err != nil {
				return 1, fmt.Errorf("launch Fleet.app: %w", err)
			}
			code, err := os.ReadFile(filepath.Join(dir, "exit"))
			if err != nil {
				return 1, nil
			} // Crashed or exited before completing startup.
			status, err := strconv.Atoi(string(code))
			return status, err
		case pending = <-signals:
		case <-ticker.C:
			_, _ = io.Copy(os.Stdout, log)
		}
		if pending != nil {
			if pending == syscall.SIGHUP {
				pending = syscall.SIGTERM
			}
			if b, err := os.ReadFile(filepath.Join(dir, "pid")); err == nil {
				if pid, err := strconv.Atoi(string(b)); err == nil && pid > 0 {
					if child, err := os.FindProcess(pid); err == nil {
						_ = child.Signal(pending)
					}
					pending = nil
				}
			}
		}
	}
}
