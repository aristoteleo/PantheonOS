package pantheoncli

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"
	"text/tabwriter"
	"time"
)

// IsCommand reports whether a first argument is one of the client's.
func IsCommand(name string) bool {
	switch name {
	case "login", "logout", "whoami", "apps", "dev", "desktop", "browser", "nodes", "run", "transfer":
		return true
	}
	return false
}

// Usage is the client's part of `pantheon --help`.
const Usage = `Apps
  pantheon apps list [--running] [--query Q]
  pantheon apps describe <app>
  pantheon apps call <app> <tool> [key=value ...] [--args JSON | --args-file F | -]
  pantheon apps start <app> [--node N] [--version V] [--window] [--job JSON]
  pantheon apps stop <app> [--node N]
  pantheon apps search <query> | install <repository> <version> | uninstall <app>
  pantheon apps versions <app> | set-version <app> <version> | set-node <app> [<node>]
  pantheon apps nodes <app> | policy <app> on_demand|resident | trash | restore <archive_id>
  pantheon dev <app> <action> [key=value ...]     (status, files, read, write, diff, commit, test, tag, ...)

Desktop and browser
  pantheon desktop <action> [key=value ...]       (windows, open, read, update, act, call, inspect, screenshot, ...)
  pantheon browser <action> [key=value ...]       (open, goto, read, click, type, scroll, screenshot, pages, close)

Nodes
  pantheon nodes list | info <node>
  pantheon run --node N [--kind shell|python] -- <command>
  pantheon transfer <src-node>:<path> <dst-node>:<path>

Account
  pantheon login [--hub URL] [--key KEY | --key-stdin | --username U [--password-stdin]]
  pantheon whoami | logout

Every command takes --json (machine-readable output) and --timeout SECONDS.
Method arguments are key=value (values that are JSON are read as JSON), or a
JSON object with --args, --args-file F, or - (stdin).`

// The deployment's Apps are named as in Fleet; the Agent's toolset names are
// accepted too.
var deploymentAliases = map[string]string{
	"file_manager": "files", "integrated_notebook": "notebook", "model_services": "model-management",
}

// The library ids of the deployment's Apps, left out of the library listing.
var deploymentAppIDs = []string{"file-manager", "shell", "integrated-notebook", "web", "evolution",
	"desktop", "fleet", "model-services-management"}

type runner struct {
	ctx    context.Context
	stdin  io.Reader
	stdout io.Writer
	stderr io.Writer
	p      *parsed
	json   bool
	s      *Session
}

// Main runs a client command and returns the process exit code.
func Main(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, Usage)
		return 2
	}
	p, err := parse(args[1:], stdin)
	if err != nil {
		fmt.Fprintln(stderr, "pantheon:", err)
		return 2
	}
	r := &runner{ctx: context.Background(), stdin: stdin, stdout: stdout, stderr: stderr, p: p, json: p.bools["json"]}
	if p.bools["help"] || p.bools["h"] {
		fmt.Fprintln(stdout, Usage)
		return 0
	}
	switch args[0] {
	case "login":
		err = r.login()
	case "logout":
		err = r.logout()
	default:
		err = r.signedIn(args[0])
	}
	if err != nil {
		if r.json {
			_ = json.NewEncoder(stdout).Encode(map[string]any{"success": false, "error": err.Error()})
		} else {
			fmt.Fprintln(stderr, "pantheon:", err)
		}
		return 1
	}
	return 0
}

func (r *runner) signedIn(group string) error {
	creds, err := LoadCredentials()
	if err != nil {
		return err
	}
	if r.s, err = NewSession(creds); err != nil {
		return err
	}
	switch group {
	case "whoami":
		return r.whoami()
	case "apps":
		return r.apps()
	case "dev":
		return r.dev()
	case "desktop":
		return r.window("desktop")
	case "browser":
		return r.window("browser")
	case "nodes":
		return r.nodes()
	case "run":
		return r.run()
	case "transfer":
		return r.transfer()
	}
	return fmt.Errorf("unknown command %q", group)
}

// ---- output ----

