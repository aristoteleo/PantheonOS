package codingagentsapp

import (
	"bufio"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// What the Agent Canvas shows around a turn: the Claude Code skills it can
// call, the repository it works in, and the folders it may focus on.

// Skill is one Claude Code skill on this node.
type Skill struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Source      string `json:"source"` // user|project|plugin
	Path        string `json:"path"`
	// Plugin is the plugin a plugin skill comes with; its Name is
	// "<plugin>:<skill>", as typed after the slash.
	Plugin string `json:"plugin,omitempty"`
}

// skills lists the user's skills, the project's (cwd's) and those of the
// plugins enabled there, each from its front matter, the file or folder
// name standing in for a missing name.
func skills(cwd string) map[string]any {
	return map[string]any{"success": true, "skills": skillList(cwd)}
}

func skillList(cwd string) []Skill {
	out := skillsIn(filepath.Join(codingagents.ClaudeDir(), "skills"), "user", "")
	if cwd != "" {
		cwd = filepath.Clean(expandHome(cwd))
		out = append(out, skillsIn(filepath.Join(cwd, ".claude", "skills"), "project", "")...)
	}
	return append(out, pluginSkills(cwd)...)
}

func skillsIn(dir, source, plugin string) []Skill {
	entries, _ := os.ReadDir(dir)
	out := []Skill{}
	for _, e := range entries {
		file := filepath.Join(dir, e.Name(), "SKILL.md")
		meta, ok := frontMatter(file)
		if !ok {
			continue
		}
		s := Skill{Name: firstNonEmpty(meta["name"], e.Name()), Description: meta["description"], Source: source, Path: file}
		if plugin != "" {
			s.Name, s.Plugin = plugin+":"+s.Name, plugin
		}
		out = append(out, s)
	}
	sortSkills(out)
	return out
}

func sortSkills(list []Skill) {
	sort.Slice(list, func(i, j int) bool { return strings.ToLower(list[i].Name) < strings.ToLower(list[j].Name) })
}

// pluginSkills reads Claude Code's own plugin records: the plugins
// installed (plugins/installed_plugins.json, for the user or for the
// project cwd) and switched on in enabledPlugins of the user's or the
// project's settings, and in each, skills/<name>/SKILL.md and the slash
// commands under commands/.
func pluginSkills(cwd string) []Skill {
	dir := codingagents.ClaudeDir()
	var installed struct {
		Plugins map[string][]struct {
			Scope       string `json:"scope"`
			ProjectPath string `json:"projectPath"`
			InstallPath string `json:"installPath"`
		} `json:"plugins"`
	}
	if b, err := os.ReadFile(filepath.Join(dir, "plugins", "installed_plugins.json")); err != nil || json.Unmarshal(b, &installed) != nil {
		return nil
	}
	enabled := map[string]bool{}
	settings := []string{filepath.Join(dir, "settings.json")}
	if cwd != "" {
		settings = append(settings, filepath.Join(cwd, ".claude", "settings.json"), filepath.Join(cwd, ".claude", "settings.local.json"))
	}
	for _, f := range settings {
		var s struct {
			EnabledPlugins map[string]bool `json:"enabledPlugins"`
		}
		if b, err := os.ReadFile(f); err == nil && json.Unmarshal(b, &s) == nil {
			for k, v := range s.EnabledPlugins {
				enabled[k] = v
			}
		}
	}
	out := []Skill{}
	for key, installs := range installed.Plugins {
		if !enabled[key] {
			continue
		}
		path := ""
		for _, in := range installs {
			if in.Scope == "user" && path == "" || in.Scope != "user" && cwd != "" && filepath.Clean(in.ProjectPath) == cwd {
				path = in.InstallPath
			}
		}
		if path == "" {
			continue
		}
		var manifest struct {
			Name string `json:"name"`
		}
		if b, err := os.ReadFile(filepath.Join(path, ".claude-plugin", "plugin.json")); err == nil {
			_ = json.Unmarshal(b, &manifest)
		}
		plugin := firstNonEmpty(manifest.Name, strings.SplitN(key, "@", 2)[0])
		out = append(out, skillsIn(filepath.Join(path, "skills"), "plugin", plugin)...)
		commands := filepath.Join(path, "commands")
		_ = filepath.WalkDir(commands, func(p string, d fs.DirEntry, err error) error {
			if err != nil || d.IsDir() || !strings.HasSuffix(d.Name(), ".md") {
				return nil
			}
			meta, ok := frontMatter(p)
			if !ok {
				return nil
			}
			rel, _ := filepath.Rel(commands, strings.TrimSuffix(p, ".md"))
			out = append(out, Skill{Name: plugin + ":" + strings.ReplaceAll(filepath.ToSlash(rel), "/", ":"),
				Description: meta["description"], Source: "plugin", Path: p, Plugin: plugin})
			return nil
		})
	}
	sortSkills(out)
	return out
}

