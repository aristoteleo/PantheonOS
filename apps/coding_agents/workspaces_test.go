//go:build !windows

package codingagentsapp

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// gitRepos sets up a git that knows only what each test tells it (no user
// or system config), and returns a runner that fails the test on error.
func gitRepos(t *testing.T, userName string) func(dir string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	cfg := filepath.Join(t.TempDir(), "gitconfig")
	body := "[init]\n\tdefaultBranch = main\n"
	if userName != "" {
		body += "[user]\n\tname = " + userName + "\n\temail = t@example.com\n"
	}
	if err := os.WriteFile(cfg, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GIT_CONFIG_GLOBAL", cfg)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	t.Setenv("GIT_AUTHOR_NAME", "t")
	t.Setenv("GIT_AUTHOR_EMAIL", "t@t")
	t.Setenv("GIT_COMMITTER_NAME", "t")
	t.Setenv("GIT_COMMITTER_EMAIL", "t@t")
	t.Setenv("USER", "tester")
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	forgetGit()
	return func(dir string, args ...string) string {
		t.Helper()
		out, err := exec.Command("git", append([]string{"-C", dir}, args...)...).CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
}

// newProject is a git repository with one commit, kept as a project.
func newProject(t *testing.T, a *App, run func(string, ...string) string, name string) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), name)
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	run(root, "init", "-q", "-b", "main")
	run(root, "commit", "-q", "--allow-empty", "-m", "start")
	addProject(t, a, root)
	return root
}

func addProject(t *testing.T, a *App, roots ...string) {
	t.Helper()
	list := []codingagents.Project{}
	for _, r := range roots {
		list = append(list, codingagents.Project{Path: r})
	}
	if res := a.configure(Configure{Projects: &list}); res["success"] != true {
		t.Fatal(res)
	}
}

func rowsOf(t *testing.T) []WorkspaceRow {
	t.Helper()
	forgetGit()
	return workspaceList(codingagents.LoadSettings().Projects)
}

var twoWords = regexp.MustCompile(`^[a-z]+-[a-z]+$`)

