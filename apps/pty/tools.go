//go:build !windows

package ptyapp

import (
	"context"
	_ "embed"

	"github.com/aristoteleo/pantheon-fleet/appsvc"
)

func strP(params map[string]any, name string) string {
	if v, ok := params[name].(string); ok {
		return v
	}
	return ""
}

func intP(params map[string]any, name string, def int) int {
	switch v := params[name].(type) {
	case float64:
		return int(v)
	case int:
		return v
	}
	return def
}

//go:embed app.json
var manifestJSON []byte

// Tools returns the pty@1 surface (all hidden — UI-facing only, like
// @tool(exclude=True)). Signatures come from the embedded app.json — the
// same manifest `pantheon.apps check` keeps honest — so Go contributes only
// the handlers; a wiring mismatch errors at startup. session_id is
// framework-injected and therefore not declared, but handlers still read it
// from the parameters.
func Tools(app *App) ([]*appsvc.Tool, error) {
	return appsvc.ManifestTools(manifestJSON, map[string]appsvc.Handler{
		"pty_open": func(_ context.Context, p map[string]any) (any, error) {
			return app.open(intP(p, "cols", 80), intP(p, "rows", 24),
				strP(p, "cwd"), strP(p, "shell")), nil
		},
		"pty_write": func(_ context.Context, p map[string]any) (any, error) {
			return app.write(strP(p, "session_id"), strP(p, "data")), nil
		},
		"pty_resize": func(_ context.Context, p map[string]any) (any, error) {
			return app.resize(strP(p, "session_id"),
				intP(p, "cols", 0), intP(p, "rows", 0)), nil
		},
		"pty_attach": func(_ context.Context, p map[string]any) (any, error) {
			return app.attach(strP(p, "session_id"),
				intP(p, "cols", 0), intP(p, "rows", 0)), nil
		},
		"pty_close": func(_ context.Context, p map[string]any) (any, error) {
			return app.closeSession(strP(p, "session_id")), nil
		},
		"pty_list": func(_ context.Context, _ map[string]any) (any, error) {
			return app.list(), nil
		},
	})
}
