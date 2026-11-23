package hosted

// Moving an agent to another computer of the owner's (docs/apps/team-chat.md,
// "Moving an agent to another computer"): its folder is packed on the old
// computer, carried by the Fleet's node-to-node transfer and unpacked on the
// new one; then its record names the new computer and a new session starts
// there. The old folder stays where it was.

import (
	"context"
	"fmt"
	"log"
	"strings"
	"time"
)

// teamNodeShell runs commands on the owner's nodes and carries files between
// them (the controller's Fleet service); TeamNodes that cannot leave moving
// an agent's folder out (start fresh only).
type teamNodeShell interface {
	Run(ctx context.Context, fleet, nodeID, code string, timeout int) map[string]any
	Transfer(ctx context.Context, fleet, src, srcPath, dst, dstPath string, timeout int) map[string]any
}

func (n fleetNodes) Run(ctx context.Context, fleet, nodeID, code string, timeout int) map[string]any {
	return n.f.runOnNode(ctx, fleet, nodeID, code, "shell", timeout)
}

func (n fleetNodes) Transfer(ctx context.Context, fleet, src, srcPath, dst, dstPath string, timeout int) map[string]any {
	id, done := n.f.startTransfer(fleet, src, srcPath, dst, dstPath, "sha256", "zstd", false, timeout)
	select {
	case <-done:
	case <-ctx.Done():
		return map[string]any{"state": "failed", "error": ctx.Err().Error()}
	}
	n.f.mu.Lock()
	defer n.f.mu.Unlock()
	return n.f.transfers[fleet][id]
}

type agentMoveSpec struct {
	MID    string `json:"mid"`
	NodeID string `json:"node_id"`
	Folder string `json:"folder"`
	// Fresh: start on the new computer with an empty folder (its old one
	// cannot be copied, or the owner does not want it).
	Fresh bool `json:"fresh"`
}

const (
	moveArchiveMark = "PANTHEON_MOVE_ARCHIVE="
	moveDirMark     = "PANTHEON_MOVE_DIR="
	moveNoFolder    = "PANTHEON_MOVE_NOFOLDER"
	moveOK          = "PANTHEON_MOVE_OK"
)

// shQuote quotes a word for sh; psQuote for PowerShell.
func shQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }
func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

// movePack packs folder (all but .team/) into an archive in a temp folder of
// the node and prints where.
func movePack(goos, folder string) string {
	if goos == "windows" {
		return "$ErrorActionPreference='Stop'\n$src = " + psQuote(folder) + "\n" +
			"if (-not (Test-Path -LiteralPath $src -PathType Container)) { '" + moveNoFolder + "'; exit 3 }\n" +
			"$d = Join-Path ([IO.Path]::GetTempPath()) ('pantheon-move-' + [guid]::NewGuid().ToString('N'))\n" +
			"New-Item -ItemType Directory -Path $d | Out-Null\n$a = Join-Path $d 'agent.tgz'\n" +
			"tar -czf $a -C $src --exclude ./.team .\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n" +
			"'" + moveArchiveMark + "' + $a\n"
	}
	return "set -e\nsrc=" + shQuote(folder) + "\n" +
		"[ -d \"$src\" ] || { echo " + moveNoFolder + "; exit 3; }\n" +
		"d=$(mktemp -d \"${TMPDIR:-/tmp}/pantheon-move.XXXXXX\")\n" +
		"tar -czf \"$d/agent.tgz\" -C \"$src\" --exclude ./.team .\n" +
		"echo " + moveArchiveMark + "\"$d/agent.tgz\"\n"
}

// moveTemp makes a temp folder on the target for the archive.
func moveTemp(goos string) string {
	if goos == "windows" {
		return "$ErrorActionPreference='Stop'\n" +
			"$d = Join-Path ([IO.Path]::GetTempPath()) ('pantheon-move-' + [guid]::NewGuid().ToString('N'))\n" +
			"New-Item -ItemType Directory -Path $d | Out-Null\n'" + moveDirMark + "' + $d\n"
	}
	return "set -e\nd=$(mktemp -d \"${TMPDIR:-/tmp}/pantheon-move.XXXXXX\")\necho " + moveDirMark + "\"$d\"\n"
}

