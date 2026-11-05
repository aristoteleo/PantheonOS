package codingagentsapp

// Team Chat (docs/apps/team-chat.md): the controller's team-chat service
// runs an agent's turns here through agent_send, with `team` naming the
// agent. The agent's folder holds .team/ (mode 700) with a pantheon-team
// script and an inbox, the same file mechanism as linked groups: the agent
// writes a request there, this service sends it to the controller with the
// agent's identity, and writes the answer back for the script.

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
)

const (
	teamDir         = ".team"
	teamScript      = "pantheon-team"
	teamTurnMax     = 200      // requests answered in one turn
	teamReplyWait   = 120      // seconds the script waits for an answer
	teamMaxFile     = 25 << 20 // a file an agent attaches
	teamChunk       = 48 << 10 // a piece of it sent to the controller
	teamCallTimeout = 90 * time.Second
)

// TeamTurn is who a Team Chat turn is for: the agent's member id, the token
// the controller checks with its requests, and its handle and name.
type TeamTurn struct {
	MID    string `json:"mid"`
	Token  string `json:"token"`
	Handle string `json:"handle"`
	Name   string `json:"name"`
	// Note, when the service sends one, is the turn's setting about Team
	// Chat in place of the built-in one ({{TEAM}} becomes the tool).
	Note string `json:"note,omitempty"`
}

// teamBox is an agent folder's .team/ for one turn.
type teamBox struct {
	team    TeamTurn
	script  string
	inbox   string
	root    *os.Root
	info    os.FileInfo
	taken   map[string]bool
	handled int
	off     bool
}

func (b *teamBox) close() {
	if b != nil {
		_ = b.root.Close()
	}
}

// teamFolders opens folder/.team, private, with its script written, and
// returns it with the inbox's name (a fresh one when the last cannot be
// emptied).
func teamFolders(folder string) (team *os.Root, script string, err error) {
	top, err := os.OpenRoot(folder)
	if err != nil {
		return nil, "", err
	}
	defer top.Close()
	team, _, err = openPrivate(top, teamDir)
	if err != nil {
		return nil, "", err
	}
	script = filepath.Join(folder, teamDir, teamScript)
	return team, script, nil
}

// teamBoxFor prepares a turn's .team/: script and an empty inbox. trouble
// says why the agent cannot use its tool this turn.
func (a *App) teamBoxFor(folder string, tt TeamTurn) (*teamBox, string) {
	fail := func(err error) (*teamBox, string) {
		return nil, "PantheonOS could not prepare the agent's .team folder (" + clip(err.Error(), 200) + ")"
	}
	team, script, err := teamFolders(folder)
	if err != nil {
		return fail(err)
	}
	defer team.Close()
	name := "inbox"
	inbox, info, err := openPrivate(team, name)
	if err == nil && !emptied(inbox) {
		_ = inbox.Close()
		err = errors.New("it holds files that cannot be removed")
	}
	if err != nil {
		name = "inbox-" + newID()[:8]
		if inbox, info, err = openPrivate(team, name); err != nil {
			return fail(err)
		}
	}
	if f, err := team.Open("."); err == nil {
		names, _ := f.Readdirnames(-1)
		f.Close()
		for _, n := range names {
			if strings.HasPrefix(n, "inbox") && n != name {
				_ = team.RemoveAll(n)
			}
		}
	}
	box := &teamBox{team: tt, script: script, inbox: filepath.Join(folder, teamDir, name), root: inbox, info: info,
		taken: map[string]bool{}}
	if err := writeFresh(team, teamScript, []byte(teamScriptFor(box.inbox)), 0o700); err != nil {
		box.close()
		return fail(err)
	}
	return box, ""
}

