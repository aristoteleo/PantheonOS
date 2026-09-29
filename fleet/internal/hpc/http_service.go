package hpc

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"path/filepath"
	"regexp"
	"strings"
)

// HTTPService is the actual work submitted with an attended Slurm allocation.
type HTTPService struct {
	Kind           string   `json:"kind,omitempty"`
	Name           string   `json:"name"`
	Argv           []string `json:"argv"`
	Cwd            string   `json:"cwd,omitempty"`
	StartupSeconds int      `json:"startup_seconds"`
}

func (s HTTPService) Normalize() (HTTPService, error) {
	if s.Kind != "" && s.Kind != "jupyterlab" && s.Kind != "model-service" {
		return s, errors.New("unknown HPC App kind")
	}
	if !regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`).MatchString(s.Name) || len(s.Argv) == 0 || len(s.Argv) > 64 {
		return s, errors.New("service name and 1..64 argv entries required")
	}
	if s.StartupSeconds == 0 {
		s.StartupSeconds = 60
	}
	if s.StartupSeconds < 1 || s.StartupSeconds > 600 {
		return s, errors.New("startup_seconds must be 1..600")
	}
	if s.Cwd == "" {
		s.Cwd = "."
	}
	if filepath.IsAbs(s.Cwd) || !filepath.IsLocal(s.Cwd) || strings.Contains(s.Cwd, "\\") {
		return s, errors.New("cwd must be relative to the allocation workspace")
	}
	if s.Argv[0] == "" {
		return s, errors.New("service executable is required")
	}
	for _, a := range s.Argv {
		if strings.ContainsRune(a, 0) {
			return s, errors.New("invalid argv")
		}
	}
	b, _ := json.Marshal(s)
	if len(b) > 65536 {
		return s, errors.New("service command too large")
	}
	return s, nil
}
func (s HTTPService) Revision() string {
	b, _ := json.Marshal(s)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

//go:embed primary_http.py
var primaryHTTP string
