package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/aristoteleo/pantheon-fleet/internal/selfupdate"
)

const (
	autoUpdateFirstCheck = time.Minute
	autoUpdateInterval   = 6 * time.Hour
	autoUpdateRetry      = 10 * time.Minute
)

// selfUpdateSupported: a machine Node running a release build. Sandbox and
// pod Nodes run the Fleet baked into their image, so their image is what gets
// updated.
func selfUpdateSupported(kind string) bool {
	if kind != proto.KindMachine {
		return false
	}
	if _, err := selfupdate.ParseVersion(version); err != nil {
		return false // local development build
	}
	if _, err := selfupdate.Locate(); err != nil {
		return false
	}
	return true
}

// autoUpdate follows the release the Controller names: shortly after start,
// then every few hours, and again soon when an update waited for work to finish.
func autoUpdate(ctx context.Context, r *runner.Runner, controllerURL string) {
	wait := autoUpdateFirstCheck
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
		wait = autoUpdateInterval
		tag, err := latestFleetTag(ctx, controllerURL)
		if err != nil || tag == "" {
			continue
		}
		r.ApplyUpdate(ctx, tag, func(res selfupdate.Result, err error) {
			switch {
			case err != nil:
				fmt.Printf("%s Fleet update to %s failed: %v\n", clock(), tag, err)
			case res.Status == "deferred":
				wait = autoUpdateRetry
			}
		})
	}
}

func latestFleetTag(ctx context.Context, controllerURL string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(controllerURL, "/")+"/fleet/latest", nil)
	if err != nil {
		return "", err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("GET /fleet/latest: %s", resp.Status) // older Controller: no rollout
	}
	var out struct {
		Tag string `json:"tag"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 4096)).Decode(&out); err != nil {
		return "", err
	}
	return out.Tag, nil
}
