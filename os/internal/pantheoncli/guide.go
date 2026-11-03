package pantheoncli

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"text/tabwriter"
	"time"
)

// What a command says when it cannot do what was asked: what is missing and
// the command to run next. An agent with only a shell reads nothing else.

// groupHelp is `pantheon <group> --help` for the groups whose actions are
// not spelled out in Usage.
var groupHelp = map[string]string{
	"browser": `pantheon browser — a real web browser on PantheonOS; the user can watch and use the same page
  pantheon browser open url=https://example.com      open a page; prints its page_id and title
                                                     (shown as a window on the user's desktop; show=false: without one)
  pantheon browser pages                             the open pages
  pantheon browser read [page_id=P]                  title, URL, text and the clickable elements with their selectors
  pantheon browser goto url=https://… [page_id=P]
  pantheon browser click selector='…' [page_id=P]    a selector from browser read
  pantheon browser type selector='…' text='…' [submit=true]
  pantheon browser scroll dy=800 | to=bottom
  pantheon browser screenshot [page_id=P]            prints the image's path
  pantheon browser close page_id=P
Without page_id a command acts on the newest page.`,
	"desktop": `pantheon desktop — the windows on the user's PantheonOS desktop (their web desktop)
  pantheon desktop windows                           the open windows, their window_id and actions
  pantheon desktop open path=/abs/file [node_id=N]   open a file in its App, as a double-click does
                                                     (node_id: the node that has the file; default the Workspace)
  pantheon desktop open app=<app> [state='{…}']      open an App (pantheon apps list shows them)
  pantheon desktop read window_id=W                  what a window shows
  pantheon desktop call window_id=W action=A args='{…}'   one of the window's actions (see desktop windows)
  pantheon desktop update window_id=W patch='{…}' | set window_id=W state='{…}'
  pantheon desktop screenshot [window_id=W]          prints the image's path
Opening, acting on and capturing windows needs the desktop open in the user's browser.`,
	"skills": `pantheon skills — the user's PantheonOS skills: written procedures for recurring tasks
  pantheon skills list                               every skill: name, scope and what it is for
  pantheon skills view <name>                        a skill's instructions (SKILL.md)
  pantheon skills view <name> <file>                 one of its files (references/…, scripts/…)
In a Pantheon Agent conversation also: search <query> | browse | adopt <name> (the Store),
create | update | patch | delete <name>, rate <name>, publish <name>.`,
}

// agentOnly is said for a command that belongs to a Pantheon Agent
// conversation, run outside one.
func agentOnly(group, action string) error {
	what := map[string]string{
		"task":   "reports progress and outputs to the Pantheon Agent conversation that runs it",
		"agent":  "starts and follows sub-agents of the Pantheon Agent conversation that runs it",
		"jobs":   "follows the background commands of the Pantheon Agent conversation that runs it",
		"skills": "changes the Pantheon Agent's skills from one of its conversations",
	}[group]
	next := "Outside one, report in your own reply; to hand work to the Pantheon Agent: pantheon agent chat -i \"<message>\""
	if group == "skills" {
		next = "From here: pantheon skills list, pantheon skills view <name>; to change skills, ask the Pantheon Agent: pantheon agent chat -i \"<message>\""
	}
	command := strings.TrimSpace("pantheon " + group + " " + action)
	return fmt.Errorf("`%s` %s, and this command is not run by one. %s", command, what, next)
}

// ---- skills, outside a conversation ----

