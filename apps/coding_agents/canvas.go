package codingagentsapp

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// A turn composed on the Agent Canvas: Claude Code with an effort level,
// sub-agents it may delegate to, folders to focus on, web research on or
// off, and reference images. Other agents ignore these.

// CanvasTurn is what a canvas send asks for; a conversation keeps the
// latest so a reload can show it.
type CanvasTurn struct {
	Effort    string     `json:"effort,omitempty"`
	Subagents []Subagent `json:"subagents,omitempty"`
	Focus     []string   `json:"focus,omitempty"`
	Research  bool       `json:"research"`
	Images    []string   `json:"images,omitempty"`
}

// Subagent is one role Claude Code may hand work to.
type Subagent struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Prompt      string `json:"prompt"`
	Write       bool   `json:"write"`
	Model       string `json:"model,omitempty"`
}

const maxSubagents = 12

var (
	efforts      = map[string]bool{"low": true, "medium": true, "high": true, "xhigh": true, "max": true}
	subagentName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`)
	readTools    = []string{"Read", "Grep", "Glob"}
	webTools     = []string{"WebSearch", "WebFetch"}
)

// canvasLaunch is a canvas turn turned into Claude Code's terms.
type canvasLaunch struct {
	effort     string
	subagents  string // --agents JSON
	addDirs    []string
	disallowed []string
	system     string // appended to the system prompt
	images     []string
	focus      []string // absolute
}

// prepare checks a canvas turn and builds its launch for a conversation
// working in cwd.
func (ct *CanvasTurn) prepare(cwd string) (canvasLaunch, error) {
	var l canvasLaunch
	if ct.Effort != "" && !efforts[ct.Effort] {
		return l, fmt.Errorf("effort must be low, medium, high, xhigh or max, not %q", ct.Effort)
	}
	l.effort = ct.Effort
	if len(ct.Subagents) > maxSubagents {
		return l, fmt.Errorf("at most %d sub-agents", maxSubagents)
	}
	defs := map[string]map[string]any{}
	var roster []string
	for _, s := range ct.Subagents {
		if !subagentName.MatchString(s.Name) {
			return l, fmt.Errorf("sub-agent name %q: use letters, digits, - and _", s.Name)
		}
		if defs[s.Name] != nil {
			return l, fmt.Errorf("two sub-agents are named %s", s.Name)
		}
		desc := firstNonEmpty(strings.TrimSpace(s.Description), s.Name)
		def := map[string]any{"description": desc, "prompt": firstNonEmpty(strings.TrimSpace(s.Prompt), desc)}
		if !s.Write {
			tools := append([]string{}, readTools...)
			if ct.Research {
				tools = append(tools, webTools...)
			}
			def["tools"] = tools
		}
		if s.Model != "" {
			def["model"] = s.Model
		}
		defs[s.Name] = def
		access := "read-only"
		if s.Write {
			access = "can edit files"
		}
		roster = append(roster, fmt.Sprintf("- %s (%s): %s", s.Name, access, firstLine(desc)))
	}
	if len(defs) > 0 {
		b, err := json.Marshal(defs)
		if err != nil {
			return l, err
		}
		l.subagents = string(b)
	}
	for _, f := range ct.Focus {
		f = filepath.Clean(expandHome(f))
		if !filepath.IsAbs(f) {
			f = filepath.Join(cwd, f)
		}
		if st, err := os.Stat(f); err != nil || !st.IsDir() {
			return l, fmt.Errorf("%s is not a folder on this node", f)
		}
		l.focus = append(l.focus, f)
		if !within(cwd, f) {
			l.addDirs = append(l.addDirs, f)
		}
	}
	for _, img := range ct.Images {
		if !filepath.IsAbs(img) {
			return l, fmt.Errorf("reference image %s: give an absolute path", img)
		}
		if st, err := os.Stat(img); err != nil || st.IsDir() {
			return l, fmt.Errorf("reference image %s is not on this node", img)
		}
		l.images = append(l.images, filepath.Clean(img))
	}
	if !ct.Research {
		l.disallowed = webTools
	}

	var sys []string
	if len(l.focus) > 0 {
		sys = append(sys, "Focus on these folders:\n- "+strings.Join(l.focus, "\n- "))
	}
	if len(roster) > 0 {
		sys = append(sys, "You may delegate to these sub-agents with the Agent tool:\n"+strings.Join(roster, "\n")+
			"\nLaunch independent ones in parallel (several Agent calls in one message), and when they finish, "+
			"report each one's outcome.")
	}
	// A resumed session remembers earlier turns' settings, so each turn says
	// where it stands, including what was switched back on or off.
	if len(roster) == 0 {
		sys = append(sys, "No sub-agents this turn: do the work yourself.")
	}
	if ct.Research {
		sys = append(sys, "Web research is on for this turn: you may search and fetch from the web.")
	} else {
		sys = append(sys, "Web research is off for this turn: do not search or fetch from the web.")
	}
	sys = append(sys, "These settings are for this turn and replace any from earlier turns.")
	l.system = strings.Join(sys, "\n\n")
	return l, nil
}

// prompt adds the reference images to what the user wrote.
func (l canvasLaunch) prompt(text string) string {
	if len(l.images) == 0 {
		return text
	}
	return text + "\n\nReference images (read each with the Read tool):\n- " + strings.Join(l.images, "\n- ")
}

func (l canvasLaunch) apply(t *codingagents.Turn) {
	t.Effort, t.Subagents, t.AddDirs, t.Disallowed = l.effort, l.subagents, l.addDirs, l.disallowed
}

// within reports whether path is dir or inside it.
func within(dir, path string) bool {
	rel, err := filepath.Rel(filepath.Clean(dir), filepath.Clean(path))
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}
