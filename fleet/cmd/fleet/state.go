package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const fleetStateFileName = "fleet-state.json"

// fleetState is the minimum assignment needed to restart a node without
// consuming another single-use join token. The refresh token is protected by
// the node key at the Controller and the file is private to the local user.
type fleetState struct {
	ControllerURL string   `json:"controller_url"`
	FleetID       string   `json:"fleet_id"`
	NatsURL       string   `json:"nats_url"`
	Relays        []string `json:"relays,omitempty"`
	RefreshToken  string   `json:"refresh_token"`
}

func saveFleetState(stateDir string, state fleetState) error {
	if err := validateFleetState(state); err != nil {
		return err
	}
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return err
	}
	b, err := json.Marshal(state)
	if err != nil {
		return fmt.Errorf("encode fleet state: %w", err)
	}
	return writePrivateFile(filepath.Join(stateDir, fleetStateFileName), append(b, '\n'))
}

func persistFleetStateRefreshToken(stateDir string, state fleetState, refreshToken string) (fleetState, error) {
	if refreshToken == "" {
		return state, nil
	}
	state.RefreshToken = refreshToken
	if err := saveFleetState(stateDir, state); err != nil {
		return state, err
	}
	return state, nil
}

func loadFleetState(stateDir string) (fleetState, bool, error) {
	path := filepath.Join(stateDir, fleetStateFileName)
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return fleetState{}, false, nil
	}
	if err != nil {
		return fleetState{}, false, fmt.Errorf("read fleet state: %w", err)
	}
	var state fleetState
	if err := json.Unmarshal(b, &state); err != nil {
		return fleetState{}, false, fmt.Errorf("decode fleet state: %w", err)
	}
	if err := validateFleetState(state); err != nil {
		return fleetState{}, false, err
	}
	return state, true, nil
}

func validateFleetState(state fleetState) error {
	switch {
	case state.ControllerURL == "":
		return fmt.Errorf("fleet state has no controller URL")
	case state.FleetID == "":
		return fmt.Errorf("fleet state has no fleet ID")
	case state.NatsURL == "":
		return fmt.Errorf("fleet state has no NATS URL")
	case state.RefreshToken == "":
		return fmt.Errorf("fleet state has no refresh token; join again with a fresh token")
	default:
		return nil
	}
}

func writePrivateFile(path string, data []byte) error {
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return err
	}
	return os.Chmod(path, 0o600)
}
