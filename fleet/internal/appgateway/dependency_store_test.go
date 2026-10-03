//go:build !windows

package appgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

const persistenceControl = "owner-controller-secret-for-fixture"

func persistedGateway(t *testing.T, root string) (*Gateway, http.Handler) {
	t.Helper()
	g, err := New("apps.test", persistenceControl, []string{"https://atrium.test"},
		func(context.Context, Binding, string, string) error { return nil },
		func(context.Context, Binding) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	g.SetDependencyDispatch(func(context.Context, apptransport.InstanceIdentity, string) error { return nil },
		func(context.Context, Binding, string, json.RawMessage, int) (json.RawMessage, error) {
			return json.RawMessage(`{"ok":true}`), nil
		})
	if root != "" {
		if err := g.OpenDependencyStore(root); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { g.CloseDependencyStore() })
	}
	mux := http.NewServeMux()
	g.Register(mux)
	return g, g.Handler(mux)
}
func persistenceRequest() DependencyRequest {
	return DependencyRequest{Operation: "allocate-one", Consumer: apptransport.InstanceIdentity{
		Fleet: "owner", Node: "consumer-node", Instance: "consumer", Revision: strings.Repeat("a", 64), Generation: 2},
		Provider: Binding{Fleet: "owner", Node: "provider-node", Instance: "provider", Revision: strings.Repeat("b", 64), Generation: 3, Component: "backend", Port: "http"},
		AppID:    "files", Methods: map[string]RPCMethod{"read": {Arguments: []string{"path"}, Bound: map[string]json.RawMessage{"workspace": json.RawMessage(`"project-a"`)}}},
		Expires: time.Now().Add(5 * time.Minute).Unix(), Timeout: 5}
}
func persistenceCall(h http.Handler, method, host, path, token string, value any) *httptest.ResponseRecorder {
	raw, _ := json.Marshal(value)
	request := httptest.NewRequest(method, "http://"+host+path, bytes.NewReader(raw))
	request.Header.Set("Authorization", "Bearer "+token)
	response := httptest.NewRecorder()
	h.ServeHTTP(response, request)
	return response
}
func persistedIssue(t *testing.T, h http.Handler, q DependencyRequest) map[string]any {
	t.Helper()
	response := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, q)
	if response.Code != 200 {
		t.Fatalf("issue: %d %s", response.Code, response.Body.String())
	}
	var value map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func TestDependencyPersistenceReplayRestartRenewAndRevoke(t *testing.T) {
	root := filepath.Join(t.TempDir(), "journal")
	g, h := persistedGateway(t, root)
	q := persistenceRequest()
	issued := persistedIssue(t, h, q)
	originalExpiry := issued["expires"]
	q.Expires += 100
	replay := persistedIssue(t, h, q)
	if replay["access_token"] != issued["access_token"] || replay["expires"] != originalExpiry {
		t.Fatal("retry rotated or renewed grant")
	}
	changed := q
	changed.Timeout++
	if response := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, changed); response.Code != 409 {
		t.Fatal("changed policy accepted", response.Code)
	}
	another := q
	another.Consumer.Instance = "other-consumer"
	if persistedIssue(t, h, another)["access_token"] == issued["access_token"] {
		t.Fatal("consumer identity aliased")
	}
	renewal := map[string]any{"fleet_id": "owner", "grant_id": issued["grant_id"], "expires": q.Expires}
	if response := persistenceCall(h, "PATCH", "control.test", "/apps/dependencies", persistenceControl, renewal); response.Code != 200 {
		t.Fatal(response.Code)
	}
	g.CloseDependencyStore()
	g, h = persistedGateway(t, root)
	restored := persistedIssue(t, h, q)
	if restored["access_token"] != issued["access_token"] || restored["expires"] != float64(q.Expires) {
		t.Fatal("renewal did not survive restart")
	}
	host := Host(q.Provider.Instance, q.Provider.Component, q.Provider.Port, q.Provider.Generation, g.domain)
	if response := persistenceCall(h, "POST", host, "/rpc", issued["access_token"].(string), map[string]any{"method": "read", "args": map[string]string{"path": "one"}}); response.Code != 200 {
		t.Fatal("restored credential unusable", response.Code)
	}
	revoke := map[string]any{"fleet_id": "owner", "grant_id": issued["grant_id"]}
	if response := persistenceCall(h, "DELETE", "control.test", "/apps/dependencies", persistenceControl, revoke); response.Code != 204 {
		t.Fatal(response.Code)
	}
	g.CloseDependencyStore()
	g, h = persistedGateway(t, root)
	if response := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, q); response.Code != 410 {
		t.Fatal("revoked operation revived", response.Code)
	}
	if response := persistenceCall(h, "PATCH", "control.test", "/apps/dependencies", persistenceControl, renewal); response.Code != 410 {
		t.Fatal("revoked renewal revived", response.Code)
	}
	for _, v := range g.dependencyStore.records {
		if v.ID == issued["grant_id"] && (v.Request != nil || v.Token != "") {
			t.Fatal("revocation retained bearer")
		}
	}
}

