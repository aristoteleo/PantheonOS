package hpcconn

import (
	"encoding/hex"
	"os/exec"
	"strings"
)

// macKeychain uses the login keychain through /usr/bin/security. Commands go
// over stdin (`security -i`), so the password never appears in a process list.
type macKeychain struct{}

func systemKeychain() keychain { return macKeychain{} }

func (macKeychain) available() bool { return true }

func (k macKeychain) has(c Cluster) bool {
	return exec.Command("/usr/bin/security", "find-generic-password", "-s", keychainService, "-a", account(c)).Run() == nil
}

func (macKeychain) load(c Cluster) (string, bool) {
	out, err := exec.Command("/usr/bin/security", "find-generic-password", "-s", keychainService, "-a", account(c), "-w").Output()
	if err != nil {
		return "", false
	}
	return strings.TrimRight(string(out), "\n"), true
}

func (macKeychain) save(c Cluster, pw string) {
	cmd := exec.Command("/usr/bin/security", "-i")
	cmd.Stdin = strings.NewReader("add-generic-password -U -s \"" + keychainService + "\" -a " + account(c) +
		" -X " + hex.EncodeToString([]byte(pw)) + "\n")
	cmd.Run() //nolint:errcheck
}

func (macKeychain) forget(c Cluster) {
	exec.Command("/usr/bin/security", "delete-generic-password", "-s", keychainService, "-a", account(c)).Run() //nolint:errcheck
}