// slashSkills are the known skills a prompt starts with (/name …), which
// Claude Code expands itself without a tool call.
func slashSkills(prompt, cwd string) []string {
	var lead []string
	for _, tok := range strings.Fields(prompt) {
		if len(tok) < 2 || tok[0] != '/' {
			break
		}
		lead = append(lead, tok[1:])
	}
	if len(lead) == 0 {
		return nil
	}
	known := map[string]bool{}
	for _, s := range skillList(cwd) {
		known[s.Name] = true
	}
	var out []string
	for _, name := range lead {
		if known[name] && !slices.Contains(out, name) {
			out = append(out, name)
		}
	}
	return out
}

// frontMatter reads the YAML header of a Markdown file: plain, quoted and
// folded (> or |) scalar values at the top level. ok is false when the file
// cannot be read.
func frontMatter(path string) (map[string]string, bool) {
	f, err := os.Open(path)
	if err != nil {
		return nil, false
	}
	defer f.Close()
	meta := map[string]string{}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 1<<20)
	if !sc.Scan() || strings.TrimSpace(sc.Text()) != "---" {
		return meta, true
	}
	var key, style string
	var block []string
	flush := func() {
		if key != "" && style != "" {
			sep := " "
			if style[0] == '|' {
				sep = "\n"
			}
			meta[key] = strings.TrimSpace(strings.Join(block, sep))
		}
		key, style, block = "", "", nil
	}
	for sc.Scan() {
		line := sc.Text()
		if strings.TrimSpace(line) == "---" {
			break
		}
		if style != "" && (line == "" || line[0] == ' ' || line[0] == '\t') {
			block = append(block, strings.TrimSpace(line))
			continue
		}
		flush()
		if line == "" || line[0] == ' ' || line[0] == '\t' || line[0] == '#' {
			continue
		}
		k, v, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		k, v = strings.TrimSpace(k), strings.TrimSpace(v)
		if v != "" && (v[0] == '>' || v[0] == '|') {
			key, style = k, v
			continue
		}
		meta[k] = unquote(v)
	}
	flush()
	return meta, true
}