func (r *runner) print(result json.RawMessage) error {
	if err := Succeeded(result); err != nil {
		return err
	}
	var v any
	if json.Unmarshal(result, &v) != nil {
		_, err := r.stdout.Write(append(result, '\n'))
		return err
	}
	// A result that is a plain string reads better unquoted.
	if text, ok := v.(string); ok && !r.json {
		_, err := fmt.Fprintln(r.stdout, text)
		return err
	}
	enc := json.NewEncoder(r.stdout)
	enc.SetEscapeHTML(false)
	if !r.json {
		enc.SetIndent("", "  ")
	}
	return enc.Encode(v)
}

func (r *runner) emit(v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return r.print(data)
}

// ---- account ----

func (r *runner) whoami() error {
	id, err := r.s.Identify(r.ctx)
	if err != nil {
		return err
	}
	out := map[string]any{"hub": r.s.Creds.Hub, "fleet_id": id.FleetID, "controller": id.ControllerURL,
		"key": keyPrefix(r.s.Creds.Key)}
	if r.s.Creds.User != "" {
		out["user"] = r.s.Creds.User
	}
	if r.json {
		return r.emit(out)
	}
	if r.s.Creds.User != "" {
		fmt.Fprintf(r.stdout, "Signed in to %s as %s\n", r.s.Creds.Hub, r.s.Creds.User)
	} else {
		fmt.Fprintf(r.stdout, "Signed in to %s\n", r.s.Creds.Hub)
	}
	fmt.Fprintf(r.stdout, "Fleet %s, key %s\n", id.FleetID, keyPrefix(r.s.Creds.Key))
	return nil
}

func keyPrefix(key string) string {
	if len(key) > 12 {
		return key[:12] + "…"
	}
	return "…"
}

func (r *runner) logout() error {
	if err := RemoveCredentials(); err != nil {
		return err
	}
	if path, err := grantCachePath(); err == nil {
		_ = os.Remove(path)
	}
	fmt.Fprintln(r.stdout, "Signed out. The key still exists: revoke it in PantheonOS (Settings → Keys) if no longer needed.")
	return nil
}

// ---- Apps ----

// resolved is an App the command names: a deployment App, or a library App
// reached through the Desktop.
type resolved struct {
	name       string
	deployment bool
	appID      string
}

var nameRE = regexp.MustCompile(`[^A-Za-z0-9]+`)

func normalize(name string) string { return strings.Trim(nameRE.ReplaceAllString(strings.ToLower(name), "_"), "_") }

func (r *runner) resolve(name string, dep *DeploymentStatus) (resolved, error) {
	if alias, ok := deploymentAliases[name]; ok {
		name = alias
	}
	if _, ok := dep.Status.Apps[name]; ok {
		return resolved{name: name, deployment: true}, nil
	}
	dir, err := r.directory(dep, false)
	if err != nil {
		return resolved{}, err
	}
	for _, app := range dir {
		id, _ := app["app_id"].(string)
		if id == name || normalize(id) == normalize(name) {
			return resolved{name: name, appID: id}, nil
		}
	}
	// Not installed (yet): management actions name it by its id.
	return resolved{name: name, appID: name}, nil
}

func (r *runner) desktopCall(dep *DeploymentStatus, method string, args any, timeout time.Duration) (json.RawMessage, error) {
	b, err := dep.AppBinding("desktop")
	if err != nil {
		return nil, err
	}
	return r.s.Call(r.ctx, b, method, args, timeout)
}

func (r *runner) directory(dep *DeploymentStatus, state bool) ([]map[string]any, error) {
	result, err := r.desktopCall(dep, "agent_app_directory",
		map[string]any{"exclude": deploymentAppIDs, "state": state}, 90*time.Second)
	if err != nil {
		return nil, err
	}
	if err := Succeeded(result); err != nil {
		return nil, err
	}
	var reply struct {
		Apps []map[string]any `json:"apps"`
	}
	if err := json.Unmarshal(result, &reply); err != nil {
		return nil, errors.New("the Desktop returned an invalid App list")
	}
	return reply.Apps, nil
}

