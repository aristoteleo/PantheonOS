package codingagentsapp

import (
	"context"
	_ "embed"

	"github.com/aristoteleo/pantheonos/os/appsvc"
)

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
			return app.terminalCommand(strP(p, "agent"), strP(p, "resume"), boolP(p, "auto", true)), nil
		},
		"agent_send": func(_ context.Context, p map[string]any) (any, error) {
			return app.send(SendArgs{Agent: strP(p, "agent"), Cwd: strP(p, "cwd"), Prompt: strP(p, "prompt"),
				ConversationID: strP(p, "conversation_id"), Model: strP(p, "model"),
				Auto: boolP(p, "auto", true), Origin: strP(p, "origin")}), nil
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
