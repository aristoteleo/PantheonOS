package codingagentsapp

import (
	"context"
	_ "embed"
	"encoding/json"

	"github.com/aristoteleo/pantheonos/os/appsvc"
	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// remarshal decodes a JSON parameter into its type; nil leaves out zero.
func remarshal(v any, out any) error {
	if v == nil {
		return nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, out)
}

//go:embed app.json
var manifestJSON []byte

func strP(p map[string]any, name string) string {
	s, _ := p[name].(string)
	return s
}

func boolP(p map[string]any, name string, def bool) bool {
	if b, ok := p[name].(bool); ok {
		return b
	}
	return def
}

func intP(p map[string]any, name string) int64 {
	switch v := p[name].(type) {
	case float64:
		return int64(v)
	case int:
		return int64(v)
	case int64:
		return v
	}
	return 0
}

// Tools binds the manifest's tool face to the App.
func Tools(app *App) ([]*appsvc.Tool, error) {
	return appsvc.ManifestTools(manifestJSON, map[string]appsvc.Handler{
		"agents_detect": func(_ context.Context, p map[string]any) (any, error) {
			return app.detect(boolP(p, "refresh", false)), nil
		},
		"agent_terminal_command": func(_ context.Context, p map[string]any) (any, error) {
			return app.terminalCommand(TerminalArgs{Agent: strP(p, "agent"), Resume: strP(p, "resume"),
				Auto: boolP(p, "auto", true), Cwd: strP(p, "cwd")}), nil
		},
		"agent_send": func(_ context.Context, p map[string]any) (any, error) {
			return app.send(SendArgs{Agent: strP(p, "agent"), Cwd: strP(p, "cwd"), Prompt: strP(p, "prompt"),
				ConversationID: strP(p, "conversation_id"), Model: strP(p, "model"),
				Auto: boolP(p, "auto", true), Origin: strP(p, "origin"),
				Session: strP(p, "session_id"), Title: strP(p, "title")}), nil
		},
		"agent_sessions": func(_ context.Context, p map[string]any) (any, error) {
			return app.sessions(strP(p, "agent"), int(intP(p, "limit"))), nil
		},
		"agent_terminals": func(_ context.Context, _ map[string]any) (any, error) {
			return app.terminalList(), nil
		},
		"agents_settings": func(_ context.Context, _ map[string]any) (any, error) {
			return app.getSettings(), nil
		},
		"agents_configure": func(_ context.Context, p map[string]any) (any, error) {
			var c Configure
			if raw, ok := p["agents"]; ok && raw != nil {
				if err := remarshal(raw, &c.Agents); err != nil {
					return failure("agents: %v", err), nil
				}
				if c.Agents == nil {
					c.Agents = map[string]codingagents.AgentSettings{}
				}
			}
			if raw, ok := p["custom"]; ok {
				custom := []codingagents.CustomAgent{}
				if err := remarshal(raw, &custom); err != nil {
					return failure("custom: %v", err), nil
				}
				c.Custom = &custom
			}
			if raw, ok := p["projects"]; ok {
				projects := []codingagents.Project{}
				if err := remarshal(raw, &projects); err != nil {
					return failure("projects: %v", err), nil
				}
				c.Projects = &projects
			}
			return app.configure(c), nil
		},
		"agents_access": func(_ context.Context, p map[string]any) (any, error) {
			return app.setAccess(strP(p, "key"), intP(p, "key_id"), strP(p, "hub_url")), nil
		},
		"agent_stop": func(_ context.Context, p map[string]any) (any, error) {
			return app.stop(strP(p, "conversation_id")), nil
		},
		"agent_conversations": func(_ context.Context, p map[string]any) (any, error) {
			return app.list(strP(p, "agent"), strP(p, "cwd")), nil
		},
		"agent_conversation": func(_ context.Context, p map[string]any) (any, error) {
			return app.conversation(strP(p, "conversation_id"), intP(p, "after")), nil
		},
		"agent_delete": func(_ context.Context, p map[string]any) (any, error) {
			return app.remove(strP(p, "conversation_id")), nil
		},
	})
}
