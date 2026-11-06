//go:build !windows

package codingagentsapp

// Fixes after the sixth Phase 3 check (docs/apps/team-chat.md, "Fixes after
// the sixth Phase 3 check"): a Team Chat turn holds a model token of its
// own that ends with the turn, never the user's model key; none of the
// user's own variables or arguments for the agent reach a Team Chat turn.

import (
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// gatewayStatus is the gateway's answer to one call with token.
func gatewayStatus(t *testing.T, method, url, token string) int {
	t.Helper()
	req, _ := http.NewRequest(method, url, strings.NewReader(`{"model":"m","max_tokens":1,"messages":[]}`))
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	return resp.StatusCode
}

// Finding 1: the turn's model credential is a token for this node's
// gateway, not the user's model key. While the turn runs it makes model
// calls (passed on with the node's key: the fake proxy answers only that
// key); it opens nothing else, nothing it can call makes or extends a
// token, and the moment the turn ends every call with it fails, the model
// call included. The user's model key appears nowhere in the turn.
func TestATeamTurnsModelTokenEndsWithTheTurn(t *testing.T) {
	probe := `case "$*" in *--help*) exit 0;; esac
d="$(dirname "$0")"
echo "$ANTHROPIC_BASE_URL $ANTHROPIC_AUTH_TOKEN" > "$d/claude-key.txt"
for p in /v1/messages /v1/chat/completions /v1/responses; do
	curl -sS -o /dev/null -w "$p %{http_code}\n" -X POST -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" \
		-H 'Content-Type: application/json' -d '{}' "$ANTHROPIC_BASE_URL$p" >> "$d/claude-during.txt"
done
for p in /key/generate /key/update /key/regenerate /user/update /openai/v1/files /v1/files; do
	curl -sS -o /dev/null -w "$p %{http_code}\n" -X POST -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" \
		-H 'Content-Type: application/json' -d '{"duration":"1h"}' "$ANTHROPIC_BASE_URL$p" >> "$d/claude-other.txt"
done
` + claudeDone
	dir, _ := platformNode(t, probe, codexDone)
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.teamCaller = func(map[string]any) (map[string]any, error) {
		return map[string]any{"code": float64(0), "text": "ok"}, nil
	}
	setSource(t, a, map[string]string{"claude": "pantheon", "codex": "pantheon"})
	for _, agent := range []string{"claude", "codex"} {
		folder := filepath.Join(t.TempDir(), agent)
		if r := teamSetup(folder, "brief", "", false); r["success"] != true {
			t.Fatal(r)
		}
		res := a.send(SendArgs{Agent: agent, Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
			Team: &TeamTurn{MID: "a1", Token: "tok", Handle: agent, Name: agent, Turn: "t1", Cred: "c1"}})
		if res["success"] != true {
			t.Fatal(res)
		}
		waitIdle(t, a, res["conversation_id"].(string))
		env := readText(t, filepath.Join(dir, agent+"-env.txt"))
		calls := readText(t, filepath.Join(dir, agent+"-calls.txt"))
		if strings.Contains(env, platformKey) || strings.Contains(calls, platformKey) {
			t.Errorf("%s's Team Chat turn holds the user's model key", agent)
		}
		if !regexp.MustCompile(`(?m)^(ANTHROPIC_AUTH_TOKEN|PANTHEON_LLM_API_KEY)=ptk_[0-9a-f]{48}$`).MatchString(env) {
			t.Errorf("%s's Team Chat turn has no model token of its own:\n%s", agent, env)
		}
		if agent == "codex" && !strings.Contains(calls, `base_url="http://127.0.0.1:`) {
			t.Errorf("codex's Team Chat turn is not pointed at the node's gateway:\n%s", calls)
		}
	}
	base, token, _ := strings.Cut(strings.TrimSpace(readText(t, filepath.Join(dir, "claude-key.txt"))), " ")
	if !strings.HasPrefix(base, "http://127.0.0.1:") || !strings.HasPrefix(token, "ptk_") {
		t.Fatalf("the turn's model service is %q", base)
	}
	if during := readText(t, filepath.Join(dir, "claude-during.txt")); strings.Count(during, " 200") != 3 {
		t.Errorf("the turn's model calls did not go through with its token:\n%s", during)
	}
	for _, line := range strings.Split(strings.TrimSpace(readText(t, filepath.Join(dir, "claude-other.txt"))), "\n") {
		if !strings.HasSuffix(line, " 404") {
			t.Errorf("the turn's token opened more than model calls: %s", line)
		}
	}
	// The turn is over: nothing works with its token, the model call first.
	for _, path := range []string{"/v1/messages", "/v1/chat/completions", "/v1/responses", "/key/generate"} {
		if got := gatewayStatus(t, http.MethodPost, base+path, token); got != http.StatusUnauthorized {
			t.Errorf("after the turn, %s with its token answered %d, not 401", path, got)
		}
	}
	if got := gatewayStatus(t, http.MethodGet, base+"/v1/models", token); got != http.StatusUnauthorized {
		t.Errorf("after the turn, /v1/models with its token answered %d, not 401", got)
	}
	// A Coding Agents turn (not Team Chat) still runs on the proxy and
	// the user's key, as before.
	id := a.send(SendArgs{Agent: "codex", Prompt: "hi", Auto: true})["conversation_id"].(string)
	waitIdle(t, a, id)
	if env := readText(t, filepath.Join(dir, "codex-env.txt")); !strings.Contains(env, "PANTHEON_LLM_API_KEY="+platformKey) {
		t.Errorf("a Coding Agents turn lost its model key:\n%s", env)
	}
}

// Finding 1, the gateway itself: a revoked or expired token is refused at
// once, a live one only for model calls with their method.
func TestATurnModelTokenIsGoodOnlyForModelCallsUntilRevoked(t *testing.T) {
	_, upstream := platformNode(t, claudeDone, codexDone)
	turn, revoke, err := codingagents.TurnModelKey("claude", codingagents.Platform{Base: upstream, Key: platformKey})
	if err != nil {
		t.Fatal(err)
	}
	if got := gatewayStatus(t, http.MethodPost, turn.Base+"/v1/messages?beta=true", turn.Key); got != http.StatusOK {
		t.Errorf("a live token's model call answered %d", got)
	}
	if got := gatewayStatus(t, http.MethodGet, turn.Base+"/v1/messages", turn.Key); got != http.StatusMethodNotAllowed {
		t.Errorf("GET /v1/messages answered %d", got)
	}
	if got := gatewayStatus(t, http.MethodPost, turn.Base+"/v1/messages", platformKey); got != http.StatusUnauthorized {
		t.Errorf("the user's key at the gateway answered %d", got)
	}
	revoke()
	revoke()
	if got := gatewayStatus(t, http.MethodPost, turn.Base+"/v1/messages", turn.Key); got != http.StatusUnauthorized {
		t.Errorf("a revoked token's model call answered %d", got)
	}
}

// Finding 2: none of the user's own variables for an agent reach a Team
// Chat turn: not the ones that load code (PYTHONPATH, NODE_PATH,
// GIT_SSH_COMMAND, GIT_CONFIG_*, npm_config_*, JAVA_TOOL_OPTIONS, LD_AUDIT;
// BASH_FUNC_*%% the settings refuse as a name), change a program's setup
// (XDG_CONFIG_HOME, CLAUDE_CODE_SHELL_PREFIX, OTEL_*, HTTPS_PROXY, HOME,
// SHELL) or carry a credential (*_TOKEN), and not a plain one either. A
// Coding Agents turn keeps them.
func TestATeamTurnGetsNoneOfTheUsersVariables(t *testing.T) {
	dir, _ := platformNode(t, claudeDone, codexDone)
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.teamCaller = func(map[string]any) (map[string]any, error) {
		return map[string]any{"code": float64(0), "text": "ok"}, nil
	}
	own := map[string]string{"PYTHONPATH": "/evil", "NODE_PATH": "/evil", "GIT_SSH_COMMAND": "/evil", "GIT_CONFIG_COUNT": "1",
		"GIT_CONFIG_KEY_0": "core.pager", "GIT_CONFIG_VALUE_0": "/evil", "GIT_EXTERNAL_DIFF": "/evil", "GIT_PAGER": "/evil",
		"npm_config_userconfig": "/evil", "JAVA_TOOL_OPTIONS": "-javaagent:/evil", "DOTNET_STARTUP_HOOKS": "/evil",
		"LD_AUDIT": "/evil", "XDG_CONFIG_HOME": "/evil", "CLAUDE_CODE_SHELL_PREFIX": "/evil",
		"OTEL_EXPORTER_OTLP_ENDPOINT": "http://evil", "HTTPS_PROXY": "http://evil", "HOME": "/evil", "SHELL": "/evil",
		"GITHUB_TOKEN": "ghp_evil", "PLAIN_SETTING": "evil"}
	if r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{
		"claude": {ModelSource: "pantheon", Env: own}, "codex": {ModelSource: "pantheon", Env: own}}}); r["success"] != true {
		t.Fatal(r)
	}
	for _, agent := range []string{"claude", "codex"} {
		folder := filepath.Join(t.TempDir(), agent)
		if r := teamSetup(folder, "brief", "", false); r["success"] != true {
			t.Fatal(r)
		}
		res := a.send(SendArgs{Agent: agent, Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
			Team: &TeamTurn{MID: "a1", Token: "tok", Handle: agent, Name: agent, Turn: "t1", Cred: "c1"}})
		if res["success"] != true {
			t.Fatal(res)
		}
		waitIdle(t, a, res["conversation_id"].(string))
		env := readText(t, filepath.Join(dir, agent+"-env.txt"))
		for _, bad := range []string{"evil", "PYTHONPATH", "NODE_PATH", "GIT_", "npm_config", "JAVA_TOOL", "DOTNET_", "LD_AUDIT",
			"XDG_CONFIG", "CLAUDE_CODE_SHELL", "OTEL_", "GITHUB_TOKEN", "PLAIN_SETTING"} {
			if strings.Contains(env, bad) {
				t.Errorf("%s's Team Chat turn environment has %s:\n%s", agent, bad, env)
			}
		}
	}
	id := a.send(SendArgs{Agent: "claude", Prompt: "hi", Auto: true})["conversation_id"].(string)
	waitIdle(t, a, id)
	if env := readText(t, filepath.Join(dir, "claude-env.txt")); !strings.Contains(env, "PLAIN_SETTING=evil") {
		t.Errorf("a Coding Agents turn lost the user's variables:\n%s", env)
	}
}

