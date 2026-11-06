package codingagentsapp

import (
	"fmt"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// sandboxTurn is a Team Chat turn's command in its sandbox: the program to
// start (the sandbox's launcher), its arguments, and its environment with a
// home of its own (docs/apps/team-chat.md, "Shared agents run sandboxed").
func (a *App) sandboxTurn(c *Conversation, team *teamBox, program string, args, env []string,
	model *codingagents.Platform) (string, []string, []string, error) {
	if team == nil {
		return "", nil, nil, fmt.Errorf("%sthe agent's .team folder could not be prepared", codingagents.SandboxRefusal)
	}
	dir, err := codingagents.SandboxDir(team.team.MID)
	if err != nil {
		return "", nil, nil, err
	}
	var path []string
	for _, kv := range env {
		if v, ok := strings.CutPrefix(kv, "PATH="); ok {
			path = filepath.SplitList(v)
		}
	}
	sb := codingagents.Sandbox{Folder: resolvePath(c.Cwd), TeamDir: filepath.Dir(team.inbox), Inbox: team.inbox, Dir: dir,
		Program: program, Reads: codingagents.InstallReads(program, path)}
	if model != nil {
		sb.Gateway = codingagents.GatewayPort(model.Base)
	}
	if err := sb.Prepare(); err != nil {
		return "", nil, nil, err
	}
	prog, wrapped, err := sb.Wrap(args)
	if err != nil {
		return "", nil, nil, fmt.Errorf("%s%v", codingagents.SandboxRefusal, err)
	}
	return prog, wrapped, sb.Env(env), nil
}

// sandboxAvailable is what team_setup tells Team Chat about this
// computer's sandbox.
func sandboxAvailable() map[string]any {
	kind, why := codingagents.SandboxKind()
	return map[string]any{"kind": kind, "why": why, "runtimes": []string{"claude", "codex"}, "os": runtime.GOOS}
}

