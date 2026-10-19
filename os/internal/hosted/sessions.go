package hosted

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"path/filepath"
	"time"
)

// Provider resource sessions (e.g. a Shell session per Agent owner), held for
// the logical owner and released when it retires (formerly
// pantheonos/apps/resource_sessions.py).

var terminalSessions = map[string]bool{"released": true, "expired": true, "lost": true, "failed": true}

type sessionReceipt struct {
	OwnerRef  string `json:"owner_ref"`
	LeaseID   string `json:"lease_id"`
	Kind      string `json:"kind"`
	SessionID string `json:"session_id"`
	State     string `json:"state"`
	Expires   int64  `json:"expires"`
}

type sessionRecord struct {
	Protocol int             `json:"protocol"`
	Recipe   sessionRecipe   `json:"recipe"`
	Owner    string          `json:"owner"`
	LeaseID  string          `json:"lease_id"`
	Phase    string          `json:"phase"` // acquiring, active, releasing, terminal
	Receipt  *sessionReceipt `json:"receipt"`
	Reason   string          `json:"reason,omitempty"`
}

func sessionKey(consumer identity, operation string) string {
	sum := sha256.Sum256([]byte(consumer.NodeID + "\x00" + operation))
	return hex.EncodeToString(sum[:])
}

func leaseID(owner, key string) string {
	sum := sha256.Sum256([]byte("resource-session\x00" + owner + "\x00" + key))
	return hex.EncodeToString(sum[:])
}

// consumerState classifies the consumer of a session record.
func consumerState(record *sessionRecord, state map[string]any) string {
	who := record.Recipe.Consumer
	in := instanceOf(state, who.InstanceID)
	if in == nil {
		return "terminal"
	}
	generation := int64(num(in["generation"]))
	if generation < who.Generation {
		return "unknown" // a stale inventory cannot prove this consumer ended
	}
	if str(in["digest"]) != who.Revision || generation != who.Generation {
		return "terminal"
	}
	switch s := str(in["state"]); {
	case s == "stopped" || s == "removed":
		return "terminal"
	case liveStates[s] || s == "draining" || s == "stop_blocked":
		return "active"
	}
	return "unknown"
}

// providerCurrent reports whether the session's exact provider still runs.
func providerCurrent(record *sessionRecord, state map[string]any) (bool, error) {
	who := record.Recipe.Provider
	in := instanceOf(state, who.InstanceID)
	if in == nil {
		return false, nil
	}
	generation := int64(num(in["generation"]))
	if generation < who.Generation {
		return false, fmt.Errorf("resource-session provider generation is not current")
	}
	return str(in["app_id"]) == record.Recipe.AppID && str(in["digest"]) == who.Revision && generation == who.Generation &&
		str(in["state"]) != "stopped" && str(in["state"]) != "removed", nil
}

// sessionInvoke calls the provider's resource-session@1 method.
func (a *Allocator) sessionInvoke(ctx context.Context, fleet string, record *sessionRecord, action string) (*sessionReceipt, error) {
	r := record.Recipe
	args := map[string]any{"owner_ref": r.OwnerRef, "lease_id": record.LeaseID}
	if action == "acquire" || action == "renew" {
		args["ttl_seconds"] = 900
	}
	if action == "acquire" {
		args["kind"] = r.Kind
	}
	envelope, err := lifecycle(ctx, a.Bus, fleet, r.Provider.NodeID, "invoke", map[string]any{"app_id": r.AppID,
		"instance_id": r.Provider.InstanceID, "revision": r.Provider.Revision, "generation": r.Provider.Generation,
		"timeout_seconds": 10, "payload": map[string]any{"method": "resource_session_" + action, "args": args, "timeout_s": 10}})
	if err != nil {
		return nil, err
	}
	response, _ := envelope["response"].(map[string]any)
	if response == nil || response["success"] != true {
		return nil, fmt.Errorf("resource session operation was not acknowledged")
	}
	raw, _ := json.Marshal(response["result"])
	var value sessionReceipt
	if err := json.Unmarshal(raw, &value); err != nil || value.OwnerRef != r.OwnerRef || value.LeaseID != record.LeaseID ||
		value.Kind != r.Kind || (!terminalSessions[value.State] && value.State != "active" && value.State != "closing") ||
		value.Expires < 0 || len(value.SessionID) > 256 || value.State == "active" && value.SessionID == "" {
		return nil, fmt.Errorf("provider returned an invalid resource-session receipt")
	}
	if previous := record.Receipt; previous != nil {
		if previous.SessionID != "" && previous.SessionID != value.SessionID {
			return nil, fmt.Errorf("provider attempted to replace the original resource session")
		}
		if terminalSessions[previous.State] && value != *previous {
			return nil, fmt.Errorf("provider attempted to revive a terminal resource session")
		}
	}
	now := a.now().Unix()
	if value.State == "active" && (value.Expires < now-30 || value.Expires > now+930) {
		return nil, fmt.Errorf("provider returned an invalid resource-session lifetime")
	}
	record.Receipt = &value
	if terminalSessions[value.State] {
		record.Phase = "terminal"
	} else if record.Phase != "releasing" {
		record.Phase = "active"
	}
	return &value, nil
}

func (a *Allocator) sessionPath(fleet string, consumer identity, operation string) (string, error) {
	dir, err := a.dir(fleet, "sessions")
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, sessionKey(consumer, operation)+".json"), nil
}

