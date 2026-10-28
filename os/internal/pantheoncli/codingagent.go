package pantheoncli

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"text/tabwriter"
	"time"
)

const codingAgentUsage = `usage: pantheon coding-agent list [--node N]
       pantheon coding-agent run --node N --agent ID [--cwd DIR] [--model M] [--manual] [--detach] -- PROMPT
       pantheon coding-agent run --node N --conversation ID [--detach] -- PROMPT
       pantheon coding-agent show ID --node N [--wait] | stop ID --node N | conversations --node N`

// codingAgentCaller reaches a node's Coding Agents service: through Fleet's
// node_call from a terminal, or through the agent that runs the command.
type codingAgentCaller struct {
	call  func(node, method string, args map[string]any, timeout time.Duration) (json.RawMessage, error)
	nodes func() ([]map[string]any, error)
}

func (r *runner) directCodingAgents() codingAgentCaller {
	return codingAgentCaller{
		call: func(node, method string, args map[string]any, timeout time.Duration) (json.RawMessage, error) {
			return r.fleet("node_call", map[string]any{"node_id": node, "service": "coding_agent", "method": method,
				"args": args, "timeout": int(timeout.Seconds())}, timeout+15*time.Second)
		},
		nodes: func() ([]map[string]any, error) {
			result, err := r.fleet("fleet_list_nodes", nil, 60*time.Second)
			return listNodes(result, err)
		},
	}
}

func (r *runner) linkedCodingAgents(link *agentLink) codingAgentCaller {
	return codingAgentCaller{
		call: func(node, method string, args map[string]any, timeout time.Duration) (json.RawMessage, error) {
			return link.do(r.ctx, "coding_agent", map[string]any{"node_id": node, "method": method, "args": args,
				"timeout": int(timeout.Seconds())}, timeout+15*time.Second)
		},
		nodes: func() ([]map[string]any, error) {
			result, err := link.do(r.ctx, "nodes.list", nil, 60*time.Second)
			return listNodes(result, err)
		},
	}
}

func listNodes(result json.RawMessage, err error) ([]map[string]any, error) {
	if err != nil {
		return nil, err
	}
	if err := Succeeded(result); err != nil {
		return nil, err
	}
	var reply struct {
		Nodes []map[string]any `json:"nodes"`
	}
	if err := json.Unmarshal(result, &reply); err != nil {
		return nil, err
	}
	sort.Slice(reply.Nodes, func(i, j int) bool { return fmt.Sprint(reply.Nodes[i]["name"]) < fmt.Sprint(reply.Nodes[j]["name"]) })
	return reply.Nodes, nil
}

func (c codingAgentCaller) nodeID(node string) (string, error) {
	if node == "" {
		return "", errors.New("--node is required (pantheon coding-agent list shows where agents are installed)")
	}
	if strings.HasPrefix(node, "n_") {
		return node, nil
	}
	nodes, err := c.nodes()
	if err != nil {
		return "", err
	}
	for _, n := range nodes {
		name := fmt.Sprint(n["name"])
		if name == node || strings.TrimSuffix(name, ".local") == node || n["node_id"] == node {
			return fmt.Sprint(n["node_id"]), nil
		}
	}
	return "", fmt.Errorf("no node %q in your Fleet (pantheon nodes list)", node)
}

