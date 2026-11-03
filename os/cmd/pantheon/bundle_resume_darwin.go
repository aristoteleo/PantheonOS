package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
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

// appLaunchPath is where a node remembers its reopen settings. Opening the
// app resumes the default state directory's; another fleet's membership keeps
// its own, so joining that fleet never changes what the app reopens.
func appLaunchPath(base, stateDir string) string {
	if isMembershipDir(base, stateDir) {
		return filepath.Join(stateDir, "macos-launch.json")
	}
	return bundleResumePath()
}

// rememberAppLaunch saves resumeArgs, an `up` command line without one-time
// join credentials (membershipArgs).
func rememberAppLaunch(base, stateDir string, resumeArgs []string) {
	executable, err := os.Executable()
	if err != nil || fleetBundle(executable) == "" {
		return
	}
	cwd, err := os.Getwd()
	if err != nil {
		return
	}
	path := appLaunchPath(base, stateDir)
	data, err := json.Marshal(bundleResume{Args: resumeArgs, Dir: cwd})
	if err == nil {
		err = os.MkdirAll(filepath.Dir(path), 0700)
	}
	if err == nil {
		err = writePrivateFile(path, data)
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