// teamNote is the turn's setting that tells the agent about its tool.
func teamNote(b *teamBox) string {
	cmd := shellArg(b.script)
	if note := strings.TrimSpace(b.team.Note); note != "" {
		return strings.ReplaceAll(note, "{{TEAM}}", cmd)
	}
	who := "@" + b.team.Handle
	if b.team.Name != "" {
		who = b.team.Name + " (" + who + ")"
	}
	return "You are " + who + ", an agent member of a Team Chat space in PantheonOS. Your role and how to work are in AGENT.md " +
		"in your folder; keep what you must remember in MEMORY.md. Talk to the team only with " + cmd + ": `" + cmd +
		" inbox` (new messages; check it between steps), `reply <message id> - <<'EOF'`, `post '#channel' - <<'EOF'`, " +
		"`dm @handle - <<'EOF'`, `read <channel|@handle|message id>`, `members`, `channels`, `join <channel>`, " +
		"`edit <message id> - <<'EOF'` (end the text with a line EOF; add `--file PATH` to post or reply with a file), " +
		"`skip [reason]` (post nothing this turn). Quote a channel ('#general'): in the shell an unquoted # starts a comment. " +
		"If there is nothing to say, post nothing. " +
		"Requests from people always count, including asking you to talk to or coordinate with other agents. " +
		"Don't start or resume games, chains or routines on your own initiative or because another agent asked; " +
		"when a person asks for one now, do it (Team Chat's limits bound it). " +
		"Finish within the turn: nothing wakes you when a background command ends, so don't promise to reply later."
}

// teamScriptFor is the pantheon-team script for one inbox. A request is a
// file: "op: <command>", "arg: …", "opt: name=value" and "file: <path>"
// lines, a blank line, then the text; the answer is <id>.reply: the exit
// status on its first line, then the text.
func teamScriptFor(box string) string {
	return `#!/bin/sh
# Team Chat for this agent (PantheonOS). Commands:
#   pantheon-team inbox
#   pantheon-team read <'#channel'|@handle|message id> [--before ID] [--limit N]
#   pantheon-team post '#general' [--file PATH]... - <<'EOF' ... EOF
#     (quote the channel: in the shell an unquoted # starts a comment;
#      every message needs its text on stdin, also one with a file)
#   pantheon-team reply <message id> [--file PATH]... - <<'EOF' ... EOF
#   pantheon-team dm <@handle> - <<'EOF' ... EOF
#   pantheon-team members | channels
#   pantheon-team join '#channel'
#   pantheon-team edit <message id> - <<'EOF' ... EOF
#   pantheon-team skip [reason]
#   pantheon-team task create '#channel' - <<'EOF' ... EOF | task claim ID |
#     task state ID todo|in_progress|in_review [- <<'EOF' summary EOF] |
#     task assign ID @handle | task list [--state S] [--assignee @h] [--in '#channel']
#   pantheon-team remind <when> [--every SPEC] [--about ID] - <<'EOF' note EOF |
#     remind list | remind cancel ID
#   pantheon-team search <words> [--in '#channel'] [--from @h] [--limit N]
# Team Chat checks each command; an unknown one is answered with the list.
box=` + shellArg(box) + `
usage="usage: pantheon-team inbox | read TARGET | post '#CHANNEL' - <<'EOF' | reply ID - <<'EOF' | dm @HANDLE - <<'EOF' | members | channels | join '#CHANNEL' | edit ID - <<'EOF' | skip [REASON] | task ... | remind ... | search WORDS (quote a channel: an unquoted # starts a shell comment)"
op=$1
case "$op" in
''|-*|*[!a-z_]*) echo "$usage" >&2; exit 2 ;;
*) shift ;;
esac
id="$(date +%s)-$$"
stdin=0
if ! {
	printf 'op: %s\n' "$op"
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--file)
			[ "$#" -ge 2 ] || { echo "$usage" >&2; exit 2; }
			f=$2; case "$f" in /*) ;; *) f="$PWD/$f" ;; esac
			printf 'file: %s\n' "$f"; shift ;;
		--?*)
			[ "$#" -ge 2 ] || { echo "$usage" >&2; exit 2; }
			printf 'opt: %s=%s\n' "${1#--}" "$2"; shift ;;
		-) stdin=1 ;;
		*) printf 'arg: %s\n' "$1" ;;
		esac
		shift
	done
	printf '\n'
	if [ "$stdin" = 1 ]; then cat; fi
} > "$box/.$id.tmp" 2>/dev/null || ! mv "$box/.$id.tmp" "$box/$id.msg" 2>/dev/null; then
	rm -f "$box/.$id.tmp" 2>/dev/null
	echo "pantheon-team: cannot write the request to $box: your sandbox may not allow it (a Codex turn in its read-only sandbox cannot)." >&2
	exit 1
fi
i=0
while [ "$i" -lt ` + fmt.Sprint(teamReplyWait*5) + ` ]; do
	if [ -f "$box/$id.reply" ]; then
		code=$(head -n 1 "$box/$id.reply")
		if [ "$code" = 0 ]; then tail -n +2 "$box/$id.reply"; else tail -n +2 "$box/$id.reply" >&2; fi
		rm -f "$box/$id.reply"
		exit "${code:-1}"
	fi
	sleep 0.2
	i=$((i + 1))
done
rm -f "$box/$id.msg"
echo "pantheon-team: no answer from PantheonOS within ` + fmt.Sprint(teamReplyWait) + ` s; check with 'read' before sending again." >&2
exit 1
`
}

