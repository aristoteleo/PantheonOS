package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Keep only the successful node invocation, never the caller's environment or
// one-time join credentials. Authentication and folder choices already have
// their own persisted state. This also preserves custom state-dir/workdir flags.
type bundleResume struct {
	Args []string
	Dir  string
}

func bundleResumePath() string {
	return filepath.Join(defaultStateDir(), "macos-launch.json")
}

func resumableArgs(args []string) []string {
	result := []string{"up"}
	for i := 0; i < len(args); i++ {
		name, _, inline := strings.Cut(strings.TrimLeft(args[i], "-"), "=")
		if strings.HasPrefix(args[i], "-") && (name == "key" || name == "join-token") {
			if !inline {
				i++
			}
			continue
		}
		result = append(result, args[i])
	}
	return result
}

func rememberAppLaunch(args []string) {
	executable, err := os.Executable()
	if err != nil || fleetBundle(executable) == "" {
		return
	}
	cwd, err := os.Getwd()
	if err != nil {
		return
	}
	data, err := json.Marshal(bundleResume{Args: resumableArgs(args), Dir: cwd})
	if err == nil {
		err = os.MkdirAll(filepath.Dir(bundleResumePath()), 0700)
	}
	if err == nil {
		err = writePrivateFile(bundleResumePath(), data)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "Could not save Pantheon Fleet reopen settings: %v\n", err)
	}
}

func readBundleResume(path, home string) (bundleResume, error) {
	resume := bundleResume{Args: []string{"up"}, Dir: home}
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return resume, nil
	}
	if err != nil {
		return resume, err
	}
	if err := json.Unmarshal(data, &resume); err != nil {
		return resume, err
	}
	if len(resume.Args) == 0 || resume.Args[0] != "up" || !filepath.IsAbs(resume.Dir) {
		return resume, fmt.Errorf("invalid Pantheon Fleet reopen settings")
	}
	return resume, nil
}

func resumeFleetBundle() error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	resume, err := readBundleResume(bundleResumePath(), home)
	if err != nil {
		return err
	}
	if err := os.Chdir(resume.Dir); err != nil {
		return fmt.Errorf("restore Fleet working directory: %w", err)
	}
	os.Args = append([]string{os.Args[0]}, resume.Args...)
	return nil
}