func (r *runner) ownerSkills() error {
	pos := r.p.positional
	action := "list"
	if len(pos) > 0 {
		action = pos[0]
	}
	if action != "list" && action != "view" {
		return agentOnly("skills", action)
	}
	if action == "view" && len(pos) < 2 {
		return errors.New("usage: pantheon skills view <name> [<file>] (pantheon skills list shows the names)")
	}
	dep, err := r.s.Deployment(r.ctx)
	if err != nil {
		return err
	}
	b, err := dep.AppBinding("agent")
	if err != nil {
		return fmt.Errorf("the skills live with your Pantheon Agent, which is not running: %w", err)
	}
	args := map[string]any{"action": action}
	if action == "view" {
		args["name"] = pos[1]
		if len(pos) > 2 {
			args["file"] = pos[2]
		}
	}
	result, err := r.s.Call(r.ctx, b, "agent_skills", args, 60*time.Second)
	if err != nil && strings.Contains(err.Error(), "No registered method") {
		// An Agent released before agent_skills: its private skill folders.
		return r.ownSkillFiles(b, args)
	}
	if err != nil {
		return err
	}
	return r.printSkills(action, result, "")
}

type skillRow struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Scope       string `json:"scope"`
}

func (r *runner) printSkills(action string, result json.RawMessage, note string) error {
	if err := Succeeded(result); err != nil {
		return err
	}
	if r.json {
		return r.print(result)
	}
	if action == "list" {
		var reply struct {
			Skills []skillRow `json:"skills"`
		}
		if err := json.Unmarshal(result, &reply); err != nil {
			return r.print(result)
		}
		if len(reply.Skills) == 0 {
			fmt.Fprintln(r.stdout, "No skills yet.")
		} else {
			w := tabwriter.NewWriter(r.stdout, 0, 2, 2, ' ', 0)
			fmt.Fprintln(w, "NAME\tSCOPE\tDESCRIPTION")
			for _, s := range reply.Skills {
				fmt.Fprintf(w, "%s\t%s\t%s\n", s.Name, dash(s.Scope), shorten(s.Description, 100))
			}
			_ = w.Flush()
			fmt.Fprintln(r.stdout, "\nRead one: pantheon skills view <name>")
		}
		if note != "" {
			fmt.Fprintln(r.stdout, note)
		}
		return nil
	}
	var view struct {
		Name        string   `json:"name"`
		Description string   `json:"description"`
		File        string   `json:"file"`
		Content     string   `json:"content"`
		Files       []string `json:"files"`
		Hint        string   `json:"hint"`
	}
	if err := json.Unmarshal(result, &view); err != nil {
		return r.print(result)
	}
	if view.File == "" {
		fmt.Fprintf(r.stdout, "# skill %s", view.Name)
		if view.Description != "" {
			fmt.Fprintf(r.stdout, ": %s", view.Description)
		}
		fmt.Fprint(r.stdout, "\n\n")
	}
	fmt.Fprintln(r.stdout, strings.TrimRight(view.Content, "\n"))
	if len(view.Files) > 0 {
		fmt.Fprintf(r.stdout, "\nFiles: %s\n%s\n", strings.Join(view.Files, ", "), view.Hint)
	}
	return nil
}

func shorten(s string, n int) string {
	s = strings.Join(strings.Fields(s), " ")
	if r := []rune(s); len(r) > n {
		return string(r[:n-1]) + "…"
	}
	return s
}

// ownSkillFiles lists or reads the user's own skills through the Agent's
// skill folders, for an Agent without agent_skills.
func (r *runner) ownSkillFiles(b Binding, args map[string]any) error {
	const older = "(Your Pantheon Agent is an older release: this lists your own skills; the skills it ships with are listed after its update.)"
	if args["action"] == "list" {
		var rows []skillRow
		for _, scope := range []string{"project", "global"} {
			result, err := r.s.Call(r.ctx, b, "agent_skill_files", map[string]any{"operation": "list", "scope": scope}, 60*time.Second)
			if err != nil {
				return err
			}
			var reply struct {
				Files []struct {
					Name string `json:"name"`
					Type string `json:"type"`
				} `json:"files"`
			}
			_ = json.Unmarshal(result, &reply)
			for _, f := range reply.Files {
				if f.Type == "directory" {
					rows = append(rows, skillRow{Name: f.Name, Scope: scope})
				}
			}
		}
		data, _ := json.Marshal(map[string]any{"success": true, "count": len(rows), "skills": rows, "note": older})
		return r.printSkills("list", data, older)
	}
	name, _ := args["name"].(string)
	file, _ := args["file"].(string)
	if file == "" {
		file = "SKILL.md"
	}
	for _, scope := range []string{"project", "global"} {
		result, err := r.s.Call(r.ctx, b, "agent_skill_files", map[string]any{"operation": "read", "scope": scope,
			"path": strings.Trim(name, "/") + "/" + file}, 60*time.Second)
		if err != nil {
			continue
		}
		var reply struct {
			Data string `json:"data"`
		}
		if json.Unmarshal(result, &reply) != nil {
			continue
		}
		content, err := base64.StdEncoding.DecodeString(reply.Data)
		if err != nil {
			continue
		}
		data, _ := json.Marshal(map[string]any{"success": true, "name": name, "file": strings.TrimPrefix(file, "SKILL.md"),
			"content": string(content)})
		return r.printSkills("view", data, "")
	}
	return fmt.Errorf("no skill %q among your own skills; pantheon skills list shows them %s", name, older)
}

