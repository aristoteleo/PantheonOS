package codingagentsapp

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// A project's workspaces: its own folder, and git worktrees made for it,
// each a checkout of its own branch in a folder beside the project's
// (<parent>/<project>-worktrees/<name>), so agents in different workspaces
// do not change each other's files.

// WorkspaceRow is one workspace as the desktop lists it.
type WorkspaceRow struct {
	Project string `json:"project"`
	Path    string `json:"path"`
	Name    string `json:"name"`
	Branch  string `json:"branch,omitempty"`
	// Main: the project's own folder.
	Main bool `json:"main,omitempty"`
	// Git: the project is a git repository, so workspaces can be made in it.
	Git     bool    `json:"git,omitempty"`
	Created float64 `json:"created,omitempty"`
	// Missing: a worktree whose folder is gone.
	Missing bool `json:"missing,omitempty"`
}

var branchName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]*$`)

// workspaceList is every project's workspaces, each project's own folder first.
func workspaceList(projects []codingagents.Project) []WorkspaceRow {
	out := []WorkspaceRow{}
	for _, p := range projects {
		branch, isGit := gitBranch(p.Path)
		out = append(out, WorkspaceRow{Project: p.Path, Path: p.Path, Name: filepath.Base(p.Path), Branch: branch, Main: true, Git: isGit})
		for _, w := range p.Workspaces {
			row := WorkspaceRow{Project: p.Path, Path: w.Path, Name: w.Name, Branch: w.Branch, Git: true, Created: w.Created}
			if b, ok := gitBranch(w.Path); ok {
				row.Branch = b
			} else if _, err := os.Stat(w.Path); err != nil {
				row.Missing = true
			}
			out = append(out, row)
		}
	}
	return out
}

// gitBranch is the branch checked out in a folder, and whether it is in a
// git repository at all.
func gitBranch(dir string) (string, bool) {
	out, err := git(dir, 3*time.Second, "rev-parse", "--is-inside-work-tree")
	if err != nil || strings.TrimSpace(out) != "true" {
		return "", false
	}
	b, err := git(dir, 3*time.Second, "symbolic-ref", "--short", "-q", "HEAD")
	if err != nil {
		// A detached HEAD: its short commit.
		b, _ = git(dir, 3*time.Second, "rev-parse", "--short", "HEAD")
	}
	return strings.TrimSpace(b), true
}

func git(dir string, limit time.Duration, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", append([]string{"-C", dir}, args...)...)
	cmd.Env = codingagents.AgentEnv("")
	out, err := cmd.CombinedOutput()
	if err != nil {
		msg := strings.TrimSpace(string(out))
		if msg == "" {
			msg = err.Error()
		}
		return string(out), errors.New(strings.TrimPrefix(msg, "fatal: "))
	}
	return string(out), nil
}

func findProject(s codingagents.Settings, project string) int {
	project = filepath.Clean(expandHome(project))
	for i, p := range s.Projects {
		if p.Path == project {
			return i
		}
	}
	return -1
}

// createWorkspace makes a git worktree of a project on a new branch (or an
// existing one of that name) and lists it with the project.
func (a *App) createWorkspace(project, name string) map[string]any {
	name = strings.TrimSpace(name)
	if !branchName.MatchString(name) || strings.Contains(name, "..") || strings.HasSuffix(name, "/") || strings.HasSuffix(name, ".lock") {
		return failure("A workspace name is a branch name: letters, digits, - _ . /")
	}
	s := codingagents.LoadSettings()
	i := findProject(s, project)
	if i < 0 {
		return failure("%s is not one of your projects", project)
	}
	root := s.Projects[i].Path
	if _, ok := gitBranch(root); !ok {
		return failure("%s is not a git repository; workspaces are git worktrees", filepath.Base(root))
	}
	if _, err := git(root, 3*time.Second, "rev-parse", "--verify", "-q", "HEAD"); err != nil {
		return failure("The repository has no commit yet; commit once, then add a workspace")
	}
	dir := filepath.Join(filepath.Dir(root), filepath.Base(root)+"-worktrees", strings.ReplaceAll(name, "/", "-"))
	if _, err := os.Stat(dir); err == nil {
		return failure("%s already exists", dir)
	}
	if err := os.MkdirAll(filepath.Dir(dir), 0o755); err != nil {
		return failure("%v", err)
	}
	args := []string{"worktree", "add", "-b", name, dir}
	if _, err := git(root, 3*time.Second, "show-ref", "--verify", "-q", "refs/heads/"+name); err == nil {
		args = []string{"worktree", "add", dir, name}
	}
	if _, err := git(root, 60*time.Second, args...); err != nil {
		return failure("git worktree add: %v", err)
	}
	w := codingagents.Workspace{Name: name, Path: dir, Branch: name, Created: now()}
	if _, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		if j := findProject(*s, root); j >= 0 {
			s.Projects[j].Workspaces = append(s.Projects[j].Workspaces, w)
		}
		return nil
	}); err != nil {
		return failure("%v", err)
	}
	return map[string]any{"success": true, "workspace": WorkspaceRow{Project: root, Path: dir, Name: name, Branch: name, Git: true,
		Created: w.Created}, "workspaces": workspaceList(codingagents.LoadSettings().Projects)}
}

// removeWorkspace takes a workspace off its project's list; with remove, it
// also removes the worktree, which git refuses while it has changes not
// committed. The branch stays.
func (a *App) removeWorkspace(project, path string, remove bool) map[string]any {
	s := codingagents.LoadSettings()
	i := findProject(s, project)
	if i < 0 {
		return failure("%s is not one of your projects", project)
	}
	root := s.Projects[i].Path
	path = filepath.Clean(expandHome(path))
	found := false
	for _, w := range s.Projects[i].Workspaces {
		found = found || w.Path == path
	}
	if !found {
		return failure("%s is not a workspace of %s", path, filepath.Base(root))
	}
	if remove {
		if _, err := os.Stat(path); err == nil {
			if _, err := git(root, 60*time.Second, "worktree", "remove", path); err != nil {
				return failure("git worktree remove: %v", err)
			}
		} else {
			_, _ = git(root, 10*time.Second, "worktree", "prune")
		}
	}
	if _, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		if j := findProject(*s, root); j >= 0 {
			list := s.Projects[j].Workspaces[:0]
			for _, w := range s.Projects[j].Workspaces {
				if w.Path != path {
					list = append(list, w)
				}
			}
			s.Projects[j].Workspaces = list
		}
		return nil
	}); err != nil {
		return failure("%v", err)
	}
	return map[string]any{"success": true, "workspaces": workspaceList(codingagents.LoadSettings().Projects)}
}