func TestDependencyPersistenceSingleflightAndExpiredIntent(t *testing.T) {
	root := filepath.Join(t.TempDir(), "journal")
	g, h := persistedGateway(t, root)
	q := persistenceRequest()
	var wg sync.WaitGroup
	results := make(chan string, 16)
	for range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, q)
			results <- fmt.Sprint(r.Code) + r.Body.String()
		}()
	}
	wg.Wait()
	close(results)
	unique := map[string]bool{}
	for r := range results {
		unique[r] = true
		if !strings.HasPrefix(r, "200") {
			t.Fatal(r)
		}
	}
	if len(unique) != 1 || len(g.dependencyStore.records) != 1 {
		t.Fatal("duplicate concurrent grants")
	}
	key := dependencyOperation(q)
	record := g.dependencyStore.records[key]
	record.Expires = time.Now().Add(-time.Minute).Unix()
	record.Request.Expires = record.Expires
	raw, _ := json.Marshal(record)
	g.CloseDependencyStore()
	if err := os.WriteFile(filepath.Join(root, key+".json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	g, h = persistedGateway(t, root)
	if len(g.dependencies) != 0 {
		t.Fatal("expired grant loaded")
	}
	compacted, err := os.ReadFile(filepath.Join(root, key+".json"))
	if err != nil || bytes.Contains(compacted, []byte(record.Token)) || g.dependencyStore.records[key].Request != nil {
		t.Fatal("expired credential not compacted")
	}
	if r := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, q); r.Code != 410 {
		t.Fatal("expired operation reminted", r.Code)
	}
}

func TestDependencyJournalOwnershipCorruptionAndWriteFailure(t *testing.T) {
	root := filepath.Join(t.TempDir(), "journal")
	g, h := persistedGateway(t, root)
	q := persistenceRequest()
	issued := persistedIssue(t, h, q)
	second, _ := persistedGateway(t, "")
	if second.OpenDependencyStore(root) == nil {
		t.Fatal("concurrent writer allowed")
	}
	if info, err := os.Stat(root); err != nil || info.Mode().Perm() != 0700 {
		t.Fatal("journal is not private")
	}
	key := dependencyOperation(q)
	filename := filepath.Join(root, key+".json")
	if info, err := os.Stat(filename); err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("credential is not private")
	}
	// Force rename failure without a root-only chmod test: destination is now
	// a directory. No authorization succeeds after an uncertain journal write.
	if err := os.Remove(filename); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filename, 0700); err != nil {
		t.Fatal(err)
	}
	renewal := map[string]any{"fleet_id": "owner", "grant_id": issued["grant_id"], "expires": q.Expires + 1}
	if r := persistenceCall(h, "PATCH", "control.test", "/apps/dependencies", persistenceControl, renewal); r.Code != 503 {
		t.Fatal("failed write accepted", r.Code)
	}
	host := Host(q.Provider.Instance, q.Provider.Component, q.Provider.Port, q.Provider.Generation, g.domain)
	if r := persistenceCall(h, "POST", host, "/rpc", issued["access_token"].(string), map[string]any{}); r.Code != 503 {
		t.Fatal("poisoned journal still admits", r.Code)
	}
	g.CloseDependencyStore()
	if err := second.OpenDependencyStore(root); err == nil {
		t.Fatal("corruption silently reset")
	}
	if _, err := os.Stat(filename); err != nil {
		t.Fatal("corruption evidence removed")
	}
}

