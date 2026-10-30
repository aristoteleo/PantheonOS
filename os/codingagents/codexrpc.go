package codingagents

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// codexCall runs `codex app-server`, Codex's own JSON-RPC interface, for one
// request. Codex reads its sign-in itself; PantheonOS sees only the answer.
// It returns by ctx's deadline: the server runs in a process group of its
// own, all of which is killed then (an npm launcher's child would otherwise
// keep its output open).
func codexCall(ctx context.Context, path string, env []string, method string, params any) (json.RawMessage, error) {
	cmd := exec.CommandContext(ctx, path, "app-server")
	cmd.Env = env
	cmd.Cancel = func() error { killGroup(cmd); return nil }
	cmd.WaitDelay = time.Second
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	detach(cmd)
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	defer func() {
		_ = stdin.Close()
		killGroup(cmd)
		_ = cmd.Wait()
	}()
	send := func(v any) error {
		b, _ := json.Marshal(v)
		_, err := stdin.Write(append(b, '\n'))
		return err
	}
	if err := send(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "initialize",
		"params": map[string]any{"clientInfo": map[string]any{"name": "pantheonos", "title": "PantheonOS", "version": "1"}}}); err != nil {
		return nil, err
	}
	_ = send(map[string]any{"jsonrpc": "2.0", "method": "initialized"})
	req := map[string]any{"jsonrpc": "2.0", "id": 2, "method": method}
	if params != nil {
		req["params"] = params
	}
	if err := send(req); err != nil {
		return nil, err
	}
	type answer struct {
		result json.RawMessage
		err    error
	}
	got := make(chan answer, 1)
	go func() {
		sc := bufio.NewScanner(io.LimitReader(stdout, 64<<20))
		sc.Buffer(make([]byte, 64*1024), 16<<20)
		for sc.Scan() {
			var m struct {
				ID     json.RawMessage `json:"id"`
				Result json.RawMessage `json:"result"`
				Error  *struct {
					Message string `json:"message"`
				} `json:"error"`
			}
			if json.Unmarshal(sc.Bytes(), &m) != nil || string(m.ID) != "2" {
				continue
			}
			if m.Error != nil {
				got <- answer{err: errors.New(m.Error.Message)}
			} else {
				got <- answer{result: m.Result}
			}
			return
		}
		got <- answer{err: errors.New("codex app-server closed without an answer")}
	}()
	select {
	case a := <-got:
		if a.err != nil && ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return a.result, a.err
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

// Quota is an account's limits as an agent reports them.
type Quota struct {
	Limits map[string]Limit `json:"limits,omitempty"`
	Plan   string           `json:"plan,omitempty"`
	At     float64          `json:"at"`
	// Refused is why the provider will not serve this account through the
	// agent, when it says so: signed in, but as good as not.
	Refused string `json:"refused,omitempty"`
}

type codexWindow struct {
	UsedPercent        float64 `json:"usedPercent"`
	WindowDurationMins int     `json:"windowDurationMins"`
	ResetsAt           int64   `json:"resetsAt"`
}

// windowName names a quota window by its length, as Claude Code names its own.
func windowName(mins int) string {
	switch mins {
	case 300:
		return "five_hour"
	case 10080:
		return "seven_day"
	}
	if mins%1440 == 0 {
		return strconv.Itoa(mins/1440) + "_day"
	}
	return strconv.Itoa(mins) + "_min"
}

// CodexQuota asks Codex for the signed-in account's limits.
func CodexQuota(ctx context.Context, path string, env []string) (Quota, error) {
	raw, err := codexCall(ctx, path, env, "account/rateLimits/read", nil)
	if err != nil {
		return Quota{}, err
	}
	var r struct {
		RateLimits struct {
			Primary   *codexWindow `json:"primary"`
			Secondary *codexWindow `json:"secondary"`
			PlanType  string       `json:"planType"`
		} `json:"rateLimits"`
	}
	if err := json.Unmarshal(raw, &r); err != nil {
		return Quota{}, fmt.Errorf("codex limits: %w", err)
	}
	q := Quota{Limits: map[string]Limit{}, Plan: r.RateLimits.PlanType, At: float64(time.Now().Unix())}
	for _, w := range []*codexWindow{r.RateLimits.Primary, r.RateLimits.Secondary} {
		if w != nil && w.WindowDurationMins > 0 {
			q.Limits[windowName(w.WindowDurationMins)] = Limit{Used: w.UsedPercent / 100, ResetsAt: w.ResetsAt}
		}
	}
	return q, nil
}

// NativeSession is a session an agent keeps itself, wherever it started.
type NativeSession struct {
	Agent     string  `json:"agent"`
	ID        string  `json:"id"`
	Title     string  `json:"title"`
	Preview   string  `json:"preview,omitempty"`
	Cwd       string  `json:"cwd,omitempty"`
	Branch    string  `json:"branch,omitempty"`
	Model     string  `json:"model,omitempty"`
	Source    string  `json:"source,omitempty"` // cli, exec, …
	CreatedAt float64 `json:"created_at,omitempty"`
	UpdatedAt float64 `json:"updated_at"`
}

// CodexSessions lists Codex's threads, newest first.
func CodexSessions(ctx context.Context, path string, env []string, limit int) ([]NativeSession, error) {
	raw, err := codexCall(ctx, path, env, "thread/list", map[string]any{"limit": limit})
	if err != nil {
		return nil, err
	}
	var r struct {
		Data []struct {
			ID        string  `json:"id"`
			Name      string  `json:"name"`
			Preview   string  `json:"preview"`
			Cwd       string  `json:"cwd"`
			Model     string  `json:"model"`
			Source    string  `json:"source"`
			CreatedAt float64 `json:"createdAt"`
			UpdatedAt float64 `json:"updatedAt"`
			GitInfo   *struct {
				Branch string `json:"branch"`
			} `json:"gitInfo"`
		} `json:"data"`
	}
	if err := json.Unmarshal(raw, &r); err != nil {
		return nil, fmt.Errorf("codex threads: %w", err)
	}
	out := make([]NativeSession, 0, len(r.Data))
	for _, t := range r.Data {
		s := NativeSession{Agent: "codex", ID: t.ID, Title: firstNonEmpty(t.Name, firstLine(t.Preview)), Preview: firstLine(t.Preview),
			Cwd: t.Cwd, Model: t.Model, Source: t.Source, CreatedAt: t.CreatedAt, UpdatedAt: t.UpdatedAt}
		if t.GitInfo != nil {
			s.Branch = t.GitInfo.Branch
		}
		out = append(out, s)
	}
	return out, nil
}

// CodexSkill is a skill Codex loads for a folder.
type CodexSkill struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Path        string `json:"path"`
	Scope       string `json:"scope"` // user, repo, system, admin
	Plugin      string `json:"plugin,omitempty"`
}

