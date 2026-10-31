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
				Auto: boolP(p, "auto", true), Cwd: strP(p, "cwd"), Prompt: strP(p, "prompt")}), nil
		},
		"agent_task_command": func(_ context.Context, p map[string]any) (any, error) {
			return app.taskCommand(strP(p, "agent"), strP(p, "task")), nil
		},
		"agent_send": func(_ context.Context, p map[string]any) (any, error) {
			args := SendArgs{Agent: strP(p, "agent"), Cwd: strP(p, "cwd"), Prompt: strP(p, "prompt"),
				ConversationID: strP(p, "conversation_id"), Model: strP(p, "model"),
				Auto: boolP(p, "auto", true), Origin: strP(p, "origin"),
				Session: strP(p, "session_id"), Title: strP(p, "title")}
			ct := &CanvasTurn{Effort: strP(p, "effort"), Research: boolP(p, "research", true)}
			for name, into := range map[string]any{"subagents": &ct.Subagents, "focus": &ct.Focus, "files": &ct.Files,
				"images": &ct.Images, "skills": &ct.Skills} {
				if err := remarshal(p[name], into); err != nil {
					return failure("%s: %v", name, err), nil
				}
			}
			if raw, ok := p["links"]; ok && raw != nil {
				links := []Link{}
				if err := remarshal(raw, &links); err != nil {
					return failure("links: %v", err), nil
				}
				args.Links, args.LinkName = &links, strP(p, "link_name")
			}
			if args.Origin == "canvas" || ct.Effort != "" || !ct.Research || len(ct.Subagents)+len(ct.Focus)+len(ct.Files)+len(ct.Images)+len(ct.Skills) > 0 {
				args.Canvas = ct
			}
			return app.send(args), nil
		},
		"agents_skills": func(_ context.Context, p map[string]any) (any, error) {
			return skills(strP(p, "cwd"), strP(p, "agent")), nil
		},
		"agents_repo_info": func(_ context.Context, p map[string]any) (any, error) {
			return repoInfo(strP(p, "path")), nil
		},
		"agents_folder_tree": func(_ context.Context, p map[string]any) (any, error) {
			return folderTree(strP(p, "path"), strP(p, "dir")), nil
		},
		"agents_reference_put": func(_ context.Context, p map[string]any) (any, error) {
			return referencePut(ReferenceChunk{Cwd: strP(p, "cwd"), UploadID: strP(p, "upload_id"), Name: strP(p, "name"),
				Offset: intP(p, "offset"), Data: strP(p, "data"), Final: boolP(p, "final", false)}), nil
		},
		"agents_reference_list": func(_ context.Context, p map[string]any) (any, error) {
			return referenceList(strP(p, "cwd")), nil
		},
		"agents_reference_thumb": func(_ context.Context, p map[string]any) (any, error) {
			return referenceThumb(strP(p, "cwd"), strP(p, "path")), nil
		},
		"agents_file_read": func(_ context.Context, p map[string]any) (any, error) {
			return app.conversationFile(strP(p, "conversation_id"), strP(p, "path"), intP(p, "offset"), intP(p, "length")), nil
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
		"agents_browse": func(_ context.Context, p map[string]any) (any, error) {
			return app.browse(strP(p, "path")), nil
		},
		"agents_create_project": func(_ context.Context, p map[string]any) (any, error) {
			return app.createProject(strP(p, "parent"), strP(p, "name"), boolP(p, "git", false)), nil
		},
		"agents_workspace_create": func(_ context.Context, p map[string]any) (any, error) {
			return app.createWorkspace(WorkspaceCreate{Project: strP(p, "project"), Name: strP(p, "name"), Base: strP(p, "base"),
				Branch: strP(p, "branch"), Agent: strP(p, "agent"), Prompt: strP(p, "prompt")}), nil
		},
		"agents_workspace_remove": func(_ context.Context, p map[string]any) (any, error) {
			return app.removeWorkspace(WorkspaceRemove{Project: strP(p, "project"), Path: strP(p, "path"),
				Remove: boolP(p, "remove", false), Force: boolP(p, "force", false), DeleteBranch: boolP(p, "delete_branch", false)}), nil
		},
		"agents_workspace_status": func(_ context.Context, p map[string]any) (any, error) {
			return app.workspaceStatus(strP(p, "project"), strP(p, "path")), nil
		},
		"agents_workspace_adopt": func(_ context.Context, p map[string]any) (any, error) {
			return app.adoptWorkspace(strP(p, "project"), strP(p, "path")), nil
		},
		"agents_project_git_init": func(_ context.Context, p map[string]any) (any, error) {
			return app.initGit(strP(p, "project")), nil
		},
		"agent_sessions_import": func(_ context.Context, p map[string]any) (any, error) {
			var refs []knownSession
			if err := remarshal(p["sessions"], &refs); err != nil {
				return failure("sessions: %v", err), nil
			}
			if err := app.remember(refs...); err != nil {
				return failure("%v", err), nil
			}
			return map[string]any{"success": true}, nil
		},
		"agent_sessions_forget": func(_ context.Context, p map[string]any) (any, error) {
			var refs []knownSession
			if err := remarshal(p["sessions"], &refs); err != nil {
				return failure("sessions: %v", err), nil
			}
			if err := app.forget(refs...); err != nil {
				return failure("%v", err), nil
			}
			return map[string]any{"success": true}, nil
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
		"agents_links": func(_ context.Context, p map[string]any) (any, error) {
			var links *[]Link
			if raw, ok := p["links"]; ok && raw != nil {
				list := []Link{}
				if err := remarshal(raw, &list); err != nil {
					return failure("links: %v", err), nil
				}
				links = &list
			}
			return app.setLinks(strP(p, "conversation_id"), strP(p, "name"), links), nil
		},
		"agents_link_deliver": func(_ context.Context, p map[string]any) (any, error) {
			var m linkMessage
			if err := remarshal(p["message"], &m); err != nil || m.ID == "" || m.To == "" || m.From.ConversationID == "" {
				return failure("message: give its id, from and to"), nil
			}
			// Over the bus a message comes from another node, which it names;
			// this node's own messages never take this way.
			if m.From.NodeID == "" || m.From.NodeID == app.NodeID {
				return map[string]any{"success": false, "status": "failed", "reason": "invalid",
					"error": "a message from another node must name that node"}, nil
			}
			status, reason, errText := app.deliver(m)
			return map[string]any{"success": status != "failed", "status": status, "reason": reason, "error": errText}, nil
		},
		"agents_links_release": func(_ context.Context, p map[string]any) (any, error) {
			return app.releaseLinks(strP(p, "conversation_id")), nil
		},
		"agents_link_update": func(_ context.Context, p map[string]any) (any, error) {
			var to codingagents.LinkEnd
			if err := remarshal(p["to"], &to); err != nil {
				return failure("to: %v", err), nil
			}
			return app.linkUpdate(strP(p, "conversation_id"), strP(p, "id"), strP(p, "status"), strP(p, "error"), to), nil
		},
	})
}
