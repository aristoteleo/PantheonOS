package pantheoncli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// agentLink is how a command run by a Pantheon agent reaches that agent
// (docs/pantheon-cli.md): the Agent App's endpoint, a grant for its
// pantheon_command method only, and a token for this conversation, agent and
// tool call. Every request is carried out by the Agent, as that agent, with
// its grants and its apps filter.
type agentLink struct {
	endpoint string
	grant    string
	token    string
	http     *http.Client
}

func agentFromEnv() *agentLink {
	endpoint, grant, token := os.Getenv("PANTHEON_ENDPOINT"), os.Getenv("PANTHEON_GRANT"), os.Getenv("PANTHEON_TOKEN")
	if endpoint == "" || grant == "" || token == "" {
		return nil
	}
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.Host == "" {
		return nil
	}
	return &agentLink{endpoint: "https://" + u.Host + "/rpc", grant: grant, token: token, http: newHTTPClient()}
}

func (a *agentLink) do(ctx context.Context, op string, params map[string]any, timeout time.Duration) (json.RawMessage, error) {
	if params == nil {
		params = map[string]any{}
	}
	seconds := int(timeout.Seconds())
	if seconds > 600 {
		seconds = 600
	}
	body := map[string]any{"method": "pantheon_command", "timeout_seconds": seconds,
		"args": map[string]any{"token": a.token, "request": map[string]any{"op": op, "params": params}}}
	s := &Session{http: a.http}
	var envelope struct {
		Success bool            `json:"success"`
		Result  json.RawMessage `json:"result"`
		Error   any             `json:"error"`
	}
	if err := s.request(ctx, http.MethodPost, a.endpoint, a.grant, body, &envelope, timeout+30*time.Second); err != nil {
		var httpErr *HTTPError
		if errors.As(err, &httpErr) && (httpErr.Status == 401 || httpErr.Status == 403) {
			return nil, errors.New("the Agent no longer accepts this command's requests (its run ended or the Agent restarted)")
		}
		return nil, err
	}
	if !envelope.Success {
		return nil, &RPCError{Message: fmt.Sprint(valueOr(envelope.Error, "the Agent reported a failure"))}
	}
	var reply struct {
		Success bool            `json:"success"`
		Result  json.RawMessage `json:"result"`
		Error   any             `json:"error"`
	}
	if err := json.Unmarshal(envelope.Result, &reply); err != nil {
		return nil, errors.New("the Agent returned an invalid reply")
	}
	if !reply.Success {
		return nil, &RPCError{Message: fmt.Sprint(valueOr(reply.Error, "the Agent reported a failure"))}
	}
	return reply.Result, nil
}

