// Package childenv is the environment a node gives the processes users and
// agents run on it: shell sessions, terminals, tasks and App processes.
//
// The node's own credentials stay with the node. A command run in a sandbox
// (a user's script, a package it installs, an agent's command) must not read
// the owner's Fleet key, the node's broker credential, a platform credential
// or an App's private configuration. The user's own model-proxy key
// (LLM_API_KEY) is kept: it is theirs, and analysis code uses it.
package childenv

import (
	"encoding/json"
	"os"
	"strings"
)

// Withheld are the variables never passed to a child process.
var Withheld = map[string]bool{
	"FLEET_KEY":              true, // the owner's Fleet credential, for joining
	"NATS_JWT":               true, // the node's broker credential
	"NATS_NKEY_SEED":         true,
	"MODAL_TOKEN_ID":         true, // a platform credential
	"MODAL_TOKEN_SECRET":     true,
	"PANTHEON_STORE_TOKEN":   true, // the owner's Hub session for the Store
	"PANTHEON_STATE_TOKEN":   true, // the Hub's state-sync credential
	"LIVE_VIEW_DATA_TOKEN":   true,
	"PANTHEON_APP_RPC_TOKEN": true, // an App's control-plane bearer
	"PANTHEON_APP_CONFIG":    true, // an App's private dependency configuration
}

// WorkspaceVariable carries a node's workspace environment into a managed App
// that runs users' commands (its package sets it to ${WORKSPACE_ENV}). The App
// process itself starts from a minimal environment; the commands it runs get
// the node's, as a Terminal on that node does.
const WorkspaceVariable = "PANTHEON_WORKSPACE_ENV"

// maxWorkspace keeps the encoded environment well under the kernel's limit
// for one environment string (128 KiB on Linux).
const maxWorkspace = 64 << 10

// EncodeWorkspace is this process's environment, without the withheld
// variables, as WorkspaceVariable carries it; empty when it is too large to
// pass, which leaves the App on its own environment.
func EncodeWorkspace() string {
	b, err := json.Marshal(without(Environ(), WorkspaceVariable))
	if err != nil || len(b) > maxWorkspace {
		return ""
	}
	return string(b)
}

// Workspace is the environment for the commands users and agents run: the
// node's workspace environment when this process was given one, otherwise its
// own. Either way without the withheld variables.
func Workspace() []string {
	var env []string
	if value := os.Getenv(WorkspaceVariable); value != "" && json.Unmarshal([]byte(value), &env) == nil {
		return without(Filter(env), WorkspaceVariable)
	}
	// An older node passes the placeholder through unexpanded.
	return without(Environ(), WorkspaceVariable)
}

// Getenv reads key from Workspace.
func Getenv(key string) string {
	for _, variable := range Workspace() {
		if k, v, _ := strings.Cut(variable, "="); k == key {
			return v
		}
	}
	return ""
}

func without(env []string, key string) []string {
	out := env[:0:0]
	for _, variable := range env {
		if k, _, _ := strings.Cut(variable, "="); k != key {
			out = append(out, variable)
		}
	}
	return out
}

// Environ is os.Environ without the withheld variables.
func Environ() []string {
	return Filter(os.Environ())
}

// Filter drops the withheld variables from an environment.
func Filter(env []string) []string {
	out := make([]string, 0, len(env))
	for _, variable := range env {
		key, _, _ := strings.Cut(variable, "=")
		if Withheld[strings.ToUpper(key)] {
			continue
		}
		out = append(out, variable)
	}
	return out
}
