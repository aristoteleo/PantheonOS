package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// One machine can be a node of several fleets: several accounts each add the
// same computer. Every membership keeps its own state directory, so its own
// node id, node key, credentials and Apps; joining one fleet never rewrites
// another's. The state directory (--state-dir, by default the user config
// "pantheon-fleet" folder) holds the first membership, as before; each other
// fleet lives in <state-dir>/fleets/<fleet id>.
//
// A join (or re-join) of the fleet a directory already belongs to keeps that
// directory and its node id. A join of another fleet goes to that fleet's own
// directory. `up --fleet <id>` resumes a chosen membership.
//
// Per-user state that belongs to an account moves with the membership too
// (scopeMembershipState): the first membership keeps ~/.pantheon/..., each
// other one uses folders in its own state directory.

const membershipsDirName = "fleets"

// membershipStateVars are the per-user state directories that belong to the
// account a membership serves, with the folder each other membership uses
// instead, under its state directory:
//
//   - PANTHEON_CODING_AGENTS_DIR (~/.pantheon/coding-agents): the Coding
//     Agents settings with the PantheonOS access key minted for that
//     account, its model service key and project list, conversations, links
//     and hooks.
//   - PANTHEON_CLI_HOME (~/.pantheon/cli): the pantheon command's login and
//     grant cache, which commands and agents this node starts fall back to
//     without a key of their own.
var membershipStateVars = []struct{ name, dir string }{
	{"PANTHEON_CODING_AGENTS_DIR", "coding-agents"},
	{"PANTHEON_CLI_HOME", "cli"},
}

// scopeMembershipState points this process (and what it starts) at dir's
// own per-user state when dir is a membership other than the first, so one
// account never uses another's access key, projects or login. A variable
// already set wins. It returns the variables it set.
func scopeMembershipState(base, dir string) []string {
	if !isMembershipDir(base, dir) {
		return nil
	}
	var set []string
	for _, v := range membershipStateVars {
		if os.Getenv(v.name) != "" {
			continue
		}
		if os.Setenv(v.name, filepath.Join(dir, v.dir)) == nil {
			set = append(set, v.name)
		}
	}
	return set
}

var fleetIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)

// membershipDir is the state directory of base's membership of fleetID.
func membershipDir(base, fleetID string) (string, error) {
	if !fleetIDPattern.MatchString(fleetID) || strings.Contains(fleetID, "..") {
		return "", fmt.Errorf("invalid fleet id %q", fleetID)
	}
	return filepath.Join(base, membershipsDirName, fleetID), nil
}

// isMembershipDir reports whether dir is one of base's per-fleet directories.
func isMembershipDir(base, dir string) bool {
	rel, err := filepath.Rel(filepath.Join(base, membershipsDirName), dir)
	return err == nil && rel != "." && !strings.HasPrefix(rel, "..") && !strings.ContainsRune(rel, filepath.Separator)
}

// savedFleet is the fleet whose membership dir holds, "" when none.
func savedFleet(dir string) string {
	state, ok, err := loadFleetState(dir)
	if err != nil || !ok {
		return ""
	}
	return state.FleetID
}