// Finding 3: the user's own arguments for an agent (Claude Code's
// --settings and --plugin-dir, which load hooks despite --setting-sources
// "", Codex's -c and --profile) are not added to a Team Chat turn; a
// Coding Agents turn keeps them.
func TestATeamTurnGetsNoneOfTheUsersArguments(t *testing.T) {
	dir, _ := platformNode(t, claudeDone, codexDone)
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.teamCaller = func(map[string]any) (map[string]any, error) {
		return map[string]any{"code": float64(0), "text": "ok"}, nil
	}
	settings := filepath.Join(t.TempDir(), "hooks.json")
	if err := os.WriteFile(settings, []byte(`{"hooks":{}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{
		"claude": {ModelSource: "pantheon", Args: []string{"--settings", settings, "--plugin-dir", "/plugins/hooked"}},
		"codex":  {ModelSource: "pantheon", Args: []string{"-c", `notify=["/evil"]`, "--profile", "evilprofile"}}}}); r["success"] != true {
		t.Fatal(r)
	}
	for _, agent := range []string{"claude", "codex"} {
		folder := filepath.Join(t.TempDir(), agent)
		if r := teamSetup(folder, "brief", "", false); r["success"] != true {
			t.Fatal(r)
		}
		res := a.send(SendArgs{Agent: agent, Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
			Team: &TeamTurn{MID: "a1", Token: "tok", Handle: agent, Name: agent, Turn: "t1", Cred: "c1"}})
		if res["success"] != true {
			t.Fatal(res)
		}
		waitIdle(t, a, res["conversation_id"].(string))
		calls := readText(t, filepath.Join(dir, agent+"-calls.txt"))
		for _, bad := range []string{"--settings", "--plugin-dir", "/plugins/hooked", "notify=", "/evil", "--profile", "evilprofile"} {
			if strings.Contains(calls, bad) {
				t.Errorf("%s's Team Chat turn was started with the user's argument %s:\n%s", agent, bad, calls)
			}
		}
		_ = os.Remove(filepath.Join(dir, agent+"-calls.txt"))
	}
	id := a.send(SendArgs{Agent: "claude", Prompt: "hi", Auto: true})["conversation_id"].(string)
	waitIdle(t, a, id)
	if calls := readText(t, filepath.Join(dir, "claude-calls.txt")); !strings.Contains(calls, "--plugin-dir /plugins/hooked") {
		t.Errorf("a Coding Agents turn lost the user's arguments:\n%s", calls)
	}
}
