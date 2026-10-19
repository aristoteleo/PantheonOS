package main

import (
	"encoding/json"
	"errors"
	nodefiles "github.com/aristoteleo/pantheon-apps/node_files"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type sharedDirs []string

func (s *sharedDirs) String() string         { return strings.Join(*s, ", ") }
func (s *sharedDirs) Set(value string) error { *s = append(*s, value); return nil }

// The macOS primer accepts the same sharing flags as up while ignoring join
// options. Handle --flag=value too, especially --no-files=true, so an opt-out
// never accidentally primes the default home share before up starts.
func primeShareOptions(args []string) (string, []string, bool, error) {
	stateDir := defaultStateDir()
	var requested []string
	disabled := false
	for i := 0; i < len(args); i++ {
		name, value, inline := strings.Cut(args[i], "=")
		switch name {
		case "--share-dir", "-share-dir", "--state-dir", "-state-dir":
			if !inline {
				if i+1 == len(args) {
					return "", nil, false, errors.New("missing value for " + name)
				}
				i++
				value = args[i]
			}
			if name == "--share-dir" || name == "-share-dir" {
				requested = append(requested, value)
			} else {
				stateDir = value
			}
		case "--no-files", "-no-files":
			disabled = true
			if inline {
				var err error
				disabled, err = strconv.ParseBool(value)
				if err != nil {
					return "", nil, false, err
				}
			}
		}
	}
	return stateDir, requested, disabled, nil
}

// Only local CLI/configuration can change shares. RPC app_start never supplies roots.
func configureShares(stateDir string, requested []string, disabled bool) ([]string, error) {
	if disabled && len(requested) > 0 {
		return nil, errors.New("choose --share-dir or --no-files, not both")
	}
	config := filepath.Join(stateDir, "file-shares.json")
	paths := requested
	changed := disabled || len(requested) > 0
	if !changed {
		data, err := os.ReadFile(config)
		if os.IsNotExist(err) {
			// A missing configuration is a new/default node. An existing empty
			// list is an explicit opt-out and must stay empty across upgrades.
			paths = []string{"~"}
			changed = true
		} else if err != nil {
			return nil, err
		} else if err = json.Unmarshal(data, &paths); err != nil {
			return nil, err
		}
	}
	if disabled {
		paths = []string{}
	}
	roots, err := nodefiles.NormalizeRoots(paths)
	if err != nil {
		return nil, err
	}
	if changed {
		data, err := json.Marshal(roots)
		if err != nil {
			return nil, err
		}
		if err = os.MkdirAll(stateDir, 0700); err != nil {
			return nil, err
		}
		if err = writePrivateFile(config, data); err != nil {
			return nil, err
		}
	}
	return roots, nil
}