// acquire holds (or observes) the session for one logical owner and alias.
func (a *Allocator) acquire(ctx context.Context, fleet string, recipe sessionRecipe) (*sessionRecord, error) {
	path, err := a.sessionPath(fleet, recipe.Consumer, recipe.OperationID)
	if err != nil {
		return nil, err
	}
	unlock := a.lock(path)
	defer unlock()
	var record sessionRecord
	exists, err := readJournal(path, &record)
	if err != nil {
		return nil, err
	}
	if exists {
		want, _ := json.Marshal(recipe)
		have, _ := json.Marshal(record.Recipe)
		if string(want) != string(have) {
			return nil, fmt.Errorf("resource-session operation belongs to a different recipe")
		}
	} else {
		if len(journals(filepath.Dir(path))) >= 4096 {
			return nil, fmt.Errorf("resource-session journal is full")
		}
		state, err := a.lifecycleStatus(ctx, fleet, recipe.Consumer.NodeID)
		if err != nil {
			return nil, err
		}
		key := filepath.Base(path[:len(path)-len(".json")])
		record = sessionRecord{Protocol: 1, Recipe: recipe, Owner: fleet, LeaseID: leaseID(fleet, key), Phase: "acquiring"}
		if consumerState(&record, state) != "active" {
			return nil, fmt.Errorf("resource session requires a live consumer")
		}
		manifest, err := a.installedManifest(ctx, fleet, recipe.Provider)
		if err != nil {
			return nil, err
		}
		if manifest["id"] != recipe.AppID || num(manifest["apiVersion"]) != 2 {
			return nil, fmt.Errorf("resource session requires the declared provider App")
		}
		methods := map[string]rule{}
		for _, name := range []string{"resource_session_acquire", "resource_session_get", "resource_session_renew", "resource_session_release"} {
			args := []string{"owner_ref", "lease_id"}
			switch name {
			case "resource_session_acquire":
				args = append(args, "kind", "ttl_seconds")
			case "resource_session_renew":
				args = append(args, "ttl_seconds")
			}
			methods[name] = rule{Arguments: args, Bound: map[string]json.RawMessage{}}
		}
		if err := checkMethods([]any{"resource-session@1"}, manifest, methods); err != nil {
			return nil, err
		}
		// Write intent before the first provider mutation.
		if err := writeJournal(path, record); err != nil {
			return nil, err
		}
	}
	if record.Phase == "terminal" {
		return &record, nil
	}
	state, err := a.lifecycleStatus(ctx, fleet, recipe.Consumer.NodeID)
	if err != nil {
		return nil, err
	}
	if consumerState(&record, state) != "active" {
		return nil, fmt.Errorf("original consumer is unavailable; inspect or release its session")
	}
	providerState, err := a.lifecycleStatus(ctx, fleet, recipe.Provider.NodeID)
	if err != nil {
		return nil, err
	}
	if ok, err := providerCurrent(&record, providerState); err != nil || !ok {
		return nil, fmt.Errorf("original provider is unavailable; no replacement session was created")
	}
	if record.Phase == "releasing" {
		return nil, fmt.Errorf("resource session is being released")
	}
	action := "get"
	if record.Phase == "acquiring" {
		action = "acquire"
	}
	if _, err := a.sessionInvoke(ctx, fleet, &record, action); err != nil {
		return nil, err
	}
	return &record, writeJournal(path, record)
}

// release ends one session (idempotently); a missing journal was never acquired.
func (a *Allocator) release(ctx context.Context, fleet string, consumer identity, operation string) (*sessionRecord, error) {
	path, err := a.sessionPath(fleet, consumer, operation)
	if err != nil {
		return nil, err
	}
	unlock := a.lock(path)
	defer unlock()
	var record sessionRecord
	exists, err := readJournal(path, &record)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, nil
	}
	if record.Phase == "terminal" {
		return &record, nil
	}
	record.Phase = "releasing"
	if err := writeJournal(path, record); err != nil {
		return nil, err
	}
	_, err = a.maintainSession(ctx, fleet, path, &record, true)
	return &record, err
}

// maintainSession renews a live owner's session, releases an ended one.
func (a *Allocator) maintainSession(ctx context.Context, fleet, path string, record *sessionRecord, release bool) (string, error) {
	renew := false
	if !release && record.Phase != "releasing" {
		state, err := a.lifecycleStatus(ctx, fleet, record.Recipe.Consumer.NodeID)
		if err != nil {
			return "deferred", err
		}
		switch consumerState(record, state) {
		case "unknown":
			return "deferred", nil
		case "terminal":
			record.Phase = "releasing"
			if err := writeJournal(path, record); err != nil {
				return "deferred", err
			}
		case "active":
			renew = true
		}
	}
	providerState, err := a.lifecycleStatus(ctx, fleet, record.Recipe.Provider.NodeID)
	if err != nil {
		return "deferred", err
	}
	current, err := providerCurrent(record, providerState)
	if err != nil {
		return "deferred", err
	}
	if !current {
		// Never call a replacement provider, never claim the old one cleaned up.
		record.Phase, record.Reason = "terminal", "provider_unavailable"
		return "lost", writeJournal(path, record)
	}
	outcome := "observed"
	if record.Phase == "releasing" {
		if _, err := a.sessionInvoke(ctx, fleet, record, "release"); err != nil {
			return "deferred", err
		}
		outcome = "deferred"
		if record.Phase == "terminal" {
			outcome = "released"
		}
	} else {
		r, err := a.sessionInvoke(ctx, fleet, record, "get")
		if err != nil {
			return "deferred", err
		}
		if record.Phase == "terminal" {
			outcome = "terminal"
		}
		if renew && r.State == "active" && r.Expires <= a.now().Add(300*time.Second).Unix() {
			if _, err := a.sessionInvoke(ctx, fleet, record, "renew"); err != nil {
				return "deferred", err
			}
			outcome = "renewed"
		}
	}
	return outcome, writeJournal(path, record)
}
