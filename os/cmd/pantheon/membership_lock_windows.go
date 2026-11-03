//go:build windows

package main

import "time"

// Windows restarts into an update by starting the new process before this one
// exits, so a held lock would refuse the update. Memberships still use
// separate state directories there.
type errMembershipBusy struct{ pid int }

func (e errMembershipBusy) Error() string {
	return "another node already runs from this state directory"
}

func lockMembership(string) (func(), error) { return func() {}, nil }

func takeOverMembership(string, errMembershipBusy, time.Duration) (func(), error) {
	return func() {}, nil
}
