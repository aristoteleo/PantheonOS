package codingagentsapp

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// A project's workspaces: its own folder (the primary workspace), and git
// worktrees made for it, each a checkout of its own branch in a folder
// beside the project's (<parent>/<project>-worktrees/<name>), so agents in
// different workspaces do not change each other's files. Worktrees made
// outside the app are listed as external until the user adopts them.

// WorkspaceRow is one workspace as the desktop lists it.
type WorkspaceRow struct {
	Project string `json:"project"`
	Path    string `json:"path"`
	Name    string `json:"name"`
	Branch  string `json:"branch,omitempty"`
	// Primary: the project's own folder, its main checkout. Always sent, so
	// the desktop can tell this service from an older one.
	Primary bool `json:"primary"`
	// Main is Primary, for older desktops.
	Main bool `json:"main,omitempty"`
	// Git: the project is a git repository, so workspaces can be made in it.
	Git bool `json:"git,omitempty"`
	// Base: what the workspace's branch is compared with.
	Base    string  `json:"base,omitempty"`
	Created float64 `json:"created,omitempty"`
	// External: a worktree of the project made outside the app, not kept
	// in the project's list until adopted.
	External bool `json:"external,omitempty"`
	// Missing: a worktree whose folder is gone.
	Missing bool `json:"missing,omitempty"`
	// Dirty, Ahead and Behind are the checkout's git state, when git tells
	// them quickly; left out otherwise.
	Dirty  *bool `json:"dirty,omitempty"`
	Ahead  *int  `json:"ahead,omitempty"`
	Behind *int  `json:"behind,omitempty"`
}

// stateLimit bounds each git call that reads a card's state: a repository
// too big to answer quickly shows no state rather than slowing the list.
const stateLimit = 300 * time.Millisecond