func TestIdempotentGrantRequiresPersistence(t *testing.T) {
	_, h := persistedGateway(t, "")
	if r := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, persistenceRequest()); r.Code != 503 {
		t.Fatal("durability silently downgraded", r.Code)
	}
}

// Exercise OS lock release and recovery with no CloseDependencyStore call. The
// issuing process exits before its HTTP result can reach the owning caller.
func TestDependencyJournalProcessRecovery(t *testing.T) {
	if root := os.Getenv("PANTHEON_DEPENDENCY_RECOVERY_CHILD"); root != "" {
		_, h := persistedGateway(t, filepath.Join(root, "nested", "journal"))
		q := persistenceRequest()
		issued := persistedIssue(t, h, q)
		data, err := json.Marshal(struct {
			Request DependencyRequest `json:"request"`
			Issued  map[string]any    `json:"issued"`
		}{q, issued})
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, "receipt.json"), data, 0600); err != nil {
			t.Fatal(err)
		}
		os.Exit(0) // Deliberately skip cleanup; nothing lives in the parent process.
	}
	root := t.TempDir()
	command := exec.Command(os.Args[0], "-test.run=^TestDependencyJournalProcessRecovery$")
	command.Env = append(os.Environ(), "PANTHEON_DEPENDENCY_RECOVERY_CHILD="+root)
	if result, err := command.CombinedOutput(); err != nil {
		t.Fatalf("child failed: %v %s", err, result)
	}
	data, err := os.ReadFile(filepath.Join(root, "receipt.json"))
	if err != nil {
		t.Fatal(err)
	}
	var receipt struct {
		Request DependencyRequest `json:"request"`
		Issued  map[string]any    `json:"issued"`
	}
	if err := json.Unmarshal(data, &receipt); err != nil {
		t.Fatal(err)
	}
	g, h := persistedGateway(t, filepath.Join(root, "nested", "journal"))
	q := receipt.Request
	q.Expires += 60
	issued := persistedIssue(t, h, q)
	if issued["access_token"] != receipt.Issued["access_token"] || issued["expires"] != receipt.Issued["expires"] {
		t.Fatal("lost reply recovery changed authorization")
	}
	// A persisted token still requires a live, exact consumer at every call.
	g.consumerCheck = func(context.Context, apptransport.InstanceIdentity, string) error {
		return fmt.Errorf("consumer replaced")
	}
	host := Host(q.Provider.Instance, q.Provider.Component, q.Provider.Port, q.Provider.Generation, g.domain)
	body := map[string]any{"method": "read", "args": map[string]string{"path": "one"}}
	if r := persistenceCall(h, "POST", host, "/rpc", issued["access_token"].(string), body); r.Code != 409 {
		t.Fatal("restored token bypassed live identity", r.Code)
	}
	if r := persistenceCall(h, "POST", "control.test", "/apps/dependencies", persistenceControl, q); r.Code != 409 {
		t.Fatal("restored replay bypassed live identity", r.Code)
	}
}

func TestDependencyJournalRejectsDuplicateCredentialIdentity(t *testing.T) {
	root := filepath.Join(t.TempDir(), "journal")
	g, h := persistedGateway(t, root)
	q := persistenceRequest()
	persistedIssue(t, h, q)
	record := g.dependencyStore.records[dependencyOperation(q)]
	g.CloseDependencyStore()
	q.Consumer.Instance = "another-consumer"
	record.Request, record.Key, record.Policy = &q, dependencyOperation(q), dependencyPolicy(q)
	data, err := json.Marshal(record)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, record.Key+".json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	next, _ := persistedGateway(t, "")
	if next.OpenDependencyStore(root) == nil {
		t.Fatal("ambiguous credential restored")
	}
}