// CodexSkills asks Codex which skills it loads in cwd (the user's, the
// repository's, its own and its plugins'), the enabled ones.
func CodexSkills(ctx context.Context, path string, env []string, cwd string) ([]CodexSkill, error) {
	raw, err := codexCall(ctx, path, env, "skills/list", map[string]any{"cwds": []string{cwd}})
	if err != nil {
		return nil, err
	}
	var r struct {
		Data []struct {
			Skills []struct {
				Name        string  `json:"name"`
				Description string  `json:"description"`
				Path        string  `json:"path"`
				Scope       string  `json:"scope"`
				Enabled     bool    `json:"enabled"`
				PluginID    *string `json:"pluginId"`
			} `json:"skills"`
		} `json:"data"`
	}
	if err := json.Unmarshal(raw, &r); err != nil {
		return nil, fmt.Errorf("codex skills: %w", err)
	}
	out := []CodexSkill{}
	for _, d := range r.Data {
		for _, s := range d.Skills {
			if !s.Enabled {
				continue
			}
			k := CodexSkill{Name: s.Name, Description: s.Description, Path: s.Path, Scope: s.Scope}
			if s.PluginID != nil {
				// A plugin id is <plugin>@<marketplace>.
				k.Plugin, _, _ = strings.Cut(*s.PluginID, "@")
			}
			out = append(out, k)
		}
	}
	return out, nil
}