// viaAgent runs a command through the agent that ran it.
func (r *runner) viaAgent(link *agentLink, group string) error {
	pos := r.p.positional
	arg := func(i int) string {
		if i < len(pos) {
			return pos[i]
		}
		return ""
	}
	send := func(op string, params map[string]any, def time.Duration) error {
		timeout, err := r.p.timeout(def)
		if err != nil {
			return err
		}
		result, err := link.do(r.ctx, op, params, timeout)
		if err != nil {
			return err
		}
		return r.print(result)
	}
	switch group {
	case "whoami":
		fmt.Fprintln(r.stdout, "Running in a Pantheon agent's conversation: requests go to that agent.")
		return nil
	case "apps":
		action := arg(0)
		switch action {
		case "list":
			return send("apps.list", map[string]any{"query": r.p.flags["query"]}, 120*time.Second)
		case "describe":
			return send("apps.describe", map[string]any{"app": arg(1)}, 120*time.Second)
		case "call":
			if len(pos) != 3 {
				return errors.New("usage: pantheon apps call <app> <tool> [key=value ...]")
			}
			return send("apps.call", map[string]any{"app": arg(1), "tool": arg(2), "args": r.p.args}, 600*time.Second)
		case "start":
			params := map[string]any{"app": arg(1), "node": r.p.flags["node"], "version": r.p.flags["version"],
				"window": r.p.bools["window"]}
			if job := r.p.flags["job"]; job != "" {
				var v map[string]any
				if err := json.Unmarshal([]byte(job), &v); err != nil {
					return errors.New("--job takes a JSON object")
				}
				params["job"] = v
			}
			return send("apps.start", params, 600*time.Second)
		case "stop":
			return send("apps.stop", map[string]any{"app": arg(1), "node": r.p.flags["node"]}, 300*time.Second)
		}
		op, app, args, err := manageRequest(action, pos[min(1, len(pos)):], r.p.args)
		if err != nil {
			return err
		}
		return send("apps.manage", map[string]any{"action": op, "app": app, "args": args}, 300*time.Second)
	case "dev":
		if len(pos) != 2 {
			return errors.New("usage: pantheon dev <app> <action> [key=value ...]")
		}
		return send("dev", map[string]any{"app": arg(0), "action": strings.ReplaceAll(arg(1), "-", "_"), "args": r.p.args}, 300*time.Second)
	case "desktop", "browser":
		if len(pos) != 1 {
			return fmt.Errorf("usage: pantheon %s <action> [key=value ...]", group)
		}
		method := group + "_" + strings.ReplaceAll(arg(0), "-", "_")
		def, ok := windowTimeouts[method]
		if !ok {
			def = 120 * time.Second
		}
		return send(group, map[string]any{"action": strings.ReplaceAll(arg(0), "-", "_"), "args": r.p.args}, def)
	case "nodes":
		if arg(0) == "info" {
			return send("nodes.info", map[string]any{"node": arg(1)}, 60*time.Second)
		}
		return send("nodes.list", nil, 60*time.Second)
	case "run":
		if r.p.flags["node"] == "" || len(r.p.rest) == 0 {
			return errors.New("usage: pantheon run --node N [--kind shell|python] -- <command>")
		}
		kind := r.p.flags["kind"]
		if kind == "" {
			kind = "shell"
		}
		timeout, err := r.p.timeout(600 * time.Second)
		if err != nil {
			return err
		}
		return send("run", map[string]any{"node_id": r.p.flags["node"], "code": strings.Join(r.p.rest, " "),
			"kind": kind, "timeout": int(timeout.Seconds())}, timeout)
	case "transfer":
		if len(pos) != 2 {
			return errors.New("usage: pantheon transfer <src-node>:<path> <dst-node>:<path>")
		}
		src, srcPath, _ := strings.Cut(pos[0], ":")
		dst, dstPath, _ := strings.Cut(pos[1], ":")
		return send("transfer", map[string]any{"src_node": src, "src_path": srcPath, "dst_node": dst,
			"dst_path": dstPath, "wait": true}, 3600*time.Second)
	case "task":
		return r.task(send)
	case "agent":
		return r.subAgent(link)
	case "skills":
		return r.skills(send)
	case "jobs":
		action, id := arg(0), arg(1)
		if action == "" {
			action = "list"
		}
		params := map[string]any{"action": action, "task_id": id}
		def := 60 * time.Second
		if action == "wait" {
			// A command running past a minute moves to the background itself:
			// wait less than that, and ask again.
			params["timeout"] = 50
			if t, ok := r.p.flags["timeout"]; ok {
				if n, err := strconv.Atoi(t); err == nil && n > 0 {
					params["timeout"] = min(n, 300)
					def = time.Duration(n+30) * time.Second
				}
			}
		}
		return send("jobs", params, def)
	}
	return fmt.Errorf("unknown command %q", group)
}

// subAgent runs a sub-agent: started by the agent that ran the command, its
// steps shown under the command, its final answer the command's output.
func (r *runner) subAgent(link *agentLink) error {
	pos := r.p.positional
	if len(pos) == 0 {
		return errors.New("usage: pantheon agent list | run <profile> <task> [--detach] | wait <run_id> | stop <run_id>")
	}
	call := func(op string, params map[string]any, timeout time.Duration) (map[string]any, error) {
		raw, err := link.do(r.ctx, op, params, timeout)
		if err != nil {
			return nil, err
		}
		var out map[string]any
		if err := json.Unmarshal(raw, &out); err != nil {
			return nil, errors.New("the Agent returned an invalid reply")
		}
		return out, nil
	}
	// wait asks until the sub-agent ends (each request holds a few minutes).
	wait := func(id string) error {
		for {
			out, err := call("agent.wait", map[string]any{"run_id": id, "timeout": 240}, 270*time.Second)
			if err != nil {
				return err
			}
			if done, _ := out["done"].(bool); !done {
				continue
			}
			if msg, ok := out["error"].(string); ok && msg != "" {
				return errors.New(msg)
			}
			if r.json {
				return r.emit(out)
			}
			fmt.Fprintln(r.stdout, out["answer"])
			return nil
		}
	}
	switch pos[0] {
	case "list":
		out, err := call("agent.list", nil, 60*time.Second)
		if err != nil {
			return err
		}
		if r.json {
			return r.emit(out)
		}
		list, _ := out["profiles"].([]any)
		if len(list) == 0 {
			fmt.Fprintln(r.stdout, "No sub-agent profiles.")
		}
		for _, item := range list {
			p, _ := item.(map[string]any)
			fmt.Fprintf(r.stdout, "%s\t%v\n", p["name"], p["description"])
		}
		return nil
	case "run":
		task := strings.Join(pos[min(2, len(pos)):], " ")
		if t, ok := r.p.args["task"].(string); ok && task == "" {
			task = t
		}
		if len(pos) < 2 || strings.TrimSpace(task) == "" {
			return errors.New("usage: pantheon agent run <profile> <task> [--detach]")
		}
		out, err := call("agent.start", map[string]any{"profile": pos[1], "task": task, "detach": r.p.bools["detach"]}, 60*time.Second)
		if err != nil {
			return err
		}
		id, _ := out["run_id"].(string)
		if r.p.bools["detach"] {
			if r.json {
				return r.emit(out)
			}
			fmt.Fprintf(r.stdout, "Started %s as %s. pantheon agent wait %s returns its answer.\n", pos[1], id, id)
			return nil
		}
		return wait(id)
	case "wait":
		if len(pos) != 2 {
			return errors.New("usage: pantheon agent wait <run_id>")
		}
		return wait(pos[1])
	case "stop":
		if len(pos) != 2 {
			return errors.New("usage: pantheon agent stop <run_id>")
		}
		out, err := call("agent.stop", map[string]any{"run_id": pos[1]}, 60*time.Second)
		if err != nil {
			return err
		}
		return r.emit(out)
	}
	return fmt.Errorf("unknown agent action %q", pos[0])
}

