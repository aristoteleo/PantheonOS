package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestFleetStateRoundTripIsPrivate(t *testing.T) {
	dir := t.TempDir()
	want := fleetState{
		ControllerURL: "https://controller.example",
		FleetID:       "f_test",
		NatsURL:       "tls://nats.example:4222",
		Relays:        []string{"relay-a", "relay-b"},
		RefreshToken:  "refresh-token-value",
	}
	if err := saveFleetState(dir, want); err != nil {
		t.Fatalf("saveFleetState: %v", err)
	}

	got, found, err := loadFleetState(dir)
	if err != nil {
		t.Fatalf("loadFleetState: %v", err)
	}
	if !found {
		t.Fatal("loadFleetState found=false, want true")
	}
	if got.ControllerURL != want.ControllerURL || got.FleetID != want.FleetID ||
		got.NatsURL != want.NatsURL || got.RefreshToken != want.RefreshToken {
		t.Fatalf("state = %#v, want %#v", got, want)
	}
	if len(got.Relays) != len(want.Relays) || got.Relays[0] != want.Relays[0] || got.Relays[1] != want.Relays[1] {
		t.Fatalf("relays = %v, want %v", got.Relays, want.Relays)
	}

	info, err := os.Stat(filepath.Join(dir, fleetStateFileName))
	if err != nil {
		t.Fatalf("stat state file: %v", err)
	}
	if gotMode := info.Mode().Perm(); runtime.GOOS != "windows" && gotMode != 0o600 {
		t.Fatalf("state file mode = %04o, want 0600", gotMode)
	}
}

func TestLoadFleetStateMissingIsNotAnError(t *testing.T) {
	_, found, err := loadFleetState(t.TempDir())
	if err != nil {
		t.Fatalf("loadFleetState: %v", err)
	}
	if found {
		t.Fatal("loadFleetState found=true, want false")
	}
}

func TestPersistFleetStateRefreshTokenRotatesStoredToken(t *testing.T) {
	dir := t.TempDir()
	state := fleetState{
		ControllerURL: "https://controller.example",
		FleetID:       "f_test",
		NatsURL:       "tls://nats.example:4222",
		RefreshToken:  "old-token",
	}
	if err := saveFleetState(dir, state); err != nil {
		t.Fatalf("saveFleetState: %v", err)
	}

	updated, err := persistFleetStateRefreshToken(dir, state, "new-token")
	if err != nil {
		t.Fatalf("persistFleetStateRefreshToken: %v", err)
	}
	if updated.RefreshToken != "new-token" {
		t.Fatalf("updated token = %q, want new-token", updated.RefreshToken)
	}
	loaded, found, err := loadFleetState(dir)
	if err != nil {
		t.Fatalf("loadFleetState: %v", err)
	}
	if !found || loaded.RefreshToken != "new-token" {
		t.Fatalf("loaded state = %#v, found=%v; want rotated token", loaded, found)
	}
}

func TestPersistFleetStateRefreshTokenIgnoresEmptyToken(t *testing.T) {
	state := fleetState{
		ControllerURL: "https://controller.example",
		FleetID:       "f_test",
		NatsURL:       "tls://nats.example:4222",
		RefreshToken:  "existing-token",
	}
	updated, err := persistFleetStateRefreshToken(t.TempDir(), state, "")
	if err != nil {
		t.Fatalf("persistFleetStateRefreshToken: %v", err)
	}
	if updated.RefreshToken != state.RefreshToken {
		t.Fatalf("updated token = %q, want %q", updated.RefreshToken, state.RefreshToken)
	}
}

func TestLoadFleetStateRejectsMalformedData(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, fleetStateFileName), []byte("not-json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := loadFleetState(dir); err == nil {
		t.Fatal("malformed state must return an error")
	}
}

func TestApplyFleetStateRequiresRefreshToken(t *testing.T) {
	state := fleetState{
		ControllerURL: "https://controller.example",
		FleetID:       "f_test",
		NatsURL:       "tls://nats.example:4222",
	}
	if err := validateFleetState(state); err == nil {
		t.Fatal("state without refresh token must be rejected")
	}
}
