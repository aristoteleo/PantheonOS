package main

import (
	"encoding/json"
	"errors"
	nodefiles "github.com/aristoteleo/pantheon-apps/node_files"
	"os"
	"path/filepath"
	"strings"
)

type sharedDirs []string

func (s *sharedDirs) String() string         { return strings.Join(*s, ", ") }
func (s *sharedDirs) Set(value string) error { *s = append(*s, value); return nil }

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
			return []string{}, nil
		}
		if err != nil {
			return nil, err
		}
		if err = json.Unmarshal(data, &paths); err != nil {
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
