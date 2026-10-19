// Package nativecapture discovers the node's installed capture executable and
// native QuPath installation for the generated Fleet stream adapter.
package nativecapture

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

type Status struct {
	Protocol        int    `json:"protocol"`
	Backend         string `json:"backend"`
	Available       bool   `json:"available"`
	ScreenRecording bool   `json:"screen_recording"`
	Input           bool   `json:"input"`
	Interactive     bool   `json:"interactive"`
	Setup           bool   `json:"setup"`
}

func (s Status) Ready() bool { return s.Available && s.ScreenRecording && s.Input }

// SetupNeeded never opens a window in SSH/headless sessions or on platforms
// without the macOS privacy flow. Explicit `capture permissions` stays available.
func SetupNeeded(platform string, s Status, ssh, disabled bool) bool {
	return platform == "darwin" && s.Setup && s.Available && s.Interactive && !s.Ready() && !ssh && !disabled
}

func Setup(ctx context.Context, automatic bool) error {
	path := Path()
	if path == "" {
		return fmt.Errorf("native capture helper is not installed")
	}
	flag := "--permissions"
	if automatic {
		flag = "--setup"
	}
	cmd := exec.CommandContext(ctx, path, flag)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	return cmd.Run()
}

func Path() string {
	if runtime.GOOS != "darwin" && runtime.GOOS != "windows" {
		return ""
	}
	candidate := os.Getenv("PANTHEON_NATIVE_CAPTURE_HELPER")
	if candidate == "" {
		binary, err := os.Executable()
		if err != nil {
			return ""
		}
		if resolved, err := filepath.EvalSymlinks(binary); err == nil {
			binary = resolved
		}
		name := "fleet-native-capture"
		if runtime.GOOS == "windows" {
			name += ".exe"
		}
		candidate = filepath.Join(filepath.Dir(binary), name)
	}
	if !filepath.IsAbs(candidate) {
		return ""
	}
	if stat, err := os.Stat(candidate); err == nil && stat.Mode().IsRegular() {
		return candidate
	}
	return ""
}

func Probe() (Status, error) {
	var status Status
	path := Path()
	if path == "" {
		return status, fmt.Errorf("native capture helper is not installed")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, path, "--probe").Output()
	if err != nil {
		return status, err
	}
	err = json.Unmarshal(out, &status)
	if err == nil && status.Protocol != 1 {
		err = fmt.Errorf("unsupported native capture protocol")
	}
	return status, err
}

func QuPath() string {
	candidates := []string{os.Getenv("PANTHEON_QUPATH_EXECUTABLE"), "/Applications/QuPath.app/Contents/MacOS/QuPath"}
	for _, name := range []string{"qupath", "QuPath"} {
		if path, err := exec.LookPath(name); err == nil {
			candidates = append(candidates, path)
		}
	}
	for _, root := range []string{os.Getenv("ProgramFiles"), os.Getenv("LOCALAPPDATA")} {
		if root == "" {
			continue
		}
		paths, _ := filepath.Glob(filepath.Join(root, "QuPath*", "QuPath*.exe"))
		for _, path := range paths {
			if !strings.Contains(strings.ToLower(filepath.Base(path)), "console") {
				candidates = append(candidates, path)
			}
		}
	}
	for _, path := range candidates {
		if filepath.IsAbs(path) {
			if stat, err := os.Stat(path); err == nil && stat.Mode().IsRegular() {
				return path
			}
		}
	}
	return ""
}