// ---- desktop and browser ----

// noDesktop is the Desktop's answer when no browser shows the desktop.
const noDesktop = "no desktop to ask"

// desktopAdvice adds what to do to a failure that needs the desktop open.
func (r *runner) desktopAdvice(message string) string {
	if !strings.Contains(message, noDesktop) {
		return message
	}
	where := "the PantheonOS desktop"
	if r.s != nil && r.s.Creds != nil && r.s.Creds.Hub != "" {
		where += " (" + strings.TrimRight(r.s.Creds.Hub, "/") + "/desktop.html)"
	}
	return message + ". Opening, acting on or capturing windows needs " + where +
		" open in the user's browser, and it is not open now. Ask the user to open it, then run this again." +
		" Meanwhile: pantheon desktop windows lists the windows; pantheon browser open url=… show=false browses without a window."
}

// windowResult prints a desktop or browser result, with the next step where
// the result alone leaves the caller stuck.
func (r *runner) windowResult(group, action string, result json.RawMessage) error {
	var v map[string]any
	if json.Unmarshal(result, &v) != nil {
		return r.print(result)
	}
	failed := v["success"] == false
	message := fmt.Sprint(v["error"])
	switch {
	case group == "browser" && action == "open" && failed && strings.Contains(message, noDesktop) && v["page_id"] != nil:
		// The page opened; only showing it failed.
		v["success"], v["shown"] = true, false
		delete(v, "error")
		delete(v, "show_error")
		v["note"] = fmt.Sprintf("The page is open but not shown: the user's PantheonOS desktop is not open in a browser. "+
			"Use it with pantheon browser read page_id=%v (and click, type, screenshot). "+
			"Pass show=false to open pages without a window.", v["page_id"])
		return r.emit(v)
	case failed:
		if message == "<nil>" {
			return r.print(result)
		}
		return &RPCError{Message: r.desktopAdvice(message)}
	case group == "browser" && action == "pages":
		pages, _ := v["pages"].([]any)
		if r.json {
			if len(pages) == 0 {
				v["hint"] = "No pages are open. Open one: pantheon browser open url=https://example.com"
			}
			return r.emit(v)
		}
		if len(pages) == 0 {
			fmt.Fprintln(r.stdout, "No browser pages are open.\nOpen one: pantheon browser open url=https://example.com   (show=false: without a desktop window)")
			return nil
		}
		w := tabwriter.NewWriter(r.stdout, 0, 2, 2, ' ', 0)
		fmt.Fprintln(w, "PAGE\tTITLE\tURL")
		for _, p := range pages {
			page, _ := p.(map[string]any)
			fmt.Fprintf(w, "%v\t%s\t%v\n", page["page_id"], shorten(fmt.Sprint(valueOr(page["title"], "")), 60), page["url"])
		}
		_ = w.Flush()
		fmt.Fprintln(r.stdout, "\nThe newest is used when page_id is not given: pantheon browser read [page_id=P]")
		return nil
	}
	return r.print(result)
}