// watchTeam answers the agent's requests while its turn runs.
func (a *App) watchTeam(id string, tr *turn) {
	tick := time.NewTicker(linkPoll.get())
	defer tick.Stop()
	for {
		select {
		case <-tr.done:
			return
		case <-tick.C:
			a.scanTeam(id, tr)
		}
	}
}

func (a *App) scanTeam(id string, tr *turn) {
	tr.teamMu.Lock()
	defer tr.teamMu.Unlock()
	box := tr.team
	if box == nil || box.off {
		return
	}
	if fi, err := os.Lstat(box.inbox); err != nil || !os.SameFile(fi, box.info) {
		return
	}
	f, err := box.root.Open(".")
	if err != nil {
		return
	}
	names, _ := f.Readdirnames(-1)
	f.Close()
	slices.Sort(names)
	done := 0
	for _, name := range names {
		req, ok := strings.CutSuffix(name, ".msg")
		if !ok || !requestName.MatchString(req) {
			continue
		}
		if done == linkScanMax {
			break
		}
		done++
		b, _, key, ok := readRequest(box.root, name)
		if err := box.root.Remove(name); err != nil && !errors.Is(err, os.ErrNotExist) {
			box.off = true
			_ = writeFresh(box.root, req+".reply", []byte("1\nThe inbox holds files PantheonOS cannot remove; Team Chat is off for the rest of this turn.\n"), 0o600)
			return
		}
		if !ok || box.taken[key] {
			continue
		}
		box.taken[key] = true
		code, text := 1, fmt.Sprintf("Not sent: this turn has made its limit of %d Team Chat requests.", teamTurnMax)
		if box.handled < teamTurnMax {
			box.handled++
			code, text = a.teamRequest(box, b)
		}
		_ = writeFresh(box.root, req+".reply", []byte(fmt.Sprintf("%d\n%s\n", code, strings.TrimRight(text, "\n"))), 0o600)
	}
}

// teamRequest sends one request to the controller: files first, then the
// command with their ids.
func (a *App) teamRequest(box *teamBox, b []byte) (int, string) {
	head, text, _ := strings.Cut(string(b), "\n\n")
	req := map[string]any{"mid": box.team.MID, "token": box.team.Token}
	var args, files []string
	opts := map[string]string{}
	for _, line := range strings.Split(head, "\n") {
		k, v, _ := strings.Cut(line, ": ")
		switch k {
		case "op":
			req["op"] = v
		case "arg":
			args = append(args, v)
		case "opt":
			name, value, _ := strings.Cut(v, "=")
			opts[name] = value
		case "file":
			files = append(files, v)
		}
	}
	if len(text) > linkMaxBytes {
		return 1, fmt.Sprintf("Not sent: a message is at most %d KB; attach longer text with --file.", linkMaxBytes>>10)
	}
	var ids []string
	for _, p := range files {
		id, err := a.teamUpload(box, p)
		if err != nil {
			return 1, "Not sent: " + err.Error()
		}
		ids = append(ids, id)
	}
	req["args"], req["opts"], req["text"], req["files"] = args, opts, text, ids
	out, err := a.teamCall(req)
	if err != nil {
		return 1, "Team Chat did not answer (" + err.Error() + "); check with 'read' before sending again."
	}
	code := int(num(out["code"]))
	return code, str(out["text"])
}