// moveUnpack unpacks the archive into folder and removes the archive's
// temp folder.
func moveUnpack(goos, archive, folder string) string {
	dir := archive[:strings.LastIndexAny(archive, `/\`)]
	if goos == "windows" {
		return "$ErrorActionPreference='Stop'\n$dst = " + psQuote(folder) + "\n" +
			"New-Item -ItemType Directory -Force -Path $dst | Out-Null\n" +
			"tar -xzf " + psQuote(archive) + " -C $dst\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n" +
			"Remove-Item -Recurse -Force -LiteralPath " + psQuote(dir) + "\n'" + moveOK + "'\n"
	}
	return "set -e\ndst=" + shQuote(folder) + "\nmkdir -p \"$dst\"\n" +
		"tar -xzf " + shQuote(archive) + " -C \"$dst\"\nrm -rf " + shQuote(dir) + "\necho " + moveOK + "\n"
}

// moveCleanup removes an archive's temp folder (best effort).
func moveCleanup(goos, archive string) string {
	dir := archive[:strings.LastIndexAny(archive, `/\`)]
	if goos == "windows" {
		return "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -LiteralPath " + psQuote(dir)
	}
	return "rm -rf " + shQuote(dir)
}

// marked is the value after mark on a line of a command's output.
func marked(out, mark string) string {
	for _, line := range strings.Split(out, "\n") {
		if v, ok := strings.CutPrefix(strings.TrimSpace(line), mark); ok {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

// ranOK: a command's result, or what went wrong in a line.
func ranOK(res map[string]any) (string, string) {
	out := str(res["stdout"])
	if res["success"] == true {
		return out, ""
	}
	why := firstNonBlank(str(res["error"]), strings.TrimSpace(str(res["stderr"])), strings.TrimSpace(out), "it stopped with an error")
	return out, firstLineOf(why)
}

// runtimeTrouble says why an agent's runtime cannot run on a computer, from
// its agents_detect ("" when it can).
func runtimeTrouble(detect map[string]any, runtime, where string) string {
	label := runtimeLabel(runtime)
	for _, item := range orList(detect["agents"]) {
		a, _ := item.(map[string]any)
		if str(a["id"]) != runtime || a["missing"] == true || a["conversation"] != true {
			continue
		}
		if a["signed_in"] == false {
			return label + " is not signed in on " + where + ": open Coding Agents on " + where + " → Sign in first"
		}
		if runtime == "pantheon" && detect["pantheon"] == false {
			return label + " cannot use PantheonOS models on " + where + " yet: open Coding Agents on " + where + " and turn on \"PantheonOS on\" first"
		}
		return ""
	}
	return label + " is not installed on " + where + ": open Coding Agents on " + where + " → Install first"
}

// agentMove moves an agent to another of the owner's computers.
func (t *TeamChat) agentMove(ctx context.Context, fid string, spec agentMoveSpec) (any, error) {
	mid := spec.MID
	target, err := t.ownerNode(ctx, fid, spec.NodeID)
	if err != nil {
		return nil, err
	}
	if err := folderShape(spec.Folder); err != nil {
		return nil, err
	}
	fl, err := t.lockFleet(ctx, fid)
	if err != nil {
		return nil, err
	}
	m, err := t.agentOf(fl, mid)
	if err != nil {
		fl.mu.Unlock()
		return nil, err
	}
	l := fl.liveOf(mid)
	switch {
	case spec.NodeID == m.NodeID:
		fl.mu.Unlock()
		return nil, &ArgumentError{m.Name + " is on that computer already"}
	case fl.turns[mid] != nil:
		fl.mu.Unlock()
		return nil, &ArgumentError{m.Name + " is in a turn: stop the turn first, then move it"}
	case l.moving != "":
		fl.mu.Unlock()
		return nil, &ArgumentError{m.Name + " is moving already"}
	}
	agent := *m
	roster := rosterOf(fl, &agent)
	l.moving = "Getting ready to move"
	t.emitStatus(fl, mid)
	fl.mu.Unlock()

	// The move goes on when the person's page closes.
	ctx = context.WithoutCancel(ctx)
	step := func(line string) {
		fl.mu.Lock()
		fl.liveOf(mid).moving = line
		t.emitStatus(fl, mid)
		fl.mu.Unlock()
	}
	moved := false
	defer func() {
		fl.mu.Lock()
		fl.liveOf(mid).moving = ""
		t.emitStatus(fl, mid)
		fl.mu.Unlock()
		if moved {
			t.kick(fid, mid)
		}
	}()

	source, _ := t.Nodes.Node(ctx, fid, agent.NodeID)
	source = withMemberNode(source, &agent)
	from, to := nodeLabel(source, agent.NodeID), nodeLabel(target, spec.NodeID)
	if !target.Online {
		return nil, &ArgumentError{capFirst(to) + " is offline. Start it (or resume your Workspace), then move " + agent.Name + "."}
	}
	// Its runtime there: installed and signed in.
	detect, err := t.Nodes.Call(ctx, fid, spec.NodeID, "agents_detect", map[string]any{}, 60*time.Second)
	if err != nil {
		return nil, t.plainError(spec.NodeID, target, &tcMember{Runtime: agent.Runtime, NodeID: spec.NodeID}, err.Error())
	}
	if detect["success"] != true {
		return nil, t.plainError(spec.NodeID, target, &tcMember{Runtime: agent.Runtime, NodeID: spec.NodeID}, str(detect["error"]))
	}
	if why := runtimeTrouble(detect, agent.Runtime, to); why != "" {
		return nil, &ArgumentError{capFirst(why) + "."}
	}
	// Its new folder there, as that computer resolves it.
	folder := strings.TrimSpace(spec.Folder)
	if folder == "" {
		folder = defaultFolder(target, str(detect["home"]), agent.Handle)
	}
	moving := agent
	moving.NodeID, moving.NodeName, moving.NodeKind, moving.NodeOS, moving.Folder = spec.NodeID, target.Name, target.Kind, target.OS, folder
	check, err := t.Nodes.Call(ctx, fid, spec.NodeID, "team_setup", map[string]any{"folder": folder, "check_only": true}, 30*time.Second)
	if err != nil {
		return nil, t.plainError(spec.NodeID, target, &moving, err.Error())
	}
	if check["success"] != true {
		reason, fix, _ := explain(&moving, target, str(check["error"]))
		return nil, &ArgumentError{capFirst(strings.TrimSpace(reason + ". " + fix))}
	}
	if agent.Access != "only_me" && agent.Access != "" {
		if why := checkedFolderRefusal(check, folder, str(detect["home"])); why != "" {
			return nil, sharedRefusal(why)
		}
	}
	folder = firstNonBlank(str(check["folder"]), folder)
	moving.Folder = folder

	copied := false
	if !spec.Fresh {
		if err := t.moveFolder(ctx, fid, &agent, source, target, from, to, folder, step); err != nil {
			return nil, err
		}
		copied = true
	}
	step("Setting up on " + to)
	setup, err := t.Nodes.Call(ctx, fid, spec.NodeID, "team_setup", map[string]any{"folder": folder,
		"agent_md": agentBrief(&moving, roster)}, 30*time.Second)
	if err != nil {
		return nil, t.plainError(spec.NodeID, target, &moving, err.Error())
	}
	if setup["success"] != true {
		reason, fix, _ := explain(&moving, target, str(setup["error"]))
		return nil, &ArgumentError{capFirst(strings.TrimSpace(reason + ". " + fix))}
	}
	folder = firstNonBlank(str(setup["folder"]), folder)

	fl.mu.Lock()
	defer fl.mu.Unlock()
	cur := fl.members[mid]
	if cur == nil {
		return nil, &ArgumentError{agent.Name + " was deleted while it moved"}
	}
	next := *cur
	next.NodeID, next.NodeName, next.NodeKind, next.NodeOS, next.Folder = spec.NodeID, target.Name, target.Kind, target.OS, folder
	next.Session, next.Attention = "", nil
	if err := t.put(ctx, fl, "member."+mid, &next); err != nil {
		return nil, err
	}
	fl.members[mid] = &next
	l = fl.liveOf(mid)
	// Done moving: the answer's status is its status now (the page shows
	// it after the status event).
	l.moving = ""
	l.retries, l.retryAt, l.retryWhy, l.appWait = 0, time.Time{}, "", time.Time{}
	t.emit(fl, map[string]any{"type": "member", "member": &next})
	moved = true
	log.Printf("[team-chat] %s: agent %s moved %s:%s → %s:%s (copied %v)", fid, mid, agent.NodeID, agent.Folder, spec.NodeID, folder, copied)
	done := fmt.Sprintf("%s is on %s now, in %s.", agent.Name, to, folder)
	if copied {
		done += " Its folder was copied; the old one stays on " + from + " (" + agent.Folder + ")."
	} else {
		done += " It starts fresh there; its old folder stays on " + from + " (" + agent.Folder + ")."
	}
	return frozen(map[string]any{"agent": &next, "copied": copied, "old_folder": agent.Folder, "old_computer": from,
		"done": done, "status": t.statusOf(fl, &next)})
}

// moveFolder copies an agent's folder from its computer to target's folder.
func (t *TeamChat) moveFolder(ctx context.Context, fid string, agent *tcMember, source, target TeamNode, from, to, folder string,
	step func(string)) error {
	shell, ok := t.Nodes.(teamNodeShell)
	if !ok {
		return &ArgumentError{"This service cannot copy folders between computers; move it with Start fresh."}
	}
	if !source.Online {
		return &ArgumentError{capFirst(from) + " is offline, so " + agent.Name + "'s folder cannot be copied. " +
			"Start " + from + " and try again, or start fresh on " + to + " (without its folder and memory)."}
	}
	// Where the folder is on its computer ("~" as it resolves there).
	srcFolder := agent.Folder
	if res, err := t.Nodes.Call(ctx, fid, agent.NodeID, "team_setup", map[string]any{"folder": agent.Folder, "check_only": true}, 30*time.Second); err == nil && res["success"] == true {
		srcFolder = firstNonBlank(str(res["folder"]), srcFolder)
	}
	step("Packing its folder on " + from)
	out, why := ranOK(shell.Run(ctx, fid, agent.NodeID, movePack(source.OS, srcFolder), 600))
	if strings.Contains(out, moveNoFolder) {
		return &ArgumentError{agent.Name + "'s folder " + srcFolder + " is not on " + from + ". Start fresh on " + to + " instead."}
	}
	archive := marked(out, moveArchiveMark)
	if why != "" || archive == "" {
		return &ArgumentError{fmt.Sprintf("Could not pack %s's folder on %s (%s). Nothing was moved.", agent.Name, from, firstNonBlank(why, "no archive"))}
	}
	defer shell.Run(ctx, fid, agent.NodeID, moveCleanup(source.OS, archive), 60)
	out, why = ranOK(shell.Run(ctx, fid, target.ID, moveTemp(target.OS), 60))
	dir := marked(out, moveDirMark)
	if why != "" || dir == "" {
		return &ArgumentError{fmt.Sprintf("Could not make room on %s for %s's folder (%s). Nothing was moved.", to, agent.Name, firstNonBlank(why, "no temp folder"))}
	}
	sep := "/"
	if target.OS == "windows" {
		sep = `\`
	}
	dst := strings.TrimRight(dir, `/\`) + sep + "agent.tgz"
	step("Copying its folder to " + to)
	p := shell.Transfer(ctx, fid, agent.NodeID, archive, target.ID, dst, 1800)
	if str(p["state"]) != "done" {
		shell.Run(ctx, fid, target.ID, moveCleanup(target.OS, dst), 60)
		return &ArgumentError{fmt.Sprintf("Could not copy %s's folder from %s to %s (%s). Nothing was moved; try again.",
			agent.Name, from, to, firstLineOf(firstNonBlank(str(p["error"]), "the transfer did not finish")))}
	}
	step("Unpacking its folder on " + to)
	out, why = ranOK(shell.Run(ctx, fid, target.ID, moveUnpack(target.OS, dst, folder), 600))
	if why != "" || !strings.Contains(out, moveOK) {
		shell.Run(ctx, fid, target.ID, moveCleanup(target.OS, dst), 60)
		return &ArgumentError{fmt.Sprintf("Could not unpack %s's folder into %s on %s (%s). Its old folder is unchanged.",
			agent.Name, folder, to, firstNonBlank(why, "it did not finish"))}
	}
	return nil
}
