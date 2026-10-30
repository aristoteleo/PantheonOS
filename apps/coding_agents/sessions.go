package codingagentsapp

import (
	"context"
	"sort"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// SessionRow is one of an agent's own sessions, with the PantheonOS
// conversation that continues it, if any.
type SessionRow struct {
	codingagents.NativeSession
	ConversationID string `json:"conversation_id,omitempty"`
	// Known: started or resumed in a PantheonOS terminal, or imported.
	Known bool `json:"known,omitempty"`
}

// sessions lists the sessions the agents keep themselves on this node —
// also those started outside PantheonOS — newest first. agent narrows the
// list to one agent.
func (a *App) sessions(agent string, limit int) map[string]any {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	var rows []codingagents.NativeSession
	var errs []string
	if agent == "" || agent == "claude" {
		if _, err := resolve("claude"); err == nil {
			rows = append(rows, codingagents.ClaudeSessions(limit)...)
		}
	}
	if agent == "" || agent == "codex" {
		if r, err := resolve("codex"); err == nil {
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			list, err := codingagents.CodexSessions(ctx, r.path, codingagents.AgentEnv("codex"), limit)
			cancel()
			if err != nil {
				errs = append(errs, "Codex: "+err.Error())
			}
			rows = append(rows, list...)
		}
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].UpdatedAt > rows[j].UpdatedAt })
	if len(rows) > limit {
		rows = rows[:limit]
	}
	linked := map[string]string{}
	a.mu.Lock()
	for _, c := range a.convs {
		if c.Session != "" {
			linked[c.Agent+"/"+c.Session] = c.ID
		}
	}
	a.mu.Unlock()
	known := a.knownSet()
	out := make([]SessionRow, len(rows))
	for i, r := range rows {
		_, k := known[r.Agent+"/"+r.ID]
		out[i] = SessionRow{NativeSession: r, ConversationID: linked[r.Agent+"/"+r.ID], Known: k}
	}
	// The Pantheon Agent's chats live with the Agent, not on this computer:
	// the ones started here are listed from what their terminals reported.
	if agent == "" || agent == codingagents.PantheonAgent {
		for _, k := range known {
			if k.Agent == codingagents.PantheonAgent {
				out = append(out, SessionRow{NativeSession: codingagents.NativeSession{Agent: k.Agent, ID: k.ID,
					Title: firstNonEmpty(k.Title, "Pantheon Agent chat"), Cwd: k.Cwd, UpdatedAt: k.At}, Known: true})
			}
		}
		sort.SliceStable(out, func(i, j int) bool { return out[i].UpdatedAt > out[j].UpdatedAt })
	}
	// known_sessions: this service marks the desktop's sessions (Known).
	res := map[string]any{"success": true, "sessions": out, "known_sessions": true}
	if len(errs) > 0 {
		res["warnings"] = errs
	}
	return res
}