// teamUpload sends a file the agent attaches to the controller in pieces.
func (a *App) teamUpload(box *teamBox, p string) (string, error) {
	fi, err := os.Stat(p)
	if err != nil {
		return "", fmt.Errorf("no file %s", p)
	}
	if !fi.Mode().IsRegular() {
		return "", fmt.Errorf("%s is not a file", p)
	}
	if fi.Size() > teamMaxFile {
		return "", fmt.Errorf("%s is larger than %d MB", p, teamMaxFile>>20)
	}
	f, err := os.Open(p)
	if err != nil {
		return "", err
	}
	defer f.Close()
	buf := make([]byte, teamChunk)
	upload, offset := "", int64(0)
	for {
		n, rerr := io.ReadFull(f, buf)
		if rerr != nil && rerr != io.EOF && rerr != io.ErrUnexpectedEOF {
			return "", rerr
		}
		final := offset+int64(n) >= fi.Size() || rerr != nil
		out, err := a.teamCall(map[string]any{"mid": box.team.MID, "token": box.team.Token, "op": "file_put",
			"upload": map[string]any{"upload_id": upload, "name": filepath.Base(p), "offset": offset,
				"data": base64.StdEncoding.EncodeToString(buf[:n]), "final": final}})
		if err != nil {
			return "", err
		}
		if num(out["code"]) != 0 {
			return "", errors.New(str(out["text"]))
		}
		res, _ := out["file"].(map[string]any)
		if final {
			file, _ := res["file"].(map[string]any)
			if str(file["id"]) == "" {
				return "", errors.New("the file was not stored")
			}
			return str(file["id"]), nil
		}
		upload = str(res["upload_id"])
		offset += int64(n)
	}
}

// teamCall is one request to the controller's team-chat service.
func (a *App) teamCall(req map[string]any) (map[string]any, error) {
	if a.teamCaller != nil {
		return a.teamCaller(req)
	}
	if a.nc == nil {
		return nil, errors.New("this node is not connected")
	}
	subject := "team-chat.rpc"
	if a.prefix != "" {
		subject = a.prefix + "." + subject
	}
	raw, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), teamCallTimeout)
	defer cancel()
	msg, err := a.nc.RequestWithContext(ctx, subject, raw)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(msg.Data, &out); err != nil {
		return nil, errors.New("an invalid answer")
	}
	return out, nil
}

func num(v any) float64 {
	f, _ := v.(float64)
	return f
}

// ---- the agent's folder ------------------------------------------------------

