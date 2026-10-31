package codingagents

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
)

// Root is where a node keeps its coding-agent state: settings, the
// PantheonOS access key and conversations.
func Root() string {
	if dir := os.Getenv("PANTHEON_CODING_AGENTS_DIR"); dir != "" {
		return dir
	}
	home, err := os.UserHomeDir()
	if err != nil {
		home = os.TempDir()
	}
	return filepath.Join(home, ".pantheon", "coding-agents")
}

// AgentSettings is how the user tuned one agent on this node.
type AgentSettings struct {
	Hidden  bool              `json:"hidden,omitempty"`
	Command string            `json:"command,omitempty"` // replaces the detected binary
	Args    []string          `json:"args,omitempty"`    // added to every launch
	Env     map[string]string `json:"env,omitempty"`
	Model   string            `json:"model,omitempty"`
	// Auto is the default for full-auto mode; nil means on.
	Auto *bool `json:"auto,omitempty"`
}

// CustomAgent is an agent the catalog does not know, run in a terminal.
type CustomAgent struct {
	ID      string            `json:"id"`
	Label   string            `json:"label"`
	Command string            `json:"command"` // a shell command line
	Env     map[string]string `json:"env,omitempty"`
}

// Access lets agents on this node use PantheonOS through the pantheon
// command: a platform key made for this node, and the Hub it belongs to.
type Access struct {
	Key    string `json:"key,omitempty"`
	KeyID  int64  `json:"key_id,omitempty"`
	HubURL string `json:"hub_url,omitempty"`
}

// Settings is the node's coding-agent configuration.
type Settings struct {
	Agents map[string]AgentSettings `json:"agents,omitempty"`
	Custom []CustomAgent            `json:"custom,omitempty"`
	Access Access                   `json:"access,omitempty"`
	// Projects are the folders the user works in on this node; the desktop
	// lists the agents' sessions in them and leaves the rest out.
	Projects []Project `json:"projects,omitempty"`
}

// Project is a folder on the node the user added to the list.
type Project struct {
	Path string `json:"path"`
	// Workspaces are the git worktrees made for the project (or made
	// outside the app and adopted), each a checkout of its own branch
	// beside the project's folder. The folder itself is the project's
	// primary workspace.
	Workspaces []Workspace `json:"workspaces,omitempty"`
}

// Workspace is a git worktree of a project.
type Workspace struct {
	Name   string `json:"name"`
	Path   string `json:"path"`
	Branch string `json:"branch"`
	// Base is the branch or commit it was made from, what its branch is
	// compared with; empty for one made before workspaces kept it (the
	// repository's default branch stands in).
	Base    string  `json:"base,omitempty"`
	Created float64 `json:"created,omitempty"`
}

var settingsMu sync.Mutex

func settingsPath() string { return filepath.Join(Root(), "settings.json") }

// LoadSettings reads the node's settings; missing means defaults.
func LoadSettings() Settings {
	settingsMu.Lock()
	defer settingsMu.Unlock()
	return loadSettings()
}

func loadSettings() Settings {
	var s Settings
	if b, err := os.ReadFile(settingsPath()); err == nil {
		_ = json.Unmarshal(b, &s)
	}
	if s.Agents == nil {
		s.Agents = map[string]AgentSettings{}
	}
	return s
}

// UpdateSettings applies change to the stored settings and saves them. The
// file holds the access key, so only the user may read it.
func UpdateSettings(change func(*Settings) error) (Settings, error) {
	settingsMu.Lock()
	defer settingsMu.Unlock()
	s := loadSettings()
	if err := change(&s); err != nil {
		return s, err
	}
	b, err := json.MarshalIndent(s, "", " ")
	if err != nil {
		return s, err
	}
	if err := os.MkdirAll(Root(), 0o700); err != nil {
		return s, err
	}
	tmp := settingsPath() + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return s, err
	}
	return s, os.Rename(tmp, settingsPath())
}

var (
	envName  = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`)
	customID = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`)
)

// Validate refuses settings a launch could not use.
func (s Settings) Validate() error {
	for id, a := range s.Agents {
		if _, ok := Lookup(id); !ok && !s.hasCustom(id) {
			return fmt.Errorf("unknown agent %q", id)
		}
		for k := range a.Env {
			if !envName.MatchString(k) {
				return fmt.Errorf("%s: %q is not an environment variable name", id, k)
			}
		}
		if a.Command != "" && !filepath.IsAbs(a.Command) && strings.ContainsAny(a.Command, `/\ `) {
			return fmt.Errorf("%s: the command is an absolute path or a name on PATH", id)
		}
	}
	seen := map[string]bool{}
	for _, c := range s.Custom {
		if !customID.MatchString(c.ID) || seen[c.ID] {
			return fmt.Errorf("custom agent id %q must be unique, lower case letters, digits and -", c.ID)
		}
		if _, ok := Lookup(c.ID); ok {
			return fmt.Errorf("%q is already a known agent", c.ID)
		}
		if strings.TrimSpace(c.Label) == "" || strings.TrimSpace(c.Command) == "" {
			return errors.New("a custom agent needs a name and a command")
		}
		for k := range c.Env {
			if !envName.MatchString(k) {
				return fmt.Errorf("%s: %q is not an environment variable name", c.ID, k)
			}
		}
		seen[c.ID] = true
	}
	paths := map[string]bool{}
	for _, p := range s.Projects {
		if !filepath.IsAbs(p.Path) || filepath.Clean(p.Path) != p.Path {
			return fmt.Errorf("project %q: a project is an absolute, clean path", p.Path)
		}
		if paths[p.Path] {
			return fmt.Errorf("project %q is listed twice", p.Path)
		}
		paths[p.Path] = true
	}
	return nil
}

func (s Settings) hasCustom(id string) bool {
	for _, c := range s.Custom {
		if c.ID == id {
			return true
		}
	}
	return false
}

// Custom returns a custom agent by id.
func (s Settings) CustomAgent(id string) (CustomAgent, bool) {
	for _, c := range s.Custom {
		if c.ID == id {
			return c, true
		}
	}
	return CustomAgent{}, false
}

// AutoDefault is whether launches of id start in full-auto mode by default.
func (s Settings) AutoDefault(id string) bool {
	if a := s.Agents[id].Auto; a != nil {
		return *a
	}
	return true
}

// EnvList is an agent's own variables as KEY=VALUE, sorted.
func EnvList(env map[string]string) []string {
	out := make([]string, 0, len(env))
	for k, v := range env {
		out = append(out, k+"="+v)
	}
	sort.Strings(out)
	return out
}

// Redacted is the settings without the access key, for the desktop.
func (s Settings) Redacted() map[string]any {
	agents := s.Agents
	if agents == nil {
		agents = map[string]AgentSettings{}
	}
	custom := s.Custom
	if custom == nil {
		custom = []CustomAgent{}
	}
	projects := s.Projects
	if projects == nil {
		projects = []Project{}
	}
	return map[string]any{"agents": agents, "custom": custom, "projects": projects,
		"access": map[string]any{"enabled": s.Access.Key != "", "key_id": s.Access.KeyID, "hub_url": s.Access.HubURL}}
}