func (r *runner) apps() error {
	if len(r.p.positional) == 0 {
		return errors.New("usage: pantheon apps list|describe|call|start|stop|search|install|... (pantheon --help)")
	}
	action, rest := r.p.positional[0], r.p.positional[1:]
	dep, err := r.s.Deployment(r.ctx)
	if err != nil {
		return err
	}
	arg := func(i int) string {
		if i < len(rest) {
			return rest[i]
		}
		return ""
	}
	switch action {
	case "list":
		return r.appsList(dep)
	case "describe":
		if len(rest) != 1 {
			return errors.New("usage: pantheon apps describe <app>")
		}
		return r.appsDescribe(dep, rest[0])
	case "call":
		if len(rest) != 2 {
			return errors.New("usage: pantheon apps call <app> <tool> [key=value ...]")
		}
		return r.appsCall(dep, rest[0], rest[1])
	case "start", "stop":
		if len(rest) != 1 {
			return fmt.Errorf("usage: pantheon apps %s <app>", action)
		}
		app, err := r.resolve(rest[0], dep)
		if err != nil {
			return err
		}
		if app.deployment {
			return fmt.Errorf("%s is part of your Agent deployment: start and stop it in Fleet", app.name)
		}
		args := map[string]any{"app_id": app.appID, "node": r.p.flags["node"]}
		method := "agent_app_stop"
		if action == "start" {
			method = "agent_app_start"
			args["version"], args["window"] = r.p.flags["version"], r.p.bools["window"]
			if job := r.p.flags["job"]; job != "" {
				var v map[string]any
				if err := json.Unmarshal([]byte(job), &v); err != nil {
					return errors.New("--job takes a JSON object")
				}
				args["job"] = v
			}
		}
		timeout, err := r.p.timeout(600 * time.Second)
		if err != nil {
			return err
		}
		result, err := r.desktopCall(dep, method, args, timeout)
		if err != nil {
			return err
		}
		return r.print(result)
	}
	// Management: pantheonos manage actions, by the CLI's names.
	manage := map[string]string{"search": "search", "inspect": "inspect", "install": "install",
		"uninstall": "uninstall", "trash": "trash", "restore": "restore", "versions": "versions",
		"set-version": "set_version", "set-node": "set_node", "nodes": "nodes", "policy": "set_policy"}
	op, ok := manage[action]
	if !ok {
		return fmt.Errorf("unknown action %q (pantheon --help)", action)
	}
	args := map[string]any{}
	for k, v := range r.p.args {
		args[k] = v
	}
	appName := ""
	switch op {
	case "search":
		args["query"] = strings.Join(rest, " ")
	case "inspect":
		args["repository"] = arg(0)
	case "install":
		args["repository"], args["version"] = arg(0), arg(1)
	case "restore":
		args["archive_id"] = arg(0)
	case "set_version":
		appName, args["version"] = arg(0), arg(1)
	case "set_node":
		appName, args["node"] = arg(0), arg(1)
	case "set_policy":
		appName, args["policy"] = arg(0), arg(1)
	case "trash":
	default:
		appName = arg(0)
	}
	appID := ""
	if appName != "" {
		app, err := r.resolve(appName, dep)
		if err != nil {
			return err
		}
		if app.deployment {
			return fmt.Errorf("%s is part of your Agent deployment: manage it in Fleet", app.name)
		}
		appID = app.appID
	}
	timeout, err := r.p.timeout(300 * time.Second)
	if err != nil {
		return err
	}
	result, err := r.desktopCall(dep, "agent_app_manage", map[string]any{"action": op, "app_id": appID, "args": args}, timeout)
	if err != nil {
		return err
	}
	return r.print(result)
}