// memberships lists the fleets with a saved membership in base's per-fleet
// directories (not base itself).
func memberships(base string) []string {
	entries, err := os.ReadDir(filepath.Join(base, membershipsDirName))
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		if e.IsDir() && savedFleet(filepath.Join(base, membershipsDirName, e.Name())) == e.Name() {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out
}

// joinTokenFleet reads the fleet a join token is for, without verifying it
// (the Controller does): only to choose the state directory before the token
// is spent. "" when the token cannot be read.
func joinTokenFleet(token string) string {
	payload, _, ok := strings.Cut(strings.TrimSpace(token), ".")
	if !ok {
		return ""
	}
	b, err := base64.RawURLEncoding.DecodeString(payload)
	if err != nil {
		return ""
	}
	var p struct {
		FleetID string `json:"fleet_id"`
	}
	if json.Unmarshal(b, &p) != nil || !fleetIDPattern.MatchString(p.FleetID) {
		return ""
	}
	return p.FleetID
}

// chooseStateDir picks the state directory for `up`.
//
//   - selected: --fleet without --nats, the membership to run.
//   - freshJoin: a join credential was given; target is its fleet when known
//     (join tokens name it; keys do not, see retargetMembership).
//   - otherwise this resumes base, or the only saved membership when base
//     holds none.
func chooseStateDir(base, selected string, freshJoin bool, target string) (string, error) {
	inBase := savedFleet(base)
	if selected != "" {
		if target != "" && target != selected {
			return "", fmt.Errorf("the join token is for fleet %s, not --fleet %s", target, selected)
		}
		if inBase == selected || (freshJoin && inBase == "") {
			return base, nil
		}
		dir, err := membershipDir(base, selected)
		if err != nil {
			return "", err
		}
		if freshJoin || savedFleet(dir) == selected {
			return dir, nil
		}
		return "", fmt.Errorf("this machine has no saved membership of fleet %s%s", selected, membershipHint(base))
	}
	if freshJoin {
		if target == "" || inBase == "" || inBase == target {
			if inBase == "" && target != "" {
				// The base was left (or never used) while this fleet has its
				// own directory: keep that identity.
				if dir, err := membershipDir(base, target); err == nil && savedFleet(dir) == target {
					return dir, nil
				}
			}
			return base, nil
		}
		return membershipDir(base, target)
	}
	if inBase != "" {
		return base, nil
	}
	switch others := memberships(base); len(others) {
	case 0:
		return base, nil
	case 1:
		return membershipDir(base, others[0])
	default:
		return "", fmt.Errorf("this machine is a node of several fleets; choose one with --fleet <id>%s", membershipHint(base))
	}
}

func membershipHint(base string) string {
	all := memberships(base)
	if f := savedFleet(base); f != "" {
		all = append([]string{f}, all...)
	}
	if len(all) == 0 {
		return ""
	}
	return " (saved: " + strings.Join(all, ", ") + ")"
}

// retargetMembership is the directory for a join whose fleet turned out to
// differ from the one dir already belongs to: a key does not name its fleet
// before the join, and the Controller refuses (or, older Controllers, answers
// with) another fleet. Never dir itself while it holds another fleet.
func retargetMembership(base, dir, fleetID string) (string, error) {
	if savedFleet(dir) == fleetID {
		return dir, nil
	}
	if savedFleet(base) == fleetID {
		return base, nil
	}
	next, err := membershipDir(base, fleetID)
	if err != nil {
		return "", err
	}
	if next == dir {
		return "", fmt.Errorf("fleet %s refused this machine's node identity for that fleet", fleetID)
	}
	return next, nil
}

// membershipArgs is a resumable `up` command line pinned to fleetID, so a
// restart (Fleet update, reopening the macOS app) resumes this membership and
// not whichever one the default state directory holds.
func membershipArgs(args []string, fleetID string) []string {
	result := resumableArgs(args)
	if fleetID == "" {
		return result
	}
	kept := result[:1]
	for i := 1; i < len(result); i++ {
		name, _, inline := strings.Cut(strings.TrimLeft(result[i], "-"), "=")
		if strings.HasPrefix(result[i], "-") && name == "fleet" {
			if !inline {
				i++
			}
			continue
		}
		kept = append(kept, result[i])
	}
	return append(kept, "--fleet", fleetID)
}

// holdMembership locks dir for this node. A fresh join of a membership whose
// node is running replaces that node (running the install command again
// restarts on the new release); resuming one that runs is refused.
func holdMembership(dir string, freshJoin bool) (func(), error) {
	release, err := lockMembership(dir)
	var busy errMembershipBusy
	if err == nil || !errors.As(err, &busy) {
		return release, err
	}
	if !freshJoin {
		return nil, fmt.Errorf("%w: %s", err, dir)
	}
	fmt.Printf("Replacing this fleet's running node (pid %d)\n", busy.pid)
	release, err = takeOverMembership(dir, busy, 20*time.Second)
	if err != nil {
		return nil, fmt.Errorf("%w: %s", err, dir)
	}
	return release, nil
}
