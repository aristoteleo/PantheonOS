package main

import "strings"

// resumableArgs is a node invocation that can run again unattended: the
// one-time join credentials are dropped because the join already persisted a
// node-bound refresh token. Used to reopen the macOS app and to restart into
// a Fleet update.
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

// inAppEnv marks a process restarting into an update inside the macOS app.
const inAppEnv = "FLEET_RESTARTED_IN_APP"
