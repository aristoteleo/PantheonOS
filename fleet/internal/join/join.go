// Package join is the Runner's client for the Controller: it exchanges the
// user's API key for the Fleet it belongs to plus how to reach the control
// plane and the relays, and refreshes the short-lived credential via /token.
package join

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

var httpClient = &http.Client{Timeout: 10 * time.Second}

// Join calls the Controller's /join with the key and the node's public key, and
// returns the assignment (fleet, nats url, short-lived creds, refresh token).
func Join(ctx context.Context, controllerURL, key, nodePub string) (proto.JoinResponse, error) {
	var out proto.JoinResponse
	body, _ := json.Marshal(proto.JoinRequest{Key: key, NodePub: nodePub})

	url := strings.TrimRight(controllerURL, "/") + "/join"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return out, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return out, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return out, fmt.Errorf("controller join failed: %s", resp.Status)
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return out, err
	}
	if out.FleetID == "" || out.NatsURL == "" {
		return out, fmt.Errorf("controller returned an incomplete assignment")
	}
	return out, nil
}

// Refresh calls the Controller's /token with a refresh token + proof-of-possession
// and returns fresh credentials.
func Refresh(ctx context.Context, controllerURL string, tr proto.TokenRequest) (proto.TokenResponse, error) {
	var out proto.TokenResponse
	body, _ := json.Marshal(tr)

	url := strings.TrimRight(controllerURL, "/") + "/token"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return out, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return out, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return out, fmt.Errorf("controller token failed: %s", resp.Status)
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return out, err
	}
	if out.Creds == "" {
		return out, fmt.Errorf("controller returned no credentials")
	}
	return out, nil
}