func (r *runner) appsList(dep *DeploymentStatus) error {
	type row struct {
		App     string   `json:"app"`
		Source  string   `json:"source"`
		State   string   `json:"state"`
		Node    string   `json:"node,omitempty"`
		Window  bool     `json:"window,omitempty"`
		Policy  string   `json:"policy,omitempty"`
		Tools   []string `json:"tools,omitempty"`
		Actions []string `json:"actions,omitempty"`
		About   string   `json:"description,omitempty"`
	}
	var rows []row
	for _, name := range dep.AppNames() {
		a := dep.Status.Apps[name]
		rows = append(rows, row{App: name, Source: "deployment", State: a.State, Node: a.NodeID})
	}
	dir, err := r.directory(dep, r.p.bools["running"])
	if err != nil {
		fmt.Fprintln(r.stderr, "pantheon: the library is not listed:", err)
	}
	for _, app := range dir {
		x := row{Source: "library", State: "stopped"}
		x.App, _ = app["app_id"].(string)
		x.Window, _ = app["window"].(bool)
		x.Policy, _ = app["policy"].(string)
		x.About, _ = app["description"].(string)
		if i := strings.IndexByte(x.About, '\n'); i >= 0 {
			x.About = x.About[:i]
		}
		for _, key := range []string{"tools", "actions"} {
			list, _ := app[key].([]any)
			for _, f := range list {
				if m, ok := f.(map[string]any); ok {
					if n, ok := m["name"].(string); ok {
						if key == "tools" {
							x.Tools = append(x.Tools, n)
						} else {
							x.Actions = append(x.Actions, n)
						}
					}
				}
			}
		}
		running, hasState := app["instances"].([]any)
		if !hasState {
			x.State = ""
		}
		for _, inst := range running {
			if m, ok := inst.(map[string]any); ok {
				x.State, _ = m["state"].(string)
				x.Node, _ = m["node"].(string)
			}
		}
		if r.p.bools["running"] && len(running) == 0 {
			continue
		}
		rows = append(rows, x)
	}
	if q := strings.ToLower(r.p.flags["query"]); q != "" {
		kept := rows[:0]
		for _, x := range rows {
			if strings.Contains(strings.ToLower(x.App+" "+x.About), q) {
				kept = append(kept, x)
			}
		}
		rows = kept
	}
	names := r.nodeNames()
	for i := range rows {
		if name, ok := names[rows[i].Node]; ok {
			rows[i].Node = name
		}
	}
	if r.json {
		return r.emit(map[string]any{"apps": rows})
	}
	w := tabwriter.NewWriter(r.stdout, 0, 2, 2, ' ', 0)
	fmt.Fprintln(w, "APP\tSOURCE\tSTATE\tNODE\tOFFERS\tDESCRIPTION")
	for _, x := range rows {
		var offers []string
		if len(x.Tools) > 0 {
			offers = append(offers, fmt.Sprintf("%d tools", len(x.Tools)))
		}
		if len(x.Actions) > 0 {
			offers = append(offers, fmt.Sprintf("%d actions", len(x.Actions)))
		}
		if x.Window {
			offers = append(offers, "window")
		}
		about := x.About
		if len(about) > 60 {
			about = about[:57] + "..."
		}
		fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\t%s\n", x.App, x.Source, dash(x.State), dash(x.Node), dash(strings.Join(offers, ", ")), about)
	}
	return w.Flush()
}

func dash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func (r *runner) appsDescribe(dep *DeploymentStatus, name string) error {
	app, err := r.resolve(name, dep)
	if err != nil {
		return err
	}
	if app.deployment {
		b, err := dep.AppBinding(app.name)
		if err != nil {
			return err
		}
		result, err := r.s.Call(r.ctx, b, "list_tools", nil, 60*time.Second)
		if err != nil {
			return err
		}
		return r.print(result)
	}
	result, err := r.desktopCall(dep, "agent_app_describe", map[string]any{"app_id": app.appID}, 90*time.Second)
	if err != nil {
		return err
	}
	return r.print(result)
}

func (r *runner) appsCall(dep *DeploymentStatus, name, tool string) error {
	app, err := r.resolve(name, dep)
	if err != nil {
		return err
	}
	timeout, err := r.p.timeout(600 * time.Second)
	if err != nil {
		return err
	}
	if app.deployment {
		b, err := dep.AppBinding(app.name)
		if err != nil {
			return err
		}
		result, err := r.s.Call(r.ctx, b, tool, r.p.args, timeout)
		if err != nil {
			return err
		}
		return r.print(result)
	}
	args := map[string]any{"app_id": app.appID, "name": tool, "args": r.p.args, "timeout_s": int(timeout.Seconds())}
	if node := r.p.flags["node"]; node != "" {
		args["node"] = node
	}
	if version := r.p.flags["version"]; version != "" {
		args["version"] = version
	}
	result, err := r.desktopCall(dep, "agent_app_call", args, timeout)
	if err != nil {
		return err
	}
	return r.print(result)
}

