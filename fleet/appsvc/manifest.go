package appsvc

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// ManifestTools builds a Tool table from an app.json's provides.tools,
// binding handlers by tool name.
//
// The manifest is the signature authority — the same file the Python side's
// `pantheon.apps emit/check` keeps honest — so a Go builtin app embeds its
// own app.json (go:embed) and contributes only the handlers. Every manifest
// tool must have a handler and every handler a manifest tool: a mismatch is
// a wiring bug and errors at startup rather than surfacing as a tool that
// lists but never answers (or answers but never lists).
func ManifestTools(manifestJSON []byte, handlers map[string]Handler) ([]*Tool, error) {
	var m struct {
		ID       string `json:"id"`
		Provides struct {
			Tools []struct {
				Name        string `json:"name"`
				Description string `json:"description"`
				Hidden      bool   `json:"hidden"`
				// Raw per-param objects: an explicit "default": null must
				// stay distinguishable from no default key at all, which a
				// typed pointer field cannot do.
				Params []map[string]json.RawMessage `json:"params"`
			} `json:"tools"`
		} `json:"provides"`
	}
	if err := json.Unmarshal(manifestJSON, &m); err != nil {
		return nil, fmt.Errorf("parse app.json: %w", err)
	}
	if len(m.Provides.Tools) == 0 {
		return nil, fmt.Errorf("app.json for %q declares no provides.tools", m.ID)
	}

	unbound := make(map[string]bool, len(handlers))
	for name := range handlers {
		unbound[name] = true
	}

	tools := make([]*Tool, 0, len(m.Provides.Tools))
	for _, t := range m.Provides.Tools {
		// The service itself answers the meta tools; the manifest lists
		// them because they are on the wire, but no app handler exists.
		if t.Name == "list_tools" || t.Name == "_ping" {
			continue
		}
		h, ok := handlers[t.Name]
		if !ok {
			return nil, fmt.Errorf("manifest tool %q has no Go handler", t.Name)
		}
		delete(unbound, t.Name)
		params := make([]Param, 0, len(t.Params))
		for _, p := range t.Params {
			var name, typ string
			if raw, ok := p["name"]; ok {
				_ = json.Unmarshal(raw, &name)
			}
			if raw, ok := p["type"]; ok {
				_ = json.Unmarshal(raw, &typ)
			}
			var def any = NotDefined
			if raw, ok := p["default"]; ok {
				if err := json.Unmarshal(raw, &def); err != nil {
					return nil, fmt.Errorf("tool %q param %q: bad default: %w", t.Name, name, err)
				}
			}
			params = append(params, Param{
				Type: typ, Range: nil, Default: def, Name: name, Doc: nil,
			})
		}
		tools = append(tools, &Tool{
			Name:    t.Name,
			Doc:     t.Description,
			Hidden:  t.Hidden,
			Inputs:  params,
			Handler: h,
		})
	}
	if len(unbound) > 0 {
		names := make([]string, 0, len(unbound))
		for name := range unbound {
			names = append(names, name)
		}
		sort.Strings(names)
		return nil, fmt.Errorf(
			"handlers with no manifest tool: %s", strings.Join(names, ", "))
	}
	return tools, nil
}
