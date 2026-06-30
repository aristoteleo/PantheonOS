// Package join is the Runner's client for the Controller: it exchanges the
// user's API key for the Fleet it belongs to plus how to reach the control
// plane and the relays.
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

// Join calls the Controller's /join with the key and returns the assignment.
func Join(ctx context.Context, controllerURL, key string) (proto.JoinResponse, error) {
	var out proto.JoinResponse
	body, _ := json.Marshal(proto.JoinRequest{Key: key})

	url := strings.TrimRight(controllerURL, "/") + "/join"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return out, err
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
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
