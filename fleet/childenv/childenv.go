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