func (r *runner) dev() error {
	if len(r.p.positional) != 2 {
		return errors.New("usage: pantheon dev <app> <action> [key=value ...]")
	}
	dep, err := r.s.Deployment(r.ctx)
	if err != nil {
		return err
	}
	app, err := r.resolve(r.p.positional[0], dep)
	if err != nil {
		return err
	}
	if app.deployment {
		return fmt.Errorf("%s is part of your Agent deployment; develop it from its source repository", app.name)
	}
	timeout, err := r.p.timeout(300 * time.Second)
	if err != nil {
		return err
	}
	action := strings.ReplaceAll(r.p.positional[1], "-", "_")
	result, err := r.desktopCall(dep, "agent_app_develop", map[string]any{"action": action, "app_id": app.appID, "args": r.p.args}, timeout)
	if err != nil {
		return err
	}
	return r.print(result)
}

// ---- desktop and browser ----

// How long the Desktop may take, by action (the web UI's own limits).
var windowTimeouts = map[string]time.Duration{
	"desktop_open": 300 * time.Second, "desktop_call": 90 * time.Second, "browser_open": 450 * time.Second,
	"browser_goto": 330 * time.Second,
}

func (r *runner) window(group string) error {
	if len(r.p.positional) != 1 {
		return fmt.Errorf("usage: pantheon %s <action> [key=value ...]", group)
	}
	method := group + "_" + strings.ReplaceAll(r.p.positional[0], "-", "_")
	def, ok := windowTimeouts[method]
	if !ok {
		def = 120 * time.Second
	}
	timeout, err := r.p.timeout(def)
	if err != nil {
		return err
	}
	dep, err := r.s.Deployment(r.ctx)
	if err != nil {
		return err
	}
	result, err := r.desktopCall(dep, method, r.p.args, timeout)
	if err != nil {
		return err
	}
	return r.print(result)
}

// ---- nodes ----

func (r *runner) fleet(method string, args any, timeout time.Duration) (json.RawMessage, error) {
	return r.s.DeploymentAppCall(r.ctx, "fleet", method, args, timeout)
}

func (r *runner) nodes() error {
	if len(r.p.positional) == 0 {
		return errors.New("usage: pantheon nodes list | info <node>")
	}
	switch r.p.positional[0] {
	case "list":
		result, err := r.fleet("fleet_list_nodes", nil, 60*time.Second)
		if err != nil {
			return err
		}
		if r.json {
			return r.print(result)
		}
		if err := Succeeded(result); err != nil {
			return err
		}
		var reply struct {
			Nodes []map[string]any `json:"nodes"`
		}
		if err := json.Unmarshal(result, &reply); err != nil {
			return r.print(result)
		}
		sort.Slice(reply.Nodes, func(i, j int) bool { return fmt.Sprint(reply.Nodes[i]["name"]) < fmt.Sprint(reply.Nodes[j]["name"]) })
		w := tabwriter.NewWriter(r.stdout, 0, 2, 2, ' ', 0)
		fmt.Fprintln(w, "NAME\tNODE\tSTATUS\tOS\tKIND")
		for _, n := range reply.Nodes {
			fmt.Fprintf(w, "%v\t%v\t%v\t%v/%v\t%s\n", n["name"], n["node_id"], n["status"], n["os"], n["arch"], dash(fmt.Sprint(valueOr(n["kind"], ""))))
		}
		return w.Flush()
	case "info":
		if len(r.p.positional) != 2 {
			return errors.New("usage: pantheon nodes info <node>")
		}
		node, err := r.nodeID(r.p.positional[1])
		if err != nil {
			return err
		}
		result, err := r.fleet("fleet_node_info", map[string]any{"node_id": node}, 60*time.Second)
		if err != nil {
			return err
		}
		return r.print(result)
	}
	return fmt.Errorf("unknown action %q", r.p.positional[0])
}

