package hosted

// Fixes after the fifth Phase 3 check (docs/apps/team-chat.md, "Fixes after
// the fifth Phase 3 check"): names and handles that read as PantheonOS or
// its staff are refused, with the same digit and plural rules as the role
// words.

import (
	"strings"
	"testing"
)

// Finding 4: platform and staff names.
func TestPlatformAndStaffNamesAreRefused(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	for _, name := range []string{"PantheonOS", "Pantheon Team", "Support", "Staff", "Root", "sudo", "Pantheon0S",
		"Supp0rt", "R00t", "Staffs", "Weize from Support", "PantheonSupport", "Superuser", "Help Desk Root"} {
		if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"name": name}); e == "" {
			t.Fatalf("name %q accepted", name)
		}
	}
	for _, handle := range []string{"pantheon", "support", "root", "pantheonos", "pantheon-team", "pantheon_2", "supp0rt",
		"r00t", "roots", "support1", "staff", "sudo", "sysadmin"} {
		if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"handle": handle}); !strings.Contains(e, "reserved") {
			t.Fatalf("@%s: %s", handle, e)
		}
	}
	// Names that merely contain these letters stay free.
	for _, name := range []string{"Staffan Rootes", "Pan Theo"} {
		callAs(t, h, b, bob, "me_update", map[string]any{"name": name})
	}
	callAs(t, h, b, bob, "me_update", map[string]any{"name": "Bob Allison", "handle": "rooty"})
}