var (
	branchName    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]*$`)
	workspaceName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)
)

// gitCache keeps answers git gives about the projects for a few seconds,
// so listing workspaces often stays cheap. Any change to a project's
// workspaces clears it.
var gitCache = struct {
	sync.Mutex
	m map[string]gitCached
}{m: map[string]gitCached{}}

type gitCached struct {
	val any
	at  time.Time
}

func cachedGit[T any](key string, ttl time.Duration, read func() T) T {
	gitCache.Lock()
	c, ok := gitCache.m[key]
	gitCache.Unlock()
	if ok && time.Since(c.at) < ttl {
		return c.val.(T)
	}
	v := read()
	gitCache.Lock()
	gitCache.m[key] = gitCached{v, time.Now()}
	gitCache.Unlock()
	return v
}

func forgetGit() {
	gitCache.Lock()
	gitCache.m = map[string]gitCached{}
	gitCache.Unlock()
}

// workspaceList is every project's workspaces: each project's own folder
// first, then the worktrees kept for it, then the ones made outside the app.
// git is asked about the rows side by side, and its answers kept briefly.
func workspaceList(projects []codingagents.Project) []WorkspaceRow {
	out := []WorkspaceRow{}
	var fill []func()
	for _, p := range projects {
		branch, isGit := gitBranch(p.Path)
		primary := WorkspaceRow{Project: p.Path, Path: p.Path, Name: filepath.Base(p.Path), Branch: branch, Primary: true,
			Main: true, Git: isGit}
		base := ""
		if isGit {
			base = defaultBase(p.Path)
			if base != branch && base != "" {
				primary.Base = base
			}
		}
		out = append(out, primary)
		at := len(out) - 1
		if isGit {
			fill = append(fill, func() { withState(&out[at]) })
		}
		kept := map[string]bool{realPath(p.Path): true}
		for _, w := range p.Workspaces {
			kept[realPath(w.Path)] = true
			row := WorkspaceRow{Project: p.Path, Path: w.Path, Name: w.Name, Branch: w.Branch, Git: true, Base: w.Base,
				Created: w.Created}
			if row.Base == "" {
				row.Base = base
			}
			out = append(out, row)
			at := len(out) - 1
			fill = append(fill, func() {
				r := &out[at]
				if b, ok := gitBranch(r.Path); ok {
					r.Branch = b
					withState(r)
				} else if _, err := os.Stat(r.Path); err != nil {
					r.Missing = true
				}
			})
		}
		if !isGit {
			continue
		}
		for _, wt := range worktrees(p.Path) {
			if kept[realPath(wt.Path)] || wt.Bare || wt.Prunable {
				continue
			}
			out = append(out, WorkspaceRow{Project: p.Path, Path: wt.Path, Name: filepath.Base(wt.Path), Branch: wt.Branch,
				Git: true, Base: base, External: true})
			at := len(out) - 1
			fill = append(fill, func() { withState(&out[at]) })
		}
	}
	var wg sync.WaitGroup
	slots := make(chan struct{}, 8)
	for _, f := range fill {
		wg.Add(1)
		slots <- struct{}{}
		go func() {
			defer func() { <-slots; wg.Done() }()
			f()
		}()
	}
	wg.Wait()
	return out
}

// withState adds a checkout's git state to its row, when git answers quickly.
func withState(row *WorkspaceRow) {
	st := cachedGit("state\x00"+row.Path+"\x00"+row.Base, 5*time.Second, func() checkoutState {
		return readState(row.Path, row.Base)
	})
	row.Dirty, row.Ahead, row.Behind = st.Dirty, st.Ahead, st.Behind
}

type checkoutState struct {
	Dirty         *bool
	Ahead, Behind *int
}

func readState(dir, base string) checkoutState {
	var st checkoutState
	if out, err := git(dir, stateLimit, "status", "--porcelain", "--untracked-files=normal"); err == nil {
		dirty := strings.TrimSpace(out) != ""
		st.Dirty = &dirty
	}
	if base == "" {
		return st
	}
	if out, err := git(dir, stateLimit, "rev-list", "--left-right", "--count", base+"...HEAD"); err == nil {
		f := strings.Fields(out)
		if len(f) == 2 {
			behind, e1 := strconv.Atoi(f[0])
			ahead, e2 := strconv.Atoi(f[1])
			if e1 == nil && e2 == nil {
				st.Ahead, st.Behind = &ahead, &behind
			}
		}
	}
	return st
}

// gitBranch is the branch checked out in a folder, and whether it is in a
// git repository at all.
func gitBranch(dir string) (string, bool) {
	type answer struct {
		branch string
		git    bool
	}
	a := cachedGit("branch\x00"+dir, 3*time.Second, func() answer {
		b, ok := readBranch(dir)
		return answer{b, ok}
	})
	return a.branch, a.git
}

func readBranch(dir string) (string, bool) {
	out, err := git(dir, 3*time.Second, "rev-parse", "--is-inside-work-tree", "--abbrev-ref", "HEAD")
	if lines := strings.Fields(out); err == nil && len(lines) == 2 && lines[0] == "true" {
		if lines[1] != "HEAD" {
			return lines[1], true
		}
		// A detached HEAD: its short commit.
		b, _ := git(dir, 3*time.Second, "rev-parse", "--short", "HEAD")
		return strings.TrimSpace(b), true
	}
	// No commit yet, or no repository.
	out, err = git(dir, 3*time.Second, "rev-parse", "--is-inside-work-tree")
	if err != nil || strings.TrimSpace(out) != "true" {
		return "", false
	}
	b, _ := git(dir, 3*time.Second, "symbolic-ref", "--short", "-q", "HEAD")
	return strings.TrimSpace(b), true
}

// defaultBase is what a new workspace starts from: the branch origin/HEAD
// points at, else main or master, else what the project has checked out.
func defaultBase(root string) string {
	return cachedGit("base\x00"+root, 30*time.Second, func() string {
		if out, err := git(root, 3*time.Second, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"); err == nil {
			if ref := strings.TrimSpace(out); ref != "" && hasCommit(root, ref) {
				return ref
			}
		}
		for _, b := range []string{"main", "master"} {
			if _, err := git(root, 3*time.Second, "show-ref", "--verify", "-q", "refs/heads/"+b); err == nil {
				return b
			}
		}
		b, _ := gitBranch(root)
		return b
	})
}

func hasCommit(root, ref string) bool {
	_, err := git(root, 3*time.Second, "rev-parse", "--verify", "-q", ref+"^{commit}")
	return err == nil
}

func branchExists(root, branch string) bool {
	_, err := git(root, 3*time.Second, "show-ref", "--verify", "-q", "refs/heads/"+branch)
	return err == nil
}

// worktreeInfo is one entry of git worktree list.
type worktreeInfo struct {
	Path, Branch     string
	Bare, Prunable   bool
	Detached, Locked bool
}

func worktrees(root string) []worktreeInfo {
	return cachedGit("worktrees\x00"+root, 5*time.Second, func() []worktreeInfo { return readWorktrees(root) })
}

func readWorktrees(root string) []worktreeInfo {
	out, err := git(root, 5*time.Second, "worktree", "list", "--porcelain")
	if err != nil {
		return nil
	}
	var list []worktreeInfo
	var cur *worktreeInfo
	sc := bufio.NewScanner(strings.NewReader(out))
	for sc.Scan() {
		line := sc.Text()
		key, val, _ := strings.Cut(line, " ")
		switch key {
		case "worktree":
			list = append(list, worktreeInfo{Path: val})
			cur = &list[len(list)-1]
		case "branch":
			if cur != nil {
				cur.Branch = strings.TrimPrefix(val, "refs/heads/")
			}
		case "bare":
			if cur != nil {
				cur.Bare = true
			}
		case "detached":
			if cur != nil {
				cur.Detached = true
			}
		case "prunable":
			if cur != nil {
				cur.Prunable = true
			}
		case "locked":
			if cur != nil {
				cur.Locked = true
			}
		}
	}
	return list
}

// realPath resolves links, so paths git reports and paths kept in the
// settings compare (macOS's /var is /private/var).
func realPath(p string) string {
	if r, err := filepath.EvalSymlinks(p); err == nil {
		return r
	}
	return filepath.Clean(p)
}

func git(dir string, limit time.Duration, args ...string) (string, error) {
	return gitEnv(dir, limit, nil, args...)
}

func gitEnv(dir string, limit time.Duration, extra []string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", append([]string{"-C", dir}, args...)...)
	cmd.Env = append(codingagents.AgentEnv(""), extra...)
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

// WorkspaceCreate is what agents_workspace_create takes. Agent and Prompt
// are handed back for the desktop, which starts the agent in the new
// workspace in a tab of its own.
type WorkspaceCreate struct {
	Project, Name, Base, Branch, Agent, Prompt string
}

// worktreeDir is where a project's worktree of that name lives.
func worktreeDir(root, name string) string {
	return filepath.Join(filepath.Dir(root), filepath.Base(root)+"-worktrees", name)
}

// createWorkspace makes a git worktree of a project, on a new branch made
// from the base (or on an existing branch named outright), and keeps it in
// the project's list. A tree with changes does not stop it: the worktree
// starts from the base's commit.
func (a *App) createWorkspace(req WorkspaceCreate) map[string]any {
	s := codingagents.LoadSettings()
	i := findProject(s, req.Project)
	if i < 0 {
		return failure("%s is not one of your projects", req.Project)
	}
	root := s.Projects[i].Path
	if _, ok := gitBranch(root); !ok {
		return failure("%s is not a git repository; workspaces are git worktrees. Initialise git in it first", filepath.Base(root))
	}
	if !hasCommit(root, "HEAD") {
		return failure("The repository has no commit yet; commit once, then add a workspace")
	}
	name := strings.TrimSpace(req.Name)
	branch := strings.TrimSpace(req.Branch)
	if strings.Contains(name, "/") {
		// A name like a branch (an older desktop's): it is the branch too.
		if !branchName.MatchString(name) || strings.Contains(name, "..") || strings.HasSuffix(name, "/") {
			return failure("A workspace name is letters, digits, - _ and .")
		}
		if branch == "" {
			branch = name
		}
		name = strings.ReplaceAll(name, "/", "-")
	}
	if name == "" {
		name = newWorkspaceName(root, s.Projects[i].Workspaces)
	}
	if !workspaceName.MatchString(name) || strings.Contains(name, "..") || strings.HasSuffix(name, ".lock") || len(name) > 64 {
		return failure("A workspace name is letters, digits, - _ and . (at most 64)")
	}
	explicitBranch := branch != ""
	if branch == "" {
		branch = branchFor(root, name)
	}
	if _, err := git(root, 3*time.Second, "check-ref-format", "--branch", branch); err != nil || strings.HasPrefix(branch, "-") {
		return failure("%s is not a valid branch name", branch)
	}
	base := strings.TrimSpace(req.Base)
	if base == "" {
		base = defaultBase(root)
	} else if strings.HasPrefix(base, "-") || !hasCommit(root, base) {
		return failure("%s is not a branch or commit in %s", base, filepath.Base(root))
	}
	dir := worktreeDir(root, name)
	if _, err := os.Stat(dir); err == nil {
		return failure("A workspace named %s already exists (%s); choose another name", name, dir)
	}
	for _, w := range s.Projects[i].Workspaces {
		if w.Name == name {
			return failure("A workspace named %s already exists; choose another name", name)
		}
	}
	args := []string{"worktree", "add", "--no-track", "-b", branch, dir, base}
	if branchExists(root, branch) {
		if !explicitBranch {
			return failure("The branch %s already exists; choose another name, or name the branch under Advanced to use it", branch)
		}
		for _, wt := range readWorktrees(root) {
			if wt.Branch == branch {
				return failure("The branch %s is checked out in %s already", branch, wt.Path)
			}
		}
		// An existing branch named outright: the workspace checks it out.
		args = []string{"worktree", "add", dir, branch}
	}
	if err := os.MkdirAll(filepath.Dir(dir), 0o755); err != nil {
		return failure("%v", err)
	}
	if _, err := git(root, 60*time.Second, args...); err != nil {
		return failure("git worktree add: %v", err)
	}
	forgetGit()
	w := codingagents.Workspace{Name: name, Path: dir, Branch: branch, Base: base, Created: now()}
	if _, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		if j := findProject(*s, root); j >= 0 {
			s.Projects[j].Workspaces = append(s.Projects[j].Workspaces, w)
		}
		return nil
	}); err != nil {
		return failure("%v", err)
	}
	row := WorkspaceRow{Project: root, Path: dir, Name: name, Branch: branch, Git: true, Base: base, Created: w.Created}
	withState(&row)
	return map[string]any{"success": true, "workspace": row, "agent": req.Agent, "prompt": req.Prompt,
		"workspaces": workspaceList(codingagents.LoadSettings().Projects)}
}

// branchFor is a new workspace's branch: <who>/<name>, who being the git
// user's name (or the login name) as a slug.
func branchFor(root, name string) string {
	who := ""
	if out, err := git(root, 3*time.Second, "config", "user.name"); err == nil {
		who = slug(out)
	}
	if who == "" {
		who = slug(os.Getenv("USER"))
	}
	if who == "" {
		return name
	}
	return who + "/" + name
}

var notSlug = regexp.MustCompile(`[^a-z0-9]+`)

func slug(s string) string {
	s = strings.Trim(notSlug.ReplaceAllString(strings.ToLower(strings.TrimSpace(s)), "-"), "-")
	if len(s) > 24 {
		s = strings.TrimRight(s[:24], "-")
	}
	return s
}

// workspaceBlockers is what deleting a worktree would lose: files with
// changes not committed, and commits on its branch that no remote and not
// the base has.
type workspaceBlockers struct {
	Changes  []string `json:"changes"`
	Changed  int      `json:"changed"`
	Unpushed int      `json:"unpushed"`
	// Merged: the branch is in the base, so deleting it loses nothing.
	Merged bool   `json:"merged"`
	Branch string `json:"branch,omitempty"`
	Base   string `json:"base,omitempty"`
}

func blockersOf(root, dir string, w codingagents.Workspace) workspaceBlockers {
	b := workspaceBlockers{Changes: []string{}, Branch: w.Branch, Base: w.Base}
	if b.Base == "" {
		b.Base = defaultBase(root)
	}
	if cur, ok := gitBranch(dir); ok && cur != "" {
		b.Branch = cur
	}
	if out, err := git(dir, 10*time.Second, "status", "--porcelain", "--untracked-files=normal"); err == nil {
		for _, line := range strings.Split(strings.TrimRight(out, "\n"), "\n") {
			if len(line) > 3 {
				b.Changed++
				if len(b.Changes) < 20 {
					b.Changes = append(b.Changes, strings.TrimSpace(line[3:]))
				}
			}
		}
	}
	args := []string{"rev-list", "--count", "HEAD", "--not", "--remotes"}
	if b.Base != "" && hasCommit(root, b.Base) {
		args = append(args, b.Base)
	}
	if out, err := git(dir, 10*time.Second, args...); err == nil {
		b.Unpushed, _ = strconv.Atoi(strings.TrimSpace(out))
	}
	b.Merged = b.Branch != "" && b.Base != "" && isMerged(root, b.Branch, b.Base)
	return b
}

func isMerged(root, branch, base string) bool {
	_, err := git(root, 10*time.Second, "merge-base", "--is-ancestor", "refs/heads/"+branch, base)
	return err == nil
}

// keptWorkspace finds a workspace in a project's list.
func keptWorkspace(p codingagents.Project, path string) (codingagents.Workspace, bool) {
	for _, w := range p.Workspaces {
		if w.Path == path || realPath(w.Path) == realPath(path) {
			return w, true
		}
	}
	return codingagents.Workspace{}, false
}

// workspaceStatus is what deleting a workspace would lose, for the desktop
// to ask about first.
func (a *App) workspaceStatus(project, path string) map[string]any {
	s := codingagents.LoadSettings()
	i := findProject(s, project)
	if i < 0 {
		return failure("%s is not one of your projects", project)
	}
	root := s.Projects[i].Path
	path = filepath.Clean(expandHome(path))
	w, ok := keptWorkspace(s.Projects[i], path)
	if !ok && realPath(path) != realPath(root) {
		return failure("%s is not a workspace of %s", path, filepath.Base(root))
	}
	if !ok {
		branch, _ := gitBranch(root)
		w = codingagents.Workspace{Path: root, Branch: branch}
	}
	if _, err := os.Stat(path); err != nil {
		return map[string]any{"success": true, "missing": true, "primary": !ok, "blockers": workspaceBlockers{Changes: []string{}}}
	}
	return map[string]any{"success": true, "primary": !ok, "blockers": blockersOf(root, path, w)}
}

// WorkspaceRemove is what agents_workspace_remove takes.
type WorkspaceRemove struct {
	Project, Path string
	// Remove the worktree too, not only take it off the list.
	Remove bool
	// Force removes it even with changes not committed or commits only it has.
	Force bool
	// DeleteBranch deletes its branch too, when the base has all of it.
	DeleteBranch bool
}

// removeWorkspace takes a workspace off its project's list; with remove, it
// also removes the worktree, refusing while it has changes not committed or
// commits no remote and not the base has, unless forced. With delete_branch
// the branch goes too, only when it is merged into the base.
func (a *App) removeWorkspace(req WorkspaceRemove) map[string]any {
	s := codingagents.LoadSettings()
	i := findProject(s, req.Project)
	if i < 0 {
		return failure("%s is not one of your projects", req.Project)
	}
	root := s.Projects[i].Path
	path := filepath.Clean(expandHome(req.Path))
	if realPath(path) == realPath(root) {
		return failure("%s is the project's own folder; remove the project from the list instead", filepath.Base(root))
	}
	w, found := keptWorkspace(s.Projects[i], path)
	if !found {
		return failure("%s is not a workspace of %s", path, filepath.Base(root))
	}
	res := map[string]any{"success": true, "removed": false, "branch_deleted": false}
	var notes []string
	branch := ""
	if req.Remove {
		if _, err := os.Stat(path); err == nil {
			b := blockersOf(root, path, w)
			branch = b.Branch
			if !req.Force && (b.Changed > 0 || b.Unpushed > 0) {
				var why []string
				if b.Changed > 0 {
					why = append(why, plural(b.Changed, "file has", "files have")+" changes not committed")
				}
				if b.Unpushed > 0 {
					why = append(why, fmt.Sprintf("%s on %s %s on no remote and not in %s", plural(b.Unpushed, "commit", "commits"),
						b.Branch, map[bool]string{true: "is", false: "are"}[b.Unpushed == 1], b.Base))
				}
				r := failure("%s is kept: %s. Delete it anyway to lose them", w.Name, strings.Join(why, "; "))
				r["blockers"] = b
				return r
			}
			args := []string{"worktree", "remove", path}
			if req.Force {
				args = []string{"worktree", "remove", "--force", path}
			}
			if _, err := git(root, 60*time.Second, args...); err != nil {
				return failure("git worktree remove: %v", err)
			}
			res["removed"] = true
		} else {
			_, _ = git(root, 10*time.Second, "worktree", "prune")
			res["removed"] = true
		}
		if branch == "" {
			branch = w.Branch
		}
		if req.DeleteBranch && branch != "" && branchExists(root, branch) {
			base := w.Base
			if base == "" {
				base = defaultBase(root)
			}
			res["branch"] = branch
			if base != "" && isMerged(root, branch, base) {
				if _, err := git(root, 10*time.Second, "branch", "-D", branch); err != nil {
					notes = append(notes, fmt.Sprintf("The branch %s is kept: %v", branch, err))
				} else {
					res["branch_deleted"] = true
				}
			} else {
				notes = append(notes, fmt.Sprintf("The branch %s is kept: it is not merged into %s", branch, base))
			}
		}
	}
	forgetGit()
	if _, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		if j := findProject(*s, root); j >= 0 {
			list := s.Projects[j].Workspaces[:0]
			for _, x := range s.Projects[j].Workspaces {
				if x.Path != w.Path {
					list = append(list, x)
				}
			}
			s.Projects[j].Workspaces = list
		}
		return nil
	}); err != nil {
		return failure("%v", err)
	}
	if len(notes) > 0 {
		res["note"] = strings.Join(notes, ". ")
	}
	res["workspaces"] = workspaceList(codingagents.LoadSettings().Projects)
	return res
}

func plural(n int, one, many string) string {
	if n == 1 {
		return "1 " + one
	}
	return fmt.Sprintf("%d %s", n, many)
}

// adoptWorkspace keeps a worktree made outside the app in its project's list.
func (a *App) adoptWorkspace(project, path string) map[string]any {
	s := codingagents.LoadSettings()
	i := findProject(s, project)
	if i < 0 {
		return failure("%s is not one of your projects", project)
	}
	root := s.Projects[i].Path
	path = filepath.Clean(expandHome(path))
	if _, ok := keptWorkspace(s.Projects[i], path); ok {
		return failure("%s is already a workspace of %s", path, filepath.Base(root))
	}
	var wt *worktreeInfo
	for _, x := range readWorktrees(root) {
		if realPath(x.Path) == realPath(path) && realPath(x.Path) != realPath(root) && !x.Bare {
			x := x
			wt = &x
		}
	}
	if wt == nil {
		return failure("%s is not a worktree of %s", path, filepath.Base(root))
	}
	w := codingagents.Workspace{Name: filepath.Base(path), Path: path, Branch: wt.Branch, Base: defaultBase(root), Created: now()}
	forgetGit()
	if _, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		if j := findProject(*s, root); j >= 0 {
			s.Projects[j].Workspaces = append(s.Projects[j].Workspaces, w)
		}
		return nil
	}); err != nil {
		return failure("%v", err)
	}
	row := WorkspaceRow{Project: root, Path: path, Name: w.Name, Branch: w.Branch, Git: true, Base: w.Base, Created: w.Created}
	withState(&row)
	return map[string]any{"success": true, "workspace": row, "workspaces": workspaceList(codingagents.LoadSettings().Projects)}
}

// initGit makes a project folder a git repository with a first commit of
// what it holds (as .gitignore allows), so workspaces can be made of it.
// The home folder is refused: a repository there would hold everything.
func (a *App) initGit(project string) map[string]any {
	s := codingagents.LoadSettings()
	i := findProject(s, project)
	if i < 0 {
		return failure("%s is not one of your projects", project)
	}
	root := s.Projects[i].Path
	if home, err := os.UserHomeDir(); err == nil && realPath(home) == realPath(root) {
		return failure("Your home folder is not made a git repository; add a project folder inside it for workspaces")
	}
	if _, ok := gitBranch(root); ok {
		return failure("%s is in a git repository already", filepath.Base(root))
	}
	if _, err := git(root, 10*time.Second, "init", "-q"); err != nil {
		return failure("git init: %v", err)
	}
	var who []string
	if out, err := git(root, 3*time.Second, "config", "user.email"); err != nil || strings.TrimSpace(out) == "" {
		// git refuses to commit without a name to sign with.
		user := os.Getenv("USER")
		if user == "" {
			user = "pantheon"
		}
		host, _ := os.Hostname()
		if host == "" {
			host = "localhost"
		}
		who = []string{"GIT_AUTHOR_NAME=" + user, "GIT_COMMITTER_NAME=" + user,
			"GIT_AUTHOR_EMAIL=" + user + "@" + host, "GIT_COMMITTER_EMAIL=" + user + "@" + host}
	}
	if _, err := git(root, 120*time.Second, "add", "-A"); err != nil {
		return failure("git init worked, but git add did not: %v", err)
	}
	if _, err := gitEnv(root, 120*time.Second, who, "commit", "-q", "--allow-empty", "--no-verify", "-m", "Initial commit"); err != nil {
		return failure("git init worked, but the first commit did not: %v", err)
	}
	forgetGit()
	return map[string]any{"success": true, "workspaces": workspaceList(codingagents.LoadSettings().Projects)}
}
