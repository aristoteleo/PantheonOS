package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/aristoteleo/pantheon-fleet/appsvc"
)

func workspaceRoot(data string) (string, error) {
	values, err := appsvc.RuntimeValues()
	if err != nil {
		return "", err
	}
	raw, configured := values["shell"]
	if !configured {
		root := filepath.Join(data, "workspace")
		return root, os.MkdirAll(root, 0700)
	}
	var config struct {
		Workspace string `json:"workspace"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&config) != nil || decoder.Decode(new(any)) != io.EOF || !filepath.IsAbs(config.Workspace) {
		return "", fmt.Errorf("Shell requires an explicit absolute workspace directory")
	}
	root, err := filepath.EvalSymlinks(config.Workspace)
	if err != nil {
		return "", fmt.Errorf("Shell workspace is unavailable")
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return "", fmt.Errorf("Shell workspace is unavailable")
	}
	// A project path is borrowed, never created/deleted by the provider. Session
	// isolation remains cwd/env isolation, not an OS filesystem sandbox.
	return root, nil
}