// skills reads and manages the agent's skills, and the Store's.
func (r *runner) skills(send func(string, map[string]any, time.Duration) error) error {
	pos := r.p.positional
	if len(pos) == 0 {
		return errors.New("usage: pantheon skills view <name> [<file>] | list | search <query> | ... (pantheon --help)")
	}
	arg := func(i int) string {
		if i < len(pos) {
			return pos[i]
		}
		return ""
	}
	if pos[0] == "view" {
		if arg(1) == "" {
			return errors.New("usage: pantheon skills view <name> [<file>]")
		}
		return send("skills.view", map[string]any{"name": arg(1), "file": arg(2)}, 60*time.Second)
	}
	actions := map[string]string{"list": "list", "create": "create", "update": "update", "patch": "patch",
		"delete": "delete", "search": "search_store", "browse": "browse_store", "adopt": "adopt",
		"rate": "rate", "publish": "publish"}
	action, ok := actions[pos[0]]
	if !ok {
		return fmt.Errorf("unknown skills action %q (pantheon --help)", pos[0])
	}
	params := map[string]any{"action": action}
	options := map[string]any{}
	switch action {
	case "search_store", "browse_store":
		if q := strings.Join(pos[1:], " "); q != "" {
			options["query"] = q
		}
	case "list":
	default:
		params["name"] = arg(1)
	}
	for k, v := range r.p.args {
		switch k {
		case "name", "content", "old_string", "new_string", "replace_all":
			params[k] = v
		default:
			options[k] = v
		}
	}
	if len(options) > 0 {
		params["options"] = options
	}
	return send("skills.manage", params, 120*time.Second)
}

// task is the conversation's task tracking: what the user sees of the work.
func (r *runner) task(send func(string, map[string]any, time.Duration) error) error {
	pos := r.p.positional
	if len(pos) == 0 {
		return errors.New("usage: pantheon task start|update|output|outputs|notify (pantheon --help)")
	}
	args := r.p.args
	switch pos[0] {
	case "start", "update":
		params := map[string]any{"task_name": "%SAME%", "mode": "%SAME%", "task_summary": "%SAME%",
			"task_status": "%SAME%", "predicted_task_size": 5}
		if len(pos) > 1 {
			params["task_name"] = strings.Join(pos[1:], " ")
		}
		for k, v := range args {
			params[k] = v
		}
		return send("task.boundary", params, 60*time.Second)
	case "output":
		if len(pos) != 2 {
			return errors.New("usage: pantheon task output <path> [title=… description=… kind=…]")
		}
		params := map[string]any{"path": pos[1]}
		for k, v := range args {
			params[k] = v
		}
		return send("task.output", params, 60*time.Second)
	case "outputs":
		return send("task.outputs", nil, 60*time.Second)
	case "notify":
		if _, given := args["message"]; len(pos) < 2 && !given {
			return errors.New("usage: pantheon task notify <message> [--wait] [paths_to_review=[...]]")
		}
		params := map[string]any{"message": strings.Join(pos[1:], " "), "blocked_on_user": r.p.bools["wait"],
			"paths_to_review": []any{}, "confidence_score": 0.8, "confidence_justification": "", "questions": []any{}}
		for k, v := range args {
			params[k] = v
		}
		return send("task.notify", params, 120*time.Second)
	}
	return fmt.Errorf("unknown task action %q", pos[0])
}

// manageRequest is an App-management command as the manage operation and its
// arguments, shared by both modes.
func manageRequest(action string, rest []string, extra map[string]any) (string, string, map[string]any, error) {
	manage := map[string]string{"search": "search", "inspect": "inspect", "install": "install",
		"uninstall": "uninstall", "trash": "trash", "restore": "restore", "versions": "versions",
		"set-version": "set_version", "set-node": "set_node", "nodes": "nodes", "policy": "set_policy"}
	op, ok := manage[action]
	if !ok {
		return "", "", nil, fmt.Errorf("unknown action %q (pantheon --help)", action)
	}
	arg := func(i int) string {
		if i < len(rest) {
			return rest[i]
		}
		return ""
	}
	args := map[string]any{}
	for k, v := range extra {
		args[k] = v
	}
	app := ""
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
		app, args["version"] = arg(0), arg(1)
	case "set_node":
		app, args["node"] = arg(0), arg(1)
	case "set_policy":
		app, args["policy"] = arg(0), arg(1)
	case "trash":
	default:
		app = arg(0)
	}
	return op, app, args, nil
}