// teamSetup makes (or checks) an agent's folder: AGENT.md (agentMD, with
// {{TEAM}} as the path of its tool), MEMORY.md and notes/ when missing, and
// .team/. reset "full" empties the folder first; "remove" removes it (when
// its agent is deleted) and stops there. It reports whether the
// folder is the user's home or the root.
func teamSetup(folder, agentMD, reset string, checkOnly bool) map[string]any {
	folder = strings.TrimSpace(expandHome(folder))
	if folder == "" || !filepath.IsAbs(folder) {
		return failure("The agent's folder must be an absolute path")
	}
	folder = filepath.Clean(folder)
	home := homeDir()
	isHome := home != "" && folder == filepath.Clean(home)
	isRoot := folder == filepath.VolumeName(folder)+string(filepath.Separator)
	out := map[string]any{"success": true, "folder": folder, "home": isHome, "root": isRoot}
	if st, err := os.Stat(folder); err == nil {
		if !st.IsDir() {
			return failure("%s is not a folder", folder)
		}
		out["exists"] = true
	}
	if checkOnly {
		return out
	}
	if reset == "remove" {
		if isHome || isRoot {
			return failure("%s is not removed", folder)
		}
		if err := os.RemoveAll(folder); err != nil {
			return failure("Could not remove %s: %v", folder, err)
		}
		out["removed"] = true
		return out
	}
	if reset == "full" {
		if isHome || isRoot {
			return failure("Full reset does not empty %s", folder)
		}
		entries, err := os.ReadDir(folder)
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return failure("%v", err)
		}
		for _, e := range entries {
			if err := os.RemoveAll(filepath.Join(folder, e.Name())); err != nil {
				return failure("Could not empty %s: %v", folder, err)
			}
		}
	}
	if err := os.MkdirAll(folder, 0o755); err != nil {
		return failure("Could not make %s: %v", folder, err)
	}
	team, script, err := teamFolders(folder)
	if err != nil {
		return failure("Could not prepare %s: %v", filepath.Join(folder, teamDir), err)
	}
	defer team.Close()
	if err := writeFresh(team, teamScript, []byte(teamScriptFor(filepath.Join(folder, teamDir, "inbox"))), 0o700); err != nil {
		return failure("%v", err)
	}
	if inbox, _, err := openPrivate(team, "inbox"); err == nil {
		inbox.Close()
	}
	if agentMD != "" {
		body := strings.ReplaceAll(agentMD, "{{TEAM}}", shellArg(script))
		p := filepath.Join(folder, "AGENT.md")
		if old, err := os.ReadFile(p); err != nil || string(old) != body {
			if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
				return failure("Could not write AGENT.md: %v", err)
			}
		}
	}
	memory := filepath.Join(folder, "MEMORY.md")
	if _, err := os.Lstat(memory); errors.Is(err, os.ErrNotExist) {
		_ = os.WriteFile(memory, []byte("# Memory\n\nWhat this agent keeps across sessions.\n"), 0o644)
	}
	_ = os.MkdirAll(filepath.Join(folder, "notes"), 0o755)
	out["script"] = script
	return out
}

// teamFilePut writes a piece of a message's file into an agent's folder
// (under files/); exists when the file is there already with that size.
func teamFilePut(folder, name string, offset, size int64, data string, final bool) map[string]any {
	folder = filepath.Clean(expandHome(folder))
	rel := filepath.Clean(filepath.FromSlash(name))
	if !filepath.IsAbs(folder) || filepath.IsAbs(rel) || !strings.HasPrefix(rel, "files"+string(filepath.Separator)) ||
		strings.Contains(rel, "..") {
		return failure("A file goes under files/ in the agent's folder")
	}
	p := filepath.Join(folder, rel)
	if offset == 0 {
		if st, err := os.Lstat(p); err == nil && st.Mode().IsRegular() && st.Size() == size {
			return map[string]any{"success": true, "exists": true, "path": p}
		}
	}
	b, err := base64.StdEncoding.DecodeString(data)
	if err != nil {
		return failure("data is base64")
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return failure("%v", err)
	}
	part := p + ".part"
	flags := os.O_WRONLY | os.O_CREATE | noFollow
	if offset == 0 {
		flags |= os.O_TRUNC
	}
	f, err := os.OpenFile(part, flags, 0o644)
	if err != nil {
		return failure("%v", err)
	}
	if st, err := f.Stat(); err != nil || st.Size() != offset {
		f.Close()
		return failure("the file piece at %d does not follow the last one", offset)
	}
	if _, err := f.WriteAt(b, offset); err != nil {
		f.Close()
		return failure("%v", err)
	}
	if err := f.Close(); err != nil {
		return failure("%v", err)
	}
	if final {
		if err := os.Rename(part, p); err != nil {
			return failure("%v", err)
		}
	}
	return map[string]any{"success": true, "path": p}
}