func (c codingAgentCaller) result(node, method string, args map[string]any, timeout time.Duration) (map[string]any, error) {
	raw, err := c.call(node, method, args, timeout)
	if err != nil {
		return nil, err
	}
	if err := Succeeded(raw); err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (r *runner) codingAgent(c codingAgentCaller) error {
	pos := r.p.positional
	if len(pos) == 0 {
		return errors.New(codingAgentUsage)
	}
	switch pos[0] {
	case "list":
		if r.p.flags["node"] == "" {
			return r.codingAgentNodes(c)
		}
		node, err := c.nodeID(r.p.flags["node"])
		if err != nil {
			return err
		}
		out, err := c.result(node, "agents_detect", map[string]any{"refresh": true}, 60*time.Second)
		if err != nil {
			return err
		}
		if r.json {
			return r.emit(out)
		}
		agents, _ := out["agents"].([]any)
		if len(agents) == 0 {
			fmt.Fprintln(r.stdout, "No coding agents found on this node.")
			return nil
		}
		w := tabwriter.NewWriter(r.stdout, 0, 2, 2, ' ', 0)
		fmt.Fprintln(w, "AGENT\tNAME\tCONVERSATION\tPATH")
		for _, item := range agents {
			a, _ := item.(map[string]any)
			conv := "terminal only"
			if a["conversation"] == true {
				conv = "yes"
			}
			fmt.Fprintf(w, "%v\t%v\t%s\t%v\n", a["id"], a["label"], conv, a["path"])
		}
		return w.Flush()
	case "conversations":
		node, err := c.nodeID(r.p.flags["node"])
		if err != nil {
			return err
		}
		out, err := c.result(node, "agent_conversations", map[string]any{"agent": r.p.flags["agent"]}, 60*time.Second)
		if err != nil {
			return err
		}
		if r.json {
			return r.emit(out)
		}
		w := tabwriter.NewWriter(r.stdout, 0, 2, 2, ' ', 0)
		fmt.Fprintln(w, "ID\tAGENT\tSTATUS\tUPDATED\tTITLE")
		list, _ := out["conversations"].([]any)
		for _, item := range list {
			cv, _ := item.(map[string]any)
			updated, _ := cv["updated"].(float64)
			fmt.Fprintf(w, "%v\t%v\t%v\t%s\t%v\n", cv["id"], cv["agent"], cv["status"],
				time.Unix(int64(updated), 0).Format("2006-01-02 15:04"), cv["title"])
		}
		return w.Flush()
	case "run":
		return r.codingAgentRun(c)
	case "show":
		if len(pos) != 2 {
			return errors.New(codingAgentUsage)
		}
		node, err := c.nodeID(r.p.flags["node"])
		if err != nil {
			return err
		}
		_, err = r.followConversation(c, node, pos[1], 0, r.p.bools["wait"])
		return err
	case "stop":
		if len(pos) != 2 {
			return errors.New(codingAgentUsage)
		}
		node, err := c.nodeID(r.p.flags["node"])
		if err != nil {
			return err
		}
		out, err := c.result(node, "agent_stop", map[string]any{"conversation_id": pos[1]}, 30*time.Second)
		if err != nil {
			return err
		}
		return r.emit(out)
	}
	return fmt.Errorf("unknown action %q\n%s", pos[0], codingAgentUsage)
}

// codingAgentNodes lists the coding agents found on every node.
func (r *runner) codingAgentNodes(c codingAgentCaller) error {
	nodes, err := c.nodes()
	if err != nil {
		return err
	}
	if r.json {
		var rows []map[string]any
		for _, n := range nodes {
			rows = append(rows, map[string]any{"node_id": n["node_id"], "name": n["name"], "status": n["status"],
				"coding_agents": n["coding_agents"]})
		}
		return r.emit(map[string]any{"success": true, "nodes": rows})
	}
	w := tabwriter.NewWriter(r.stdout, 0, 2, 2, ' ', 0)
	fmt.Fprintln(w, "NAME\tNODE\tSTATUS\tCODING AGENTS")
	for _, n := range nodes {
		var ids []string
		list, _ := n["coding_agents"].([]any)
		for _, id := range list {
			ids = append(ids, fmt.Sprint(id))
		}
		fmt.Fprintf(w, "%v\t%v\t%v\t%s\n", n["name"], n["node_id"], n["status"], dash(strings.Join(ids, ", ")))
	}
	return w.Flush()
}

func (r *runner) codingAgentRun(c codingAgentCaller) error {
	prompt := strings.TrimSpace(strings.Join(append(r.p.positional[1:], r.p.rest...), " "))
	if prompt == "" {
		return errors.New(codingAgentUsage)
	}
	conversation := r.p.flags["conversation"]
	if conversation == "" && r.p.flags["agent"] == "" {
		return errors.New("--agent is required for a new conversation (pantheon coding-agent list --node N)")
	}
	node, err := c.nodeID(r.p.flags["node"])
	if err != nil {
		return err
	}
	origin := "cli"
	if agentFromEnv() != nil {
		origin = "agent"
	}
	out, err := c.result(node, "agent_send", map[string]any{"prompt": prompt, "agent": r.p.flags["agent"],
		"cwd": r.p.flags["cwd"], "conversation_id": conversation, "model": r.p.flags["model"],
		"auto": !r.p.bools["manual"], "origin": origin}, 60*time.Second)
	if err != nil {
		return err
	}
	id := fmt.Sprint(out["conversation_id"])
	after := int64(0)
	if seq, ok := out["from_seq"].(float64); ok {
		after = int64(seq) - 1
	}
	if r.p.bools["detach"] {
		if r.json {
			return r.emit(map[string]any{"success": true, "conversation_id": id, "node_id": node})
		}
		fmt.Fprintf(r.stdout, "Started conversation %s on %s.\nFollow it: pantheon coding-agent show %s --node %s\n", id, node, id, node)
		return nil
	}
	_, err = r.followConversation(c, node, id, after, true)
	return err
}

// followConversation prints a conversation's events from after on, and with
// wait keeps polling until the running turn ends. It returns the last
// message the agent wrote.
func (r *runner) followConversation(c codingAgentCaller, node, id string, after int64, wait bool) (string, error) {
	timeout, err := r.p.timeout(time.Hour)
	if err != nil {
		return "", err
	}
	deadline := time.Now().Add(timeout)
	var final, turnError string
	var events []map[string]any
	for {
		out, err := c.result(node, "agent_conversation", map[string]any{"conversation_id": id, "after": after}, 60*time.Second)
		if err != nil {
			return "", err
		}
		list, _ := out["events"].([]any)
		for _, item := range list {
			e, _ := item.(map[string]any)
			if seq, ok := e["seq"].(float64); ok && int64(seq) > after {
				after = int64(seq)
			}
			events = append(events, e)
			switch e["type"] {
			case "text":
				final = fmt.Sprint(e["text"])
				if !r.json {
					fmt.Fprintln(r.stdout, final)
				}
			case "user":
				if !r.json && !wait {
					fmt.Fprintf(r.stdout, "\n> %v\n\n", e["text"])
				}
			case "tool":
				if !r.json && e["status"] == "running" {
					fmt.Fprintf(r.stderr, "→ %v %v\n", e["name"], e["input"])
				}
			case "done":
				turnError, _ = e["error"].(string)
			}
		}
		meta, _ := out["conversation"].(map[string]any)
		more := out["more"] == true
		if !more && (!wait || meta["status"] != "running") {
			if r.json {
				return final, r.emit(map[string]any{"success": turnError == "", "conversation_id": id, "node_id": node,
					"status": meta["status"], "session_id": meta["session_id"], "final": final, "error": turnError,
					"events": events})
			}
			fmt.Fprintf(r.stderr, "\nconversation %s on %s (%v) — continue: pantheon coding-agent run --node %s --conversation %s -- …\n",
				id, node, meta["agent"], node, id)
			if turnError != "" {
				return final, fmt.Errorf("the turn ended with an error: %s", turnError)
			}
			return final, nil
		}
		if time.Now().After(deadline) {
			return final, fmt.Errorf("still running after %s; follow it with: pantheon coding-agent show %s --node %s", timeout, id, node)
		}
		if !more {
			time.Sleep(1500 * time.Millisecond)
		}
	}
}