// A workspace made with no name gets a two-word one, on a branch under the
// git user's name, from the default branch, in a folder beside the project.
func TestWorkspaceGeneratedNameBranchAndBase(t *testing.T) {
	run := gitRepos(t, "Ada Lovelace")
	a := NewApp(nil, "", t.TempDir())
	root := newProject(t, a, run, "repo")
	// The project's own checkout is on another branch, with changes: neither matters.
	run(root, "checkout", "-q", "-b", "elsewhere")
	if err := os.WriteFile(filepath.Join(root, "wip.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	res := a.createWorkspace(WorkspaceCreate{Project: root, Agent: "codex", Prompt: "fix it"})
	if res["success"] != true {
		t.Fatal(res)
	}
	w := res["workspace"].(WorkspaceRow)
	if !twoWords.MatchString(w.Name) {
		t.Errorf("a generated name is two words: %q", w.Name)
	}
	if w.Branch != "ada-lovelace/"+w.Name || w.Base != "main" {
		t.Errorf("branch %q from %q", w.Branch, w.Base)
	}
	if w.Path != filepath.Join(filepath.Dir(root), "repo-worktrees", w.Name) {
		t.Errorf("path %s", w.Path)
	}
	if res["agent"] != "codex" || res["prompt"] != "fix it" {
		t.Errorf("the agent and prompt come back for the desktop to start: %v %v", res["agent"], res["prompt"])
	}
	if got := run(w.Path, "rev-parse", "--abbrev-ref", "HEAD"); got != w.Branch {
		t.Errorf("checked out %q", got)
	}
	if up, err := exec.Command("git", "-C", w.Path, "rev-parse", "--abbrev-ref", "@{upstream}").Output(); err == nil {
		t.Errorf("the new branch tracks nothing: %s", up)
	}
	// Another one gets another name.
	res2 := a.createWorkspace(WorkspaceCreate{Project: root})
	if res2["success"] != true || res2["workspace"].(WorkspaceRow).Name == w.Name {
		t.Fatalf("a second generated name: %v", res2)
	}
	// Without a git user name, the login name is the prefix.
	run(root, "config", "user.name", "")
	res3 := a.createWorkspace(WorkspaceCreate{Project: root, Name: "named"})
	if res3["success"] != true || res3["workspace"].(WorkspaceRow).Branch != "tester/named" {
		t.Fatalf("login-name prefix: %v", res3)
	}
}

// The default base: origin/HEAD's branch, else main or master, else HEAD.
func TestWorkspaceDefaultBase(t *testing.T) {
	run := gitRepos(t, "t")
	a := NewApp(nil, "", t.TempDir())
	// A clone of a repository whose default branch is trunk.
	upstream := filepath.Join(t.TempDir(), "up")
	if err := os.MkdirAll(upstream, 0o755); err != nil {
		t.Fatal(err)
	}
	run(upstream, "init", "-q", "-b", "trunk")
	run(upstream, "commit", "-q", "--allow-empty", "-m", "start")
	clone := filepath.Join(t.TempDir(), "clone")
	run(filepath.Dir(clone), "clone", "-q", upstream, clone)
	// A repository with master only, and one on a branch of its own name.
	master := filepath.Join(t.TempDir(), "old")
	other := filepath.Join(t.TempDir(), "odd")
	for dir, branch := range map[string]string{master: "master", other: "work"} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		run(dir, "init", "-q", "-b", branch)
		run(dir, "commit", "-q", "--allow-empty", "-m", "start")
	}
	addProject(t, a, clone, master, other)
	for dir, want := range map[string]string{clone: "origin/trunk", master: "master", other: "work"} {
		if got := defaultBase(dir); got != want {
			t.Errorf("%s: base %q, want %q", filepath.Base(dir), got, want)
		}
	}
	res := a.createWorkspace(WorkspaceCreate{Project: clone, Name: "x"})
	if res["success"] != true || res["workspace"].(WorkspaceRow).Base != "origin/trunk" {
		t.Fatalf("from origin's default: %v", res)
	}
	// A base given outright, and a bad one.
	run(other, "branch", "feature")
	if res := a.createWorkspace(WorkspaceCreate{Project: other, Name: "y", Base: "feature"}); res["success"] != true ||
		res["workspace"].(WorkspaceRow).Base != "feature" {
		t.Fatalf("given base: %v", res)
	}
	if res := a.createWorkspace(WorkspaceCreate{Project: other, Name: "z", Base: "nope"}); res["success"] != false ||
		!strings.Contains(res["error"].(string), "nope is not a branch or commit") {
		t.Errorf("a bad base: %v", res)
	}
}

// Clear errors: not git, no commit, a taken name or branch, a bad name.
func TestWorkspaceCreateErrors(t *testing.T) {
	run := gitRepos(t, "t")
	a := NewApp(nil, "", t.TempDir())
	plain := t.TempDir()
	empty := filepath.Join(t.TempDir(), "empty")
	if err := os.MkdirAll(empty, 0o755); err != nil {
		t.Fatal(err)
	}
	run(empty, "init", "-q", "-b", "main")
	root := filepath.Join(t.TempDir(), "repo")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	run(root, "init", "-q", "-b", "main")
	run(root, "commit", "-q", "--allow-empty", "-m", "start")
	addProject(t, a, plain, empty, root)
	errOf := func(req WorkspaceCreate) string {
		t.Helper()
		res := a.createWorkspace(req)
		if res["success"] != false {
			t.Fatalf("%+v should fail: %v", req, res)
		}
		return res["error"].(string)
	}
	if e := errOf(WorkspaceCreate{Project: plain}); !strings.Contains(e, "not a git repository") {
		t.Error(e)
	}
	if e := errOf(WorkspaceCreate{Project: empty}); !strings.Contains(e, "no commit yet") {
		t.Error(e)
	}
	if e := errOf(WorkspaceCreate{Project: root, Name: "../x"}); !strings.Contains(e, "workspace name") {
		t.Error(e)
	}
	if res := a.createWorkspace(WorkspaceCreate{Project: root, Name: "fix"}); res["success"] != true {
		t.Fatal(res)
	}
	if e := errOf(WorkspaceCreate{Project: root, Name: "fix"}); !strings.Contains(e, "already exists") {
		t.Error(e)
	}
	// The default branch of a new name is taken.
	run(root, "branch", "t/taken")
	if e := errOf(WorkspaceCreate{Project: root, Name: "taken"}); !strings.Contains(e, "branch t/taken already exists") {
		t.Error(e)
	}
	// A branch named outright that another checkout has.
	if e := errOf(WorkspaceCreate{Project: root, Name: "again", Branch: "main"}); !strings.Contains(e, "checked out in") {
		t.Error(e)
	}
	// One that nobody has checked out is used as it is.
	res := a.createWorkspace(WorkspaceCreate{Project: root, Name: "use", Branch: "t/taken"})
	if res["success"] != true || res["workspace"].(WorkspaceRow).Branch != "t/taken" {
		t.Errorf("an existing branch named outright: %v", res)
	}
	if e := errOf(WorkspaceCreate{Project: root, Name: "b", Branch: "bad..branch"}); !strings.Contains(e, "not a valid branch") {
		t.Error(e)
	}
}

// Deleting a worktree is refused while it has changes or commits only it
// has, unless forced; its branch goes too only when merged into the base.
func TestWorkspaceRemoveGuards(t *testing.T) {
	run := gitRepos(t, "t")
	a := NewApp(nil, "", t.TempDir())
	root := newProject(t, a, run, "repo")
	mk := func(name string) string {
		t.Helper()
		res := a.createWorkspace(WorkspaceCreate{Project: root, Name: name})
		if res["success"] != true {
			t.Fatal(res)
		}
		return res["workspace"].(WorkspaceRow).Path
	}
	remove := func(req WorkspaceRemove) map[string]any {
		req.Project, req.Remove = root, true
		return a.removeWorkspace(req)
	}
	// The project's own folder is not deleted.
	if res := remove(WorkspaceRemove{Path: root}); res["success"] != false || !strings.Contains(res["error"].(string), "remove the project") {
		t.Errorf("primary: %v", res)
	}
	// Changes not committed.
	dirty := mk("dirty")
	if err := os.WriteFile(filepath.Join(dirty, "wip.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	st := a.workspaceStatus(root, dirty)["blockers"].(workspaceBlockers)
	if st.Changed != 1 || st.Changes[0] != "wip.txt" || !st.Merged {
		t.Errorf("status: %+v", st)
	}
	res := remove(WorkspaceRemove{Path: dirty})
	if res["success"] != false || !strings.Contains(res["error"].(string), "1 file has changes not committed") {
		t.Errorf("dirty: %v", res)
	}
	if _, err := os.Stat(dirty); err != nil {
		t.Errorf("the dirty worktree stays: %v", err)
	}
	// A commit no remote and not the base has.
	ahead := mk("ahead")
	run(ahead, "commit", "-q", "--allow-empty", "-m", "mine")
	if res := remove(WorkspaceRemove{Path: ahead}); res["success"] != false ||
		!strings.Contains(res["error"].(string), "1 commit on t/ahead is on no remote and not in main") {
		t.Errorf("unpushed: %v", res)
	}
	// Forced, with delete_branch: the worktree goes, the unmerged branch stays.
	res = remove(WorkspaceRemove{Path: ahead, Force: true, DeleteBranch: true})
	if res["success"] != true || res["removed"] != true || res["branch_deleted"] != false ||
		!strings.Contains(res["note"].(string), "not merged into main") {
		t.Errorf("forced: %v", res)
	}
	if !branchExists(root, "t/ahead") {
		t.Error("an unmerged branch is kept")
	}
	// Merged into the base: the branch is deleted with it.
	merged := mk("merged")
	run(merged, "commit", "-q", "--allow-empty", "-m", "done")
	run(root, "merge", "-q", "--ff-only", "t/merged")
	res = remove(WorkspaceRemove{Path: merged, DeleteBranch: true})
	if res["success"] != true || res["branch_deleted"] != true {
		t.Errorf("merged: %v", res)
	}
	if branchExists(root, "t/merged") {
		t.Error("the merged branch is deleted")
	}
	// Forced over changes.
	if res := remove(WorkspaceRemove{Path: dirty, Force: true}); res["success"] != true {
		t.Errorf("forced dirty: %v", res)
	}
	if _, err := os.Stat(dirty); !os.IsNotExist(err) {
		t.Errorf("removed: %v", err)
	}
	if got := codingagents.LoadSettings().Projects[0].Workspaces; len(got) != 0 {
		t.Errorf("all off the list: %+v", got)
	}
}

// Worktrees made by hand are listed as external, not kept until adopted.
func TestWorkspaceExternalAndAdopt(t *testing.T) {
	run := gitRepos(t, "t")
	a := NewApp(nil, "", t.TempDir())
	root := newProject(t, a, run, "repo")
	hand := filepath.Join(t.TempDir(), "by-hand")
	run(root, "worktree", "add", "-q", "-b", "hand", hand)
	rows := rowsOf(t)
	if len(rows) != 2 || !rows[0].Primary || !rows[1].External || rows[1].Branch != "hand" || rows[1].Name != "by-hand" {
		t.Fatalf("external: %+v", rows)
	}
	if len(codingagents.LoadSettings().Projects[0].Workspaces) != 0 {
		t.Error("not kept until adopted")
	}
	if res := a.adoptWorkspace(root, filepath.Join(t.TempDir(), "nope")); res["success"] != false {
		t.Errorf("not a worktree: %v", res)
	}
	if res := a.adoptWorkspace(root, rows[1].Path); res["success"] != true {
		t.Fatal(res)
	}
	rows = rowsOf(t)
	if len(rows) != 2 || rows[1].External || rows[1].Branch != "hand" || rows[1].Base != "main" {
		t.Fatalf("adopted: %+v", rows)
	}
	if res := a.adoptWorkspace(root, hand); res["success"] != false {
		t.Errorf("twice: %v", res)
	}
}

// A card's git state: changes, and commits ahead of and behind the base.
func TestWorkspaceRowState(t *testing.T) {
	run := gitRepos(t, "t")
	a := NewApp(nil, "", t.TempDir())
	root := newProject(t, a, run, "repo")
	res := a.createWorkspace(WorkspaceCreate{Project: root, Name: "s"})
	dir := res["workspace"].(WorkspaceRow).Path
	run(dir, "commit", "-q", "--allow-empty", "-m", "a")
	run(dir, "commit", "-q", "--allow-empty", "-m", "b")
	run(root, "commit", "-q", "--allow-empty", "-m", "c")
	if err := os.WriteFile(filepath.Join(dir, "new.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	rows := rowsOf(t)
	w := rows[1]
	if w.Dirty == nil || !*w.Dirty || w.Ahead == nil || *w.Ahead != 2 || w.Behind == nil || *w.Behind != 1 {
		t.Fatalf("state: %+v", w)
	}
	if rows[0].Dirty == nil || *rows[0].Dirty || rows[0].Ahead != nil {
		t.Errorf("the primary checkout is clean and is the base itself: %+v", rows[0])
	}
	b, _ := json.Marshal(rows[0])
	if !strings.Contains(string(b), `"primary":true`) || !strings.Contains(string(b), `"main":true`) {
		t.Errorf("primary is always sent, main kept for older desktops: %s", b)
	}
}

// Settings written before workspaces kept a base load and list as before.
func TestWorkspaceOldSettings(t *testing.T) {
	run := gitRepos(t, "t")
	a := NewApp(nil, "", t.TempDir())
	root := newProject(t, a, run, "repo")
	dir := filepath.Join(filepath.Dir(root), "repo-worktrees", "fix-upload")
	run(root, "worktree", "add", "-q", "-b", "fix-upload", dir)
	old := `{"agents":{"claude":{"model":"opus"}},"projects":[{"path":"` + root +
		`","workspaces":[{"name":"fix-upload","path":"` + dir + `","branch":"fix-upload","created":1}]}]}`
	if err := os.WriteFile(filepath.Join(codingagents.Root(), "settings.json"), []byte(old), 0o600); err != nil {
		t.Fatal(err)
	}
	s := codingagents.LoadSettings()
	if s.Agents["claude"].Model != "opus" || len(s.Projects) != 1 || len(s.Projects[0].Workspaces) != 1 {
		t.Fatalf("loaded: %+v", s)
	}
	rows := rowsOf(t)
	if len(rows) != 2 || rows[1].Name != "fix-upload" || rows[1].External || rows[1].Base != "main" || rows[1].Created != 1 {
		t.Fatalf("listed: %+v", rows)
	}
	// An older desktop's name with a slash is the branch, as it was.
	res := a.createWorkspace(WorkspaceCreate{Project: root, Name: "feat/x"})
	if res["success"] != true || res["workspace"].(WorkspaceRow).Branch != "feat/x" || res["workspace"].(WorkspaceRow).Name != "feat-x" {
		t.Errorf("slash name: %v", res)
	}
	if res := a.removeWorkspace(WorkspaceRemove{Project: root, Path: dir, Remove: true}); res["success"] != true {
		t.Errorf("an old workspace is removed: %v", res)
	}
}

// A folder that is no repository becomes one with a first commit; the home
// folder is not made one.
func TestProjectGitInit(t *testing.T) {
	run := gitRepos(t, "")
	a := NewApp(nil, "", t.TempDir())
	t.Setenv("GIT_AUTHOR_NAME", "")
	t.Setenv("GIT_AUTHOR_EMAIL", "")
	t.Setenv("GIT_COMMITTER_NAME", "")
	t.Setenv("GIT_COMMITTER_EMAIL", "")
	home := t.TempDir()
	t.Setenv("HOME", home)
	folder := filepath.Join(home, "notes")
	if err := os.MkdirAll(folder, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(folder, "a.md"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	addProject(t, a, folder, home)
	if res := a.initGit(home); res["success"] != false || !strings.Contains(res["error"].(string), "home folder") {
		t.Errorf("home: %v", res)
	}
	if _, err := os.Stat(filepath.Join(home, ".git")); err == nil {
		t.Error("no repository in home")
	}
	if res := a.initGit(folder); res["success"] != true {
		t.Fatal(res)
	}
	if got := run(folder, "ls-files"); got != "a.md" {
		t.Errorf("the first commit holds the files: %q", got)
	}
	if res := a.initGit(folder); res["success"] != false {
		t.Errorf("twice: %v", res)
	}
	if res := a.createWorkspace(WorkspaceCreate{Project: folder, Name: "w"}); res["success"] != true {
		t.Errorf("then workspaces: %v", res)
	}
}

// A new workspace's agent starts with its first prompt on its command line.
func TestTerminalCommandFirstPrompt(t *testing.T) {
	fakeAgent(t, "claude", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	res := a.terminalCommand(TerminalArgs{Agent: "claude", Cwd: "/w", Prompt: "Fix the tests"})
	argv, _ := res["argv"].([]string)
	if res["prompted"] != true || len(argv) == 0 || argv[len(argv)-1] != "Fix the tests" {
		t.Fatalf("argv %q %v", argv, res["prompted"])
	}
	res = a.terminalCommand(TerminalArgs{Agent: "claude", Cwd: "/w"})
	if res["prompted"] != false {
		t.Errorf("no prompt: %v", res["prompted"])
	}
	if got := promptArgs("gemini", "-x"); len(got) != 2 || got[0] != "--prompt-interactive" || got[1] != " -x" {
		t.Errorf("gemini %q", got)
	}
	if promptArgs("goose", "hi") != nil {
		t.Error("an agent without a prompt argument gets none")
	}
}

// The old flow, end to end: make one, keep it when the list is saved, remove it.
func TestWorkspaces(t *testing.T) {
	run := gitRepos(t, "t")
	a := NewApp(nil, "", t.TempDir())
	root := newProject(t, a, run, "repo")
	res := a.createWorkspace(WorkspaceCreate{Project: root, Name: "fix-upload"})
	if res["success"] != true {
		t.Fatal(res)
	}
	dir := filepath.Join(filepath.Dir(root), "repo-worktrees", "fix-upload")
	rows := rowsOf(t)
	if len(rows) != 2 || !rows[0].Main || rows[0].Branch != "main" || rows[1].Path != dir || rows[1].Branch != "t/fix-upload" {
		t.Fatalf("the project's folder, then its workspace: %+v", rows)
	}
	addProject(t, a, root)
	if got := codingagents.LoadSettings().Projects[0].Workspaces; len(got) != 1 || got[0].Base != "main" {
		t.Fatalf("saving the list keeps the workspaces: %+v", got)
	}
	if res := a.removeWorkspace(WorkspaceRemove{Project: root, Path: dir, Remove: true}); res["success"] != true {
		t.Fatal(res)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Errorf("the worktree is removed: %v", err)
	}
}
