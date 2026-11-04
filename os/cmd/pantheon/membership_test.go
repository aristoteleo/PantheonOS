package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/internal/token"
)

func saveMembership(t *testing.T, dir, fleet string) {
	t.Helper()
	if err := saveFleetState(dir, fleetState{ControllerURL: "https://c.example", FleetID: fleet,
		NatsURL: "nats://n.example", RefreshToken: "rt-" + fleet}); err != nil {
		t.Fatal(err)
	}
}

func joinToken(t *testing.T, fleet string) string {
	t.Helper()
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tok, err := token.SignJoin(priv, token.JoinPayload{FleetID: fleet, JTI: "j", Exp: 1 << 40})
	if err != nil {
		t.Fatal(err)
	}
	return tok
}

func TestJoinTokenFleetReadsWithoutVerifying(t *testing.T) {
	if got := joinTokenFleet(joinToken(t, "f_00b9045a882a59c1")); got != "f_00b9045a882a59c1" {
		t.Fatalf("fleet = %q", got)
	}
	for _, bad := range []string{"", "nodot", "!!!.sig", "e30.sig", joinToken(t, "../x")} {
		if got := joinTokenFleet(bad); got != "" {
			t.Fatalf("joinTokenFleet(%q) = %q, want empty", bad, got)
		}
	}
}

func TestChooseStateDirKeepsEachFleetsMembership(t *testing.T) {
	base := t.TempDir()
	fleetsB := filepath.Join(base, "fleets", "f_b")
	fleetsC := filepath.Join(base, "fleets", "f_c")
	check := func(name, selected string, fresh bool, target, want string) {
		t.Helper()
		got, err := chooseStateDir(base, selected, fresh, target)
		if err != nil || got != want {
			t.Fatalf("%s: dir = %q, %v; want %q", name, got, err, want)
		}
	}
	// A new machine joins in the state directory itself, as before.
	check("first join", "", true, "f_a", base)
	saveMembership(t, base, "f_a")
	check("same fleet re-join keeps its node", "", true, "f_a", base)
	check("a key names no fleet before joining", "", true, "", base)
	check("another account's join token", "", true, "f_b", fleetsB)
	check("resume", "", false, "", base)
	check("--fleet of the base membership", "f_a", false, "", base)
	if _, err := chooseStateDir(base, "f_b", false, ""); err == nil || !strings.Contains(err.Error(), "no saved membership of fleet f_b") {
		t.Fatalf("resume of an unknown membership: %v", err)
	}
	saveMembership(t, fleetsB, "f_b")
	check("--fleet of another membership", "f_b", false, "", fleetsB)
	check("re-join of another membership", "", true, "f_b", fleetsB)
	if _, err := chooseStateDir(base, "f_c", true, "f_b"); err == nil {
		t.Fatal("a join token for another fleet than --fleet must be refused")
	}
	if _, err := chooseStateDir(base, "../x", false, ""); err == nil {
		t.Fatal("an invalid fleet id must not name a directory")
	}

	// The base membership was left: resume the only other one, or ask.
	if err := os.Remove(filepath.Join(base, fleetStateFileName)); err != nil {
		t.Fatal(err)
	}
	check("the only membership", "", false, "", fleetsB)
	check("join token of a saved membership while base is empty", "", true, "f_b", fleetsB)
	saveMembership(t, fleetsC, "f_c")
	if _, err := chooseStateDir(base, "", false, ""); err == nil || !strings.Contains(err.Error(), "--fleet") ||
		!strings.Contains(err.Error(), "f_b, f_c") {
		t.Fatalf("ambiguous resume: %v", err)
	}
}

func TestRetargetMembershipNeverReusesAnotherFleetsDirectory(t *testing.T) {
	base := t.TempDir()
	saveMembership(t, base, "f_a")
	got, err := retargetMembership(base, base, "f_b")
	if err != nil || got != filepath.Join(base, "fleets", "f_b") {
		t.Fatalf("retarget = %q, %v", got, err)
	}
	if _, err := retargetMembership(base, got, "f_b"); err == nil {
		t.Fatal("a refusal of the fleet's own directory must not loop")
	}
	if got, err := retargetMembership(base, got, "f_a"); err != nil || got != base {
		t.Fatalf("retarget to base membership = %q, %v", got, err)
	}
}

