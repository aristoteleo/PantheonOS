//go:build !windows

package ptyapp

import (
	"context"

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

// Tools returns the pty@1 surface (all hidden — UI-facing only, like
// @tool(exclude=True)). Declared inputs mirror the committed pty.app.json:
// session_id is framework-injected and therefore not declared, but handlers
// still read it from the parameters.
func Tools(app *App) []*appsvc.Tool {
	req := func(name, typ string) appsvc.Param {
		return appsvc.Param{Type: typ, Range: nil, Default: appsvc.NotDefined, Name: name, Doc: nil}
	}
	opt := func(name, typ string, def any) appsvc.Param {
		return appsvc.Param{Type: typ, Range: nil, Default: def, Name: name, Doc: nil}
	}
	return []*appsvc.Tool{
		{
			Name:   "pty_open",
			Doc:    "Start a shell on a new pseudo-terminal.",
			Hidden: true,
			Inputs: []appsvc.Param{
				opt("cols", "int", 80), opt("rows", "int", 24),
				opt("cwd", "str | None", nil), opt("shell", "str | None", nil),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.open(intP(p, "cols", 80), intP(p, "rows", 24),
					strP(p, "cwd"), strP(p, "shell")), nil
			},
		},
		{
			Name:   "pty_write",
			Doc:    "Send keystrokes to a session.",
			Hidden: true,
			Inputs: []appsvc.Param{req("data", "str")},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.write(strP(p, "session_id"), strP(p, "data")), nil
			},
		},
		{
			Name:   "pty_resize",
			Doc:    "Tell the shell the window changed size.",
			Hidden: true,
			Inputs: []appsvc.Param{req("cols", "int"), req("rows", "int")},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.resize(strP(p, "session_id"),
					intP(p, "cols", 0), intP(p, "rows", 0)), nil
			},
		},
		{
			Name:   "pty_attach",
			Doc:    "Rejoin a live session instead of starting a shell.",
			Hidden: true,
			Inputs: []appsvc.Param{
				opt("cols", "int | None", nil), opt("rows", "int | None", nil),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.attach(strP(p, "session_id"),
					intP(p, "cols", 0), intP(p, "rows", 0)), nil
			},
		},
		{
			Name:   "pty_close",
			Doc:    "End a session and reap its shell.",
			Hidden: true,
			Inputs: []appsvc.Param{},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.closeSession(strP(p, "session_id")), nil
			},
		},
		{
			Name:   "pty_list",
			Doc:    "Sessions this toolset is holding.",
			Hidden: true,
			Inputs: []appsvc.Param{},
			Handler: func(_ context.Context, _ map[string]any) (any, error) {
				return app.list(), nil
			},
		},
	}
}