func unquote(v string) string {
	if len(v) >= 2 && (v[0] == '"' && v[len(v)-1] == '"' || v[0] == '\'' && v[len(v)-1] == '\'') {
		inner := v[1 : len(v)-1]
		if v[0] == '\'' {
			return strings.ReplaceAll(inner, "''", "'")
		}
		return strings.NewReplacer(`\"`, `"`, `\\`, `\`, `\n`, "\n").Replace(inner)
	}
	return v
}

// Commit is a repository's latest commit.
type Commit struct {
	Hash    string `json:"hash"`
	Short   string `json:"short"`
	Subject string `json:"subject"`
}

// repoInfo is a folder's git state: the commit and branch checked out, and
// the local branches.
func repoInfo(path string) map[string]any {
	path = filepath.Clean(expandHome(path))
	if st, err := os.Stat(path); err != nil || !st.IsDir() {
		return failure("%s is not a folder on this node", path)
	}
	branch, isGit := gitBranch(path)
	if !isGit {
		return map[string]any{"success": true, "git": false}
	}
	out := map[string]any{"success": true, "git": true, "branch": branch, "branches": []string{}}
	if log, err := git(path, 3*time.Second, "log", "-1", "--format=%H%x00%h%x00%s"); err == nil {
		if f := strings.SplitN(strings.TrimRight(log, "\n"), "\x00", 3); len(f) == 3 {
			out["commit"] = Commit{Hash: f[0], Short: f[1], Subject: f[2]}
		}
	}
	if refs, err := git(path, 3*time.Second, "for-each-ref", "--format=%(refname:short)", "refs/heads"); err == nil {
		branches := []string{}
		for _, b := range strings.Split(refs, "\n") {
			if b = strings.TrimSpace(b); b != "" {
				branches = append(branches, b)
			}
		}
		out["branches"] = branches
	}
	return out
}

// FolderCount is one subfolder with the files under it.
type FolderCount struct {
	Name  string `json:"name"`
	Path  string `json:"path"`
	Files int    `json:"files"`
	// Subfolders is how many folders it has itself, for expanding it.
	Subfolders int `json:"subfolders"`
	// Approx: counting stopped at the limit; Files is a lower bound.
	Approx bool `json:"approx,omitempty"`
}

const (
	treeFileLimit = 100_000
	treeTimeLimit = 2 * time.Second
)

// skipDir is a folder the tree leaves out: git's, dependencies, hidden ones.
func skipDir(name string) bool { return name == "node_modules" || strings.HasPrefix(name, ".") }

// folderTree lists dir's immediate subfolders (dir inside the repository at
// path, relative or absolute) with their files counted recursively, up to
// a shared limit of files and time.
func folderTree(path, dir string) map[string]any {
	root := filepath.Clean(expandHome(path))
	if st, err := os.Stat(root); err != nil || !st.IsDir() {
		return failure("%s is not a folder on this node", root)
	}
	if dir == "" {
		dir = root
	} else if !filepath.IsAbs(dir) {
		dir = filepath.Join(root, dir)
	}
	dir = filepath.Clean(dir)
	// Links count where they lead: a folder linked from inside the
	// project to elsewhere is outside it.
	realRoot, err1 := filepath.EvalSymlinks(root)
	realDir, err2 := filepath.EvalSymlinks(dir)
	if err2 != nil && errors.Is(err2, os.ErrNotExist) {
		return failure("%s is not a folder on this node", dir)
	}
	if err1 != nil || err2 != nil || !within(root, dir) || !within(realRoot, realDir) {
		return failure("%s is outside %s", dir, root)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return failure("%s cannot be opened: %v", dir, err)
	}
	deadline := time.Now().Add(treeTimeLimit)
	counted, over := 0, false
	folders := []FolderCount{}
	for _, e := range entries {
		if !e.IsDir() || skipDir(e.Name()) {
			continue
		}
		fc := FolderCount{Name: e.Name(), Path: filepath.Join(dir, e.Name())}
		_ = filepath.WalkDir(fc.Path, func(p string, d fs.DirEntry, err error) error {
			if over {
				return fs.SkipAll
			}
			if err != nil {
				return nil
			}
			if d.IsDir() {
				if p == fc.Path {
					return nil
				}
				if skipDir(d.Name()) {
					return fs.SkipDir
				}
				if filepath.Dir(p) == fc.Path {
					fc.Subfolders++
				}
				return nil
			}
			if d.Type().IsRegular() {
				fc.Files++
				if counted++; counted >= treeFileLimit || counted%1024 == 0 && time.Now().After(deadline) {
					over = true
				}
			}
			return nil
		})
		fc.Approx = over
		folders = append(folders, fc)
	}
	sort.Slice(folders, func(i, j int) bool { return strings.ToLower(folders[i].Name) < strings.ToLower(folders[j].Name) })
	return map[string]any{"success": true, "dir": dir, "folders": folders, "approx": over}
}