func TestMembershipArgsPinTheFleetAndDropJoinCredentials(t *testing.T) {
	got := membershipArgs([]string{"--controller", "https://c", "--join-token", "secret", "--key=pbk_x",
		"--fleet", "f_old", "--fleet=f_older", "--state-dir", "/s"}, "f_b")
	want := []string{"up", "--controller", "https://c", "--state-dir", "/s", "--fleet", "f_b"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("args = %q, want %q", got, want)
	}
	if got := membershipArgs([]string{"--controller", "https://c"}, ""); !reflect.DeepEqual(got, []string{"up", "--controller", "https://c"}) {
		t.Fatalf("unpinned args = %q", got)
	}
}

func TestIsMembershipDir(t *testing.T) {
	base := t.TempDir()
	for dir, want := range map[string]bool{
		base:                                 false,
		filepath.Join(base, "fleets"):        false,
		filepath.Join(base, "fleets", "f_b"): true,
		filepath.Join(base, "fleets", "f_b", "apps"): false,
		filepath.Join(base, "other"):                 false,
	} {
		if got := isMembershipDir(base, dir); got != want {
			t.Fatalf("isMembershipDir(%q) = %v", dir, got)
		}
	}
}

func TestMembershipLockRefusesASecondResume(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("memberships are not locked on Windows")
	}
	dir := t.TempDir()
	release, err := lockMembership(dir)
	if err != nil {
		t.Fatal(err)
	}
	var busy errMembershipBusy
	if _, err := holdMembership(dir, false); !errors.As(err, &busy) || busy.pid != os.Getpid() {
		t.Fatalf("second resume: %v", err)
	}
	// A fresh join never signals itself.
	if _, err := holdMembership(dir, true); err == nil {
		t.Fatal("a takeover of this process's own lock must fail")
	}
	release()
	release, err = holdMembership(dir, false)
	if err != nil {
		t.Fatalf("lock after release: %v", err)
	}
	release()
}

// The macOS primer runs before `up` chooses the membership: it reads shares
// but never saves them, so another fleet's choice stays as it was.
func TestPrimerDoesNotSaveShares(t *testing.T) {
	dir := t.TempDir()
	config := filepath.Join(dir, "file-shares.json")
	if err := os.WriteFile(config, []byte("[]"), 0o600); err != nil {
		t.Fatal(err)
	}
	roots, err := shareRoots(dir, []string{dir}, false, false)
	if err != nil || len(roots) != 1 {
		t.Fatalf("roots = %v, %v", roots, err)
	}
	if b, _ := os.ReadFile(config); string(b) != "[]" {
		t.Fatalf("primer changed the saved shares: %s", b)
	}
}

func TestScopeMembershipStateKeepsEachAccountsOwnState(t *testing.T) {
	base := t.TempDir()
	other := filepath.Join(base, "fleets", "f_b")
	for _, v := range membershipStateVars {
		t.Setenv(v.name, "")
	}
	// The first membership keeps the shared per-user directories.
	if set := scopeMembershipState(base, base); len(set) != 0 || os.Getenv("PANTHEON_CODING_AGENTS_DIR") != "" {
		t.Fatalf("the first membership was moved: %v", set)
	}
	// Another fleet's membership gets its own, unless one is set already.
	t.Setenv("PANTHEON_CLI_HOME", "/custom/cli")
	set := scopeMembershipState(base, other)
	if !reflect.DeepEqual(set, []string{"PANTHEON_CODING_AGENTS_DIR"}) {
		t.Fatalf("set %v", set)
	}
	if got := os.Getenv("PANTHEON_CODING_AGENTS_DIR"); got != filepath.Join(other, "coding-agents") {
		t.Fatalf("coding agents dir %q", got)
	}
	if got := os.Getenv("PANTHEON_CLI_HOME"); got != "/custom/cli" {
		t.Fatalf("an override was replaced: %q", got)
	}
	// What another membership set is not an override: a node started from
	// that node's terminal inherits it.
	third := filepath.Join(base, "fleets", "f_c")
	if set := scopeMembershipState(base, third); len(set) != 1 || os.Getenv("PANTHEON_CODING_AGENTS_DIR") != filepath.Join(third, "coding-agents") {
		t.Fatalf("set %v, dir %q", set, os.Getenv("PANTHEON_CODING_AGENTS_DIR"))
	}
	if scopeMembershipState(base, base); os.Getenv("PANTHEON_CODING_AGENTS_DIR") != "" {
		t.Fatalf("the first membership kept another's dir %q", os.Getenv("PANTHEON_CODING_AGENTS_DIR"))
	}
}