// nodeNames maps node ids to names, or nothing when Fleet does not answer.
func (r *runner) nodeNames() map[string]string {
	names := map[string]string{}
	result, err := r.fleet("fleet_list_nodes", nil, 30*time.Second)
	if err != nil {
		return names
	}
	var reply struct {
		Nodes []struct {
			NodeID string `json:"node_id"`
			Name   string `json:"name"`
		} `json:"nodes"`
	}
	if json.Unmarshal(result, &reply) == nil {
		for _, n := range reply.Nodes {
			names[n.NodeID] = n.Name
		}
	}
	return names
}

// nodeID accepts a node id or a node name.
func (r *runner) nodeID(node string) (string, error) {
	if strings.HasPrefix(node, "n_") {
		return node, nil
	}
	result, err := r.fleet("fleet_list_nodes", nil, 60*time.Second)
	if err != nil {
		return "", err
	}
	var reply struct {
		Nodes []struct {
			NodeID string `json:"node_id"`
			Name   string `json:"name"`
		} `json:"nodes"`
	}
	if json.Unmarshal(result, &reply) == nil {
		for _, n := range reply.Nodes {
			if n.Name == node || strings.TrimSuffix(n.Name, ".local") == node {
				return n.NodeID, nil
			}
		}
	}
	return "", fmt.Errorf("no node %q in your Fleet (pantheon nodes list)", node)
}

func (r *runner) run() error {
	if r.p.flags["node"] == "" || len(r.p.rest) == 0 {
		return errors.New("usage: pantheon run --node N [--kind shell|python] -- <command>")
	}
	node, err := r.nodeID(r.p.flags["node"])
	if err != nil {
		return err
	}
	timeout, err := r.p.timeout(600 * time.Second)
	if err != nil {
		return err
	}
	kind := r.p.flags["kind"]
	if kind == "" {
		kind = "shell"
	}
	code := strings.Join(r.p.rest, " ")
	if len(r.p.rest) == 1 && r.p.rest[0] == "-" {
		data, err := io.ReadAll(bufio.NewReader(r.stdin))
		if err != nil {
			return err
		}
		code = string(data)
	}
	result, err := r.fleet("run_on_node", map[string]any{"node_id": node, "code": code, "kind": kind,
		"timeout": int(timeout.Seconds())}, timeout)
	if err != nil {
		return err
	}
	if r.json {
		return r.print(result)
	}
	if err := Succeeded(result); err != nil {
		return err
	}
	var reply map[string]any
	if json.Unmarshal(result, &reply) == nil {
		for _, key := range []string{"stdout", "output", "result"} {
			if text, ok := reply[key].(string); ok {
				fmt.Fprint(r.stdout, text)
				if !strings.HasSuffix(text, "\n") && text != "" {
					fmt.Fprintln(r.stdout)
				}
				if errText, ok := reply["stderr"].(string); ok && errText != "" {
					fmt.Fprint(r.stderr, errText)
				}
				if code, ok := reply["exit_code"].(float64); ok && code != 0 {
					return fmt.Errorf("the command exited with %d", int(code))
				}
				return nil
			}
		}
	}
	return r.print(result)
}

func (r *runner) transfer() error {
	if len(r.p.positional) != 2 {
		return errors.New("usage: pantheon transfer <src-node>:<path> <dst-node>:<path>")
	}
	var ends [2][2]string
	for i, spec := range r.p.positional {
		node, path, ok := strings.Cut(spec, ":")
		if !ok || node == "" || path == "" {
			return fmt.Errorf("%q is not <node>:<path>", spec)
		}
		id, err := r.nodeID(node)
		if err != nil {
			return err
		}
		ends[i] = [2]string{id, path}
	}
	timeout, err := r.p.timeout(3600 * time.Second)
	if err != nil {
		return err
	}
	result, err := r.fleet("transfer", map[string]any{"src_node": ends[0][0], "src_path": ends[0][1],
		"dst_node": ends[1][0], "dst_path": ends[1][1], "wait": true, "timeout": int(timeout.Seconds())}, timeout)
	if err != nil {
		return err
	}
	return r.print(result)
}

func valueOr(v any, def any) any {
	if v == nil {
		return def
	}
	return v
}
