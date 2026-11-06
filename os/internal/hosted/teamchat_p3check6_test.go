package hosted

// Fixes after the sixth Phase 3 check (docs/apps/team-chat.md, "Fixes after
// the sixth Phase 3 check"): look-alike letters are folded with the Unicode
// confusables table and a word may not mix alphabets; a handle's - and _
// are left out before it is checked, any handle holding pantheon is
// reserved, and a handle's words follow the names' rule.

import (
	"strings"
	"testing"
)

// Finding 4: look-alike names and separator handles.
func TestLookAlikeNamesAndSeparatedHandlesAreRefused(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	for _, name := range []string{
		"Ρantheon",                            // Greek capital Rho
		"Pantheοn",                            // Greek small omicron
		"Ѕupport",                             // Cyrillic Dze
		"Ꮲantheon",                            // Cherokee
		"\u0392\u039f\u03a4",                  // Greek only: reads BOT
		"\U0001d40f\U0001d41a\U0001d427theon", // mathematical bold
		"Official Pantheon", "The Pantheon", "XPantheon", "Help-Desk", "help_desk",
	} {
		if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"name": name}); e == "" {
			t.Fatalf("name %q accepted", name)
		}
	}
	// A word that mixes alphabets is refused as such, whatever it reads as.
	if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"name": "G\u03bfrdon"}); !strings.Contains(e, "mix alphabets") {
		t.Fatalf("a name mixing Latin and Greek: %q", e)
	}
	for _, handle := range []string{"help-desk", "help_desk", "sys-admin", "super-user", "official-pantheon", "the-pantheon",
		"xpantheon", "xpantheonx", "p4ntheon-fan", "weize-owner", "super_admin", "help-desk2"} {
		if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"handle": handle}); !strings.Contains(e, "reserved") {
			t.Fatalf("@%s: %s", handle, e)
		}
	}
	// Names in one alphabet, and handles that only hold such letters, stay free.
	for _, name := range []string{"Jürgen Müller", "Zoë", "Иван Петров",
		"Σοφία", "李雷 Li", "Ana Gent", "Staffan Rootes"} {
		callAs(t, h, b, bob, "me_update", map[string]any{"name": name})
	}
	for _, handle := range []string{"ana-gent", "help-me", "pan-theo", "bob_allison"} {
		callAs(t, h, b, bob, "me_update", map[string]any{"handle": handle})
	}
}

// Names and handles follow one rule: a handle is reserved exactly when the
// name written with the same words is.
func TestNamesAndHandlesShareOneRule(t *testing.T) {
	for _, c := range []struct{ name, handle string }{
		{"Help Desk", "help-desk"}, {"Sys Admin", "sys_admin"}, {"Super User", "super-user"},
		{"Official Pantheon", "official-pantheon"}, {"The Pantheon", "the-pantheon"}, {"Weize Owner", "weize-owner"},
		{"Ana Gent", "ana-gent"}, {"Help Me", "help-me"}, {"Pan Theo", "pan-theo"},
	} {
		if n, hd := reservedName(c.name), reservedHandle(c.handle); n != hd {
			t.Errorf("name %q reserved %v, handle @%s reserved %v", c.name, n, c.handle, hd)
		}
	}
	// The table folds what the old list missed.
	for in, want := range map[string]string{"Ρantheon": "pantheon", "Pantheοn": "pantheon",
		"Ѕupport": "support", "Ꮲantheon": "pantheon"} {
		if got := skeleton(in); got != want {
			t.Errorf("skeleton(%q) = %q, want %q", in, got, want)
		}
	}
}
