package modelservices

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"sync"
	"time"
)

// Typed inference job metadata on a service's node, over the Fleet relay
// (pantheon/models/jobs.py InferenceSession, client.connect). The controller
// has no direct transport: a direct_only request fails before anything is sent.

var (
	jobIDRE      = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)
	artifactIDRE = regexp.MustCompile(`^[a-f0-9]{32}$`)
	jobStates    = map[any]bool{"queued": true, "running": true, "cancelling": true, "succeeded": true, "failed": true, "cancelled": true, "unknown": true}
)

const jobsPrefix = "/inference/jobs"

// Grants caches relay grants per service binding until shortly before expiry.
type Grants struct {
	mu sync.Mutex
	m  map[string]map[string]any
}

var relayClient = &http.Client{Timeout: 120 * time.Second,
	CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}

func (m *Manager) relayGrant(ctx context.Context, row Row) (map[string]any, error) {
	if row["state"] != "ready" || !truthy(row["binding"]) {
		return nil, fmt.Errorf("This model service is stopped or not ready; open Model Services")
	}
	key := m.nodes.fleet + "/" + string(Compact(row["binding"]))
	now := float64(time.Now().Unix())
	m.grants.mu.Lock()
	if m.grants.m == nil {
		m.grants.m = map[string]map[string]any{}
	}
	for k, g := range m.grants.m {
		if f, _ := strconv.ParseFloat(fmt.Sprint(g["expires"]), 64); f <= now+30 {
			delete(m.grants.m, k)
		}
	}
	grant := m.grants.m[key]
	m.grants.mu.Unlock()
	if grant != nil {
		return grant, nil
	}
	if err := m.dir.request(ctx, "POST", "/api/fleet/apps/workload-connect", row["binding"], &grant); err != nil {
		return nil, err
	}
	origin, err := url.Parse(strOf(grant["origin"]))
	if err != nil || origin.Scheme != "https" || origin.Hostname() == "" || origin.User != nil || origin.Path != "" ||
		origin.RawQuery != "" || origin.Fragment != "" || strOf(grant["access_token"]) == "" {
		return nil, fmt.Errorf("Invalid Fleet model transport grant")
	}
	if _, ok := grant["expires"].(json.Number); ok {
		m.grants.mu.Lock()
		if len(m.grants.m) < 64 {
			m.grants.m[key] = grant
		}
		m.grants.mu.Unlock()
	}
	return grant, nil
}

// jobRequest sends one bounded request on the service's data plane.
func (m *Manager) jobRequest(ctx context.Context, row Row, policy, method, path string, status int) (map[string]any, error) {
	switch policy {
	case "direct_only":
		return nil, fmt.Errorf("Authenticated direct transport is unavailable. Nothing was sent to Relay.")
	case "relay_allowed":
	default:
		return nil, fmt.Errorf("Unsupported model transport policy")
	}
	grant, err := m.relayGrant(ctx, row)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, method, strOf(grant["origin"])+path, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Model-Config", strOf(row["config_revision"]))
	req.Header.Set("Accept-Encoding", "identity")
	req.Header.Set("Authorization", "Bearer "+strOf(grant["access_token"]))
	resp, err := relayClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Fleet media request failed")
	}
	defer resp.Body.Close()
	if resp.StatusCode != status {
		// Provider/node errors can contain credentials or local paths.
		return nil, fmt.Errorf("Fleet media request returned HTTP %d", resp.StatusCode)
	}
	if e := resp.Header.Get("Content-Encoding"); e != "" && e != "identity" {
		return nil, fmt.Errorf("Compressed media responses are not supported")
	}
	const limit = 256 * 1024
	body, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if err != nil || len(body) > limit {
		return nil, fmt.Errorf("Media response exceeds its size limit")
	}
	var out map[string]any
	if err := decode(bytes.TrimSpace(body), &out); err != nil {
		return nil, fmt.Errorf("Invalid inference job response")
	}
	return out, nil
}

// InferenceJobs lists durable job metadata without inputs or results.
func (m *Manager) InferenceJobs(ctx context.Context, id, policy string) (map[string]any, error) {
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	value, err := m.jobRequest(ctx, row, policy, "GET", jobsPrefix, 200)
	if err != nil {
		return nil, err
	}
	jobs := listOf(value["jobs"])
	if intOf(value["protocol"]) != 1 || value["jobs"] == nil || len(jobs) > 128 {
		return nil, fmt.Errorf("Invalid inference job history")
	}
	for _, raw := range jobs {
		job := mapOf(raw)
		if _, has := job["result"]; job == nil || !jobStates[job["state"]] || has || !matches(jobIDRE, job["job_id"]) {
			return nil, fmt.Errorf("Invalid inference job history record")
		}
		job["ref"] = "fleet-job://" + id + "/" + strOf(job["job_id"])
	}
	return value, nil
}

// JobOperation observes, cancels, reconciles or removes one fixed job; it
// never resubmits.
func (m *Manager) JobOperation(ctx context.Context, ref, action, policy string) (map[string]any, error) {
	id, job, err := parseJobRef(ref)
	if err != nil {
		return nil, err
	}
	method, path := "GET", jobsPrefix+"/"+job
	switch action {
	case "status":
	case "cancel", "reconcile":
		method, path = "POST", path+"/"+action
	case "remove":
		method = "DELETE"
	default:
		return nil, fmt.Errorf("Unknown inference job operation")
	}
	row, err := m.dir.deployment(ctx, id)
	if err != nil {
		return nil, err
	}
	record, err := m.jobRequest(ctx, row, policy, method, path, 200)
	if err != nil {
		return nil, err
	}
	if action == "remove" {
		if !keys(record, "removed") || record["removed"] != true {
			return nil, fmt.Errorf("Inference job removal was not confirmed")
		}
		return map[string]any{"removed": true}, nil
	}
	if intOf(record["protocol"]) != 1 || record["job_id"] != job || !jobStates[record["state"]] {
		return nil, fmt.Errorf("Invalid inference job receipt")
	}
	if kind, mimes := mediaKind(record["operation"]); kind != "" && record["state"] == "succeeded" {
		artifacts := listOf(mapOf(record["result"])["artifacts"])
		if len(artifacts) != 1 {
			return nil, fmt.Errorf("Inference job did not return its media artifact")
		}
		a := mapOf(artifacts[0])
		if a == nil || a["state"] != "ready" || a["kind"] != kind || !matches(artifactIDRE, a["id"]) || a["purpose"] != "output" ||
			!mimes[strOf(a["mime"])] || !intIn(a["size"], 1, 512<<20) || !same(a["received"], a["size"]) || !matches(sha256RE, a["sha256"]) {
			return nil, fmt.Errorf("Invalid generated media receipt")
		}
		// An opaque id bound to THIS deployment; upstream URLs are never forwarded.
		bound := map[string]any{"ref": "fleet-artifact://" + id + "/" + strOf(a["id"])}
		for _, k := range []string{"id", "kind", "mime", "purpose", "size", "received", "state", "sha256"} {
			bound[k] = a[k]
		}
		mapOf(record["result"])["artifacts"] = []any{bound}
	}
	record["ref"] = "fleet-job://" + id + "/" + job
	return record, nil
}

func mediaKind(operation any) (string, map[string]bool) {
	switch operation {
	case "image":
		return "image", map[string]bool{"image/png": true, "image/jpeg": true, "image/webp": true}
	case "video":
		return "video", map[string]bool{"video/mp4": true}
	case "speech":
		return "audio", map[string]bool{"audio/wav": true, "audio/mpeg": true}
	}
	return "", nil
}
