//go:build !windows

package codingagentsapp

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"hash/crc32"
	"image"
	"image/color"
	"image/gif"
	"image/jpeg"
	"image/png"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

func gitRun(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir, "-c", "user.email=t@t", "-c", "user.name=t"}, args...)...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func writeFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// A canvas turn reaches Claude Code as effort, sub-agents, focus folders,
// research off and reference images; the conversation keeps what it asked.
func TestCanvasTurnArgs(t *testing.T) {
	record := fakeAgent(t, "claude", claudeTurn)
	a := NewApp(nil, "", t.TempDir())
	repo := t.TempDir()
	gitRun(t, repo, "init", "-q", "-b", "main")
	writeFile(t, filepath.Join(repo, "src", "a.go"), "package a\n")
	gitRun(t, repo, "add", "-A")
	gitRun(t, repo, "commit", "-qm", "first")
	outside := t.TempDir()
	img := filepath.Join(repo, ".pantheon", "references", "shot.png")
	writeFile(t, img, "png")

	ct := &CanvasTurn{Effort: "xhigh", Research: false, Focus: []string{"src", outside}, Images: []string{img},
		Subagents: []Subagent{
			{Name: "explorer", Description: "Explores the code", Prompt: "You explore."},
			{Name: "builder", Description: "Makes the change", Write: true, Model: "sonnet"},
		}}
	res := a.send(SendArgs{Agent: "claude", Cwd: repo, Prompt: "fix it", Auto: true, Origin: "canvas", Canvas: ct})
	if res["success"] != true {
		t.Fatalf("send: %v", res)
	}
	id := res["conversation_id"].(string)
	c := waitIdle(t, a, id)
	if canvas, _ := c["canvas"].(map[string]any); canvas == nil || canvas["effort"] != "xhigh" || c["origin"] != "canvas" {
		t.Fatalf("conversation canvas: %v", c)
	}
	calls, _ := os.ReadFile(record)
	args := string(calls)
	for _, want := range []string{
		"--effort xhigh",
		`--agents {"builder":{"description":"Makes the change","model":"sonnet","prompt":"Makes the change"},` +
			`"explorer":{"description":"Explores the code","prompt":"You explore.","tools":["Read","Grep","Glob"]}}`,
		"--add-dir " + outside,
		"--disallowedTools WebSearch,WebFetch",
		"--append-system-prompt",
		"- " + filepath.Join(repo, "src"),
		"- explorer (read-only): Explores the code",
		"- builder (can edit files): Makes the change",
		"Web research is off",
		"fix it\n\nReference images (read each with the Read tool):\n- " + img,
	} {
		if !strings.Contains(args, want) {
			t.Errorf("missing %q in:\n%s", want, args)
		}
	}
	if strings.Count(args, "--add-dir") != 1 {
		t.Errorf("a folder inside the workspace needs no --add-dir:\n%s", args)
	}
	user := a.conversation(id, 0)["events"].([]codingagents.Event)[0]
	if user.Text != "fix it" || !slices.Equal(user.Images, []string{img}) || user.Branch != "main" {
		t.Errorf("user event %+v", user)
	}

	// Research on: read-only roles may use the web, nothing is denied; the
	// follow-up resumes the session.
	ct2 := &CanvasTurn{Research: true, Subagents: []Subagent{{Name: "explorer", Description: "Explores"}}}
	if r := a.send(SendArgs{ConversationID: id, Prompt: "again", Canvas: ct2}); r["success"] != true {
		t.Fatal(r)
	}
	waitIdle(t, a, id)
	calls, _ = os.ReadFile(record)
	second := string(calls)[len(args):]
	if !strings.Contains(second, "--resume sess-1") || strings.Contains(second, "--disallowedTools") ||
		!strings.Contains(second, `"tools":["Read","Grep","Glob","WebSearch","WebFetch"]`) || strings.Contains(second, "--effort") {
		t.Errorf("second turn:\n%s", second)
	}
}

func TestCanvasTurnRefusals(t *testing.T) {
	fakeAgent(t, "claude", claudeTurn)
	a := NewApp(nil, "", t.TempDir())
	cwd := t.TempDir()
	for name, ct := range map[string]*CanvasTurn{
		"effort":     {Effort: "ultra"},
		"name":       {Subagents: []Subagent{{Name: "two words"}}},
		"duplicate":  {Subagents: []Subagent{{Name: "a"}, {Name: "a"}}},
		"focus":      {Focus: []string{"missing"}},
		"image":      {Images: []string{"relative.png"}},
		"no image":   {Images: []string{filepath.Join(cwd, "none.png")}},
		"too many":   {Subagents: make([]Subagent, maxSubagents+1)},
		"empty name": {Subagents: []Subagent{{Description: "x"}}},
	} {
		if r := a.send(SendArgs{Agent: "claude", Cwd: cwd, Prompt: "hi", Canvas: ct}); r["success"] != false {
			t.Errorf("%s: %v", name, r)
		}
	}
	if l := a.list("", "")["conversations"].([]*Conversation); len(l) != 0 {
		t.Errorf("a refused send leaves no conversation: %+v", l)
	}
	// Other agents ignore canvas options.
	fakeAgent(t, "codex", `echo '{"type":"turn.completed","usage":{}}'`)
	if r := a.send(SendArgs{Agent: "codex", Cwd: cwd, Prompt: "hi", Canvas: &CanvasTurn{Effort: "ultra"}}); r["success"] != true {
		t.Errorf("codex: %v", r)
	}
}

// A real recorded stream with three parallel sub-agents goes through the
// service unchanged in attribution and replays the same way.
func TestRecordedSubagentRunReplays(t *testing.T) {
	fixture, err := filepath.Abs("../../os/codingagents/testdata/claude-subagents-parallel.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	fakeAgent(t, "claude", "cat "+fixture+"\n")
	a := NewApp(nil, "", t.TempDir())
	id := a.send(SendArgs{Agent: "claude", Cwd: t.TempDir(), Prompt: "go", Origin: "canvas",
		Canvas: &CanvasTurn{Research: true, Subagents: []Subagent{{Name: "explorer"}, {Name: "reviewer"}, {Name: "tester"}}}})["conversation_id"].(string)
	if c := waitIdle(t, a, id); c["status"] != "idle" {
		t.Fatalf("conversation %v", c)
	}
	all := a.conversation(id, 0)["events"].([]codingagents.Event)
	launches, inside, dones := 0, 0, 0
	for _, e := range all {
		if e.Subagent != "" && e.Name == "Agent" {
			launches++
		}
		if e.Parent != "" {
			inside++
		}
		if e.Type == "done" {
			dones++
		}
	}
	if launches != 3 || inside < 10 || dones != 1 || all[len(all)-1].Type != "done" || all[len(all)-1].DurationMs == 0 {
		t.Errorf("launches %d, sub-agent events %d, done events %d, last %+v", launches, inside, dones, all[len(all)-1])
	}
	// Replay from any point gives the same tail.
	for _, after := range []int64{0, 5, int64(len(all) / 2), all[len(all)-2].Seq} {
		tail := a.conversation(id, after)["events"].([]codingagents.Event)
		want := all[slices.IndexFunc(all, func(e codingagents.Event) bool { return e.Seq > after }):]
		if len(tail) != len(want) || tail[0].Seq != want[0].Seq {
			t.Errorf("after %d: %d events, want %d", after, len(tail), len(want))
		}
	}
}

// Stopping a turn ends the sub-agents still running, as stopped.
func TestStopEndsRunningSubagents(t *testing.T) {
	launch := `{"type":"assistant","message":{"content":[{"type":"tool_use","id":"L1","name":"Agent","input":{"subagent_type":"explorer","description":"Look around","prompt":"look"}}]}}`
	fakeAgent(t, "claude", "echo '"+launch+"'\nsleep 30\n")
	a := NewApp(nil, "", t.TempDir())
	id := a.send(SendArgs{Agent: "claude", Prompt: "work"})["conversation_id"].(string)
	time.Sleep(300 * time.Millisecond)
	a.stop(id)
	ev := a.conversation(id, 0)["events"].([]codingagents.Event)
	if n := len(ev); n < 3 || ev[n-2].ID != "L1" || ev[n-2].Status != "error" || !ev[n-2].Stopped || ev[n-1].Type != "done" || !ev[n-1].Stopped {
		t.Fatalf("events: %+v", ev)
	}
}

// A conversation running when the service stops is marked interrupted, its
// log ends with that, and the next message resumes the session.
func TestServiceStopInterruptsAndResumes(t *testing.T) {
	fakeAgent(t, "claude", `echo '{"type":"system","subtype":"init","session_id":"sess-9"}'`+"\nsleep 30\n")
	root := t.TempDir()
	a := NewApp(nil, "", root)
	id := a.send(SendArgs{Agent: "claude", Prompt: "long job"})["conversation_id"].(string)
	time.Sleep(300 * time.Millisecond)
	a.Close()
	b := NewApp(nil, "", root)
	c := b.snapshot(id)
	if c["status"] != "error" || c["interrupted"] != true || c["session_id"] != "sess-9" {
		t.Fatalf("after a graceful stop: %v", c)
	}
	ev := b.conversation(id, 0)["events"].([]codingagents.Event)
	if last := ev[len(ev)-1]; last.Type != "done" || !strings.HasPrefix(last.Error, "Interrupted") {
		t.Fatalf("last event %+v", last)
	}
	fakeAgent(t, "claude", claudeTurn)
	if r := b.send(SendArgs{ConversationID: id, Prompt: "go on"}); r["success"] != true {
		t.Fatal(r)
	}
	if c := waitIdle(t, b, id); c["interrupted"] == true || c["status"] != "idle" {
		t.Fatalf("after resuming: %v", c)
	}
	b.Close()
}

// After a crash the index is behind the log: the service numbers on from
// the log, so a reader replaying with after never sees a seq twice.
func TestCrashRecoveryKeepsSeqs(t *testing.T) {
	root := t.TempDir()
	conv := []*Conversation{{ID: "c1", Agent: "claude", Cwd: root, Title: "t", Session: "s", Status: "running", Seq: 2}}
	b, _ := json.Marshal(conv)
	writeFile(t, filepath.Join(root, "conversations.json"), string(b))
	var log strings.Builder
	for i := 1; i <= 5; i++ {
		e, _ := json.Marshal(codingagents.Event{Seq: int64(i), Type: "text", Text: strconv.Itoa(i)})
		log.Write(append(e, '\n'))
	}
	writeFile(t, filepath.Join(root, "c1.jsonl"), log.String())

	a := NewApp(nil, "", root)
	c := a.snapshot("c1")
	if c["status"] != "error" || c["interrupted"] != true || c["seq"] != float64(6) {
		t.Fatalf("recovered: %v", c)
	}
	tail := a.conversation("c1", 3)["events"].([]codingagents.Event)
	if len(tail) != 3 || tail[0].Seq != 4 || tail[2].Seq != 6 || tail[2].Type != "done" {
		t.Fatalf("replay after 3: %+v", tail)
	}
	// A second start does not add another.
	if NewApp(nil, "", root).snapshot("c1")["seq"] != float64(6) {
		t.Error("the interruption was recorded twice")
	}
}

func TestSkills(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	writeFile(t, filepath.Join(home, ".claude", "skills", "pdf", "SKILL.md"),
		"---\nname: pdf\ndescription: \"Work with PDF files\"\n---\n# PDF\n")
	writeFile(t, filepath.Join(home, ".claude", "skills", "deck", "SKILL.md"),
		"---\nname: marp-deck\ndescription: >-\n  Build a deck\n  as Markdown.\nlicense: MIT\n---\nbody\n")
	writeFile(t, filepath.Join(home, ".claude", "skills", "nofront", "SKILL.md"), "# Just text\n")
	if err := os.MkdirAll(filepath.Join(home, ".claude", "skills", "empty"), 0o755); err != nil {
		t.Fatal(err)
	}
	proj := t.TempDir()
	writeFile(t, filepath.Join(proj, ".claude", "skills", "release", "SKILL.md"),
		"---\nname: release\ndescription: |\n  Ship it.\n  Carefully.\n---\n")

	got := skills(proj)["skills"].([]Skill)
	want := []Skill{
		{Name: "marp-deck", Description: "Build a deck as Markdown.", Source: "user"},
		{Name: "nofront", Source: "user"},
		{Name: "pdf", Description: "Work with PDF files", Source: "user"},
		{Name: "release", Description: "Ship it.\nCarefully.", Source: "project"},
	}
	if len(got) != len(want) {
		t.Fatalf("skills: %+v", got)
	}
	for i := range want {
		if got[i].Name != want[i].Name || got[i].Description != want[i].Description || got[i].Source != want[i].Source ||
			filepath.Base(got[i].Path) != "SKILL.md" {
			t.Errorf("skill %d: %+v, want %+v", i, got[i], want[i])
		}
	}
	// A new skill shows on the next call.
	writeFile(t, filepath.Join(home, ".claude", "skills", "fresh", "SKILL.md"), "---\nname: fresh\ndescription: New\n---\n")
	if n := len(skills(proj)["skills"].([]Skill)); n != 5 {
		t.Errorf("after adding one: %d skills", n)
	}
}

func TestRepoInfo(t *testing.T) {
	repo := t.TempDir()
	gitRun(t, repo, "init", "-q", "-b", "main")
	writeFile(t, filepath.Join(repo, "a.txt"), "a")
	gitRun(t, repo, "add", "-A")
	gitRun(t, repo, "commit", "-qm", "First commit")
	gitRun(t, repo, "branch", "feature/x")
	writeFile(t, filepath.Join(repo, "b.txt"), "b")
	gitRun(t, repo, "add", "-A")
	gitRun(t, repo, "commit", "-qm", "Add b: with a colon")

	info := repoInfo(repo)
	commit := info["commit"].(Commit)
	if info["git"] != true || info["branch"] != "main" ||
		commit.Hash != gitRun(t, repo, "log", "-1", "--format=%H") ||
		commit.Short != gitRun(t, repo, "log", "-1", "--format=%h") ||
		commit.Subject != gitRun(t, repo, "log", "-1", "--format=%s") {
		t.Errorf("repo info: %+v", info)
	}
	if got, want := info["branches"].([]string), strings.Split(gitRun(t, repo, "branch", "--format=%(refname:short)"), "\n"); !slices.Equal(got, want) {
		t.Errorf("branches %v, git branch %v", got, want)
	}
	if plain := repoInfo(t.TempDir()); plain["success"] != true || plain["git"] != false {
		t.Errorf("a plain folder: %v", plain)
	}
	if missing := repoInfo(filepath.Join(repo, "nope")); missing["success"] != false {
		t.Errorf("a missing folder: %v", missing)
	}
	empty := t.TempDir()
	gitRun(t, empty, "init", "-q")
	if e := repoInfo(empty); e["git"] != true || e["commit"] != nil {
		t.Errorf("a repository without commits: %v", e)
	}
}

// findCount is what find reports for a folder's files, without .git,
// node_modules and hidden folders (hidden files count).
func findCount(t *testing.T, dir string) int {
	t.Helper()
	out, err := exec.Command("sh", "-c", `find "$1" -type d \( -name node_modules -o -name '.*' \) -prune -o -type f -print | wc -l`, "sh", dir).Output()
	if err != nil {
		t.Fatal(err)
	}
	n, _ := strconv.Atoi(strings.TrimSpace(string(out)))
	return n
}

func TestFolderTree(t *testing.T) {
	repo := t.TempDir()
	for _, f := range []string{"src/a.go", "src/b.go", "src/deep/c.go", "src/deep/deeper/d.go", "src/.cache/x",
		"src/node_modules/m/i.js", "docs/r.md", "web/node_modules/p/q.js", "web/.env", "README.md",
		".git/HEAD", ".github/w.yml", "node_modules/z.js"} {
		writeFile(t, filepath.Join(repo, f), "x")
	}
	if err := os.MkdirAll(filepath.Join(repo, "empty"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(repo, "src", "a.go"), filepath.Join(repo, "docs", "link.go")); err != nil {
		t.Fatal(err)
	}

	res := folderTree(repo, "")
	folders := res["folders"].([]FolderCount)
	var names []string
	for _, f := range folders {
		names = append(names, f.Name)
		if want := findCount(t, f.Path); f.Files != want {
			t.Errorf("%s: %d files, find says %d", f.Name, f.Files, want)
		}
	}
	if !slices.Equal(names, []string{"docs", "empty", "src", "web"}) || res["approx"] != false {
		t.Errorf("folders %v (%v)", names, res)
	}
	if src := folders[2]; src.Files != 4 || src.Subfolders != 1 {
		t.Errorf("src: %+v", src)
	}
	deep := folderTree(repo, "src/deep")["folders"].([]FolderCount)
	if len(deep) != 1 || deep[0].Name != "deeper" || deep[0].Files != 1 {
		t.Errorf("src/deep: %+v", deep)
	}
	if abs := folderTree(repo, filepath.Join(repo, "src")); abs["success"] != true {
		t.Errorf("an absolute dir inside the repo: %v", abs)
	}
	for _, bad := range []string{"..", "../..", "src/../../x", "/etc", filepath.Dir(repo)} {
		if r := folderTree(repo, bad); r["success"] != false {
			t.Errorf("%s: %v", bad, r)
		}
	}
}

func TestFolderTreeIsQuick(t *testing.T) {
	repo := t.TempDir()
	for d := 0; d < 20; d++ {
		dir := filepath.Join(repo, "pkg"+strconv.Itoa(d), "sub")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		for f := 0; f < 500; f++ {
			if err := os.WriteFile(filepath.Join(dir, strconv.Itoa(f)), nil, 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	start := time.Now()
	res := folderTree(repo, "")
	took := time.Since(start)
	total := 0
	for _, f := range res["folders"].([]FolderCount) {
		total += f.Files
	}
	if total != 10_000 || took > time.Second {
		t.Errorf("%d files in %v", total, took)
	}
}

func pngBytes(t *testing.T, w, h int) []byte {
	t.Helper()
	img := image.NewNRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			img.Set(x, y, color.NRGBA{uint8(x), uint8(y), uint8(x ^ y), 255})
		}
	}
	var b bytes.Buffer
	if err := png.Encode(&b, img); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

// upload sends data in 48 KB chunks, as the desktop does.
func upload(t *testing.T, cwd, id, name string, data []byte) map[string]any {
	t.Helper()
	const chunk = 48 << 10
	var res map[string]any
	for off := 0; ; off += chunk {
		end := min(off+chunk, len(data))
		res = referencePut(ReferenceChunk{Cwd: cwd, UploadID: id, Name: name, Offset: int64(off),
			Data: base64.StdEncoding.EncodeToString(data[off:end]), Final: end == len(data)})
		if res["success"] != true || end == len(data) {
			return res
		}
	}
}

func TestReferenceUploadListAndThumb(t *testing.T) {
	cwd := t.TempDir()
	data := pngBytes(t, 900, 600)
	res := upload(t, cwd, "u1", "Screen Shot (1).PNG", data)
	if res["success"] != true {
		t.Fatalf("upload: %v", res)
	}
	path := res["path"].(string)
	if path != filepath.Join(cwd, ".pantheon", "references", "Screen-Shot-1.png") {
		t.Errorf("path %s", path)
	}
	if got, _ := os.ReadFile(path); !bytes.Equal(got, data) {
		t.Error("the stored file differs from the upload")
	}
	// The same name again gets a number.
	if again := upload(t, cwd, "u2", "Screen Shot (1).PNG", data); filepath.Base(again["path"].(string)) != "Screen-Shot-1-2.png" {
		t.Errorf("second upload: %v", again)
	}
	list := referenceList(cwd)["images"].([]Reference)
	if len(list) != 2 || list[0].Size != int64(len(data)) {
		t.Errorf("list: %+v", list)
	}
	if empty := referenceList(t.TempDir()); empty["success"] != true || len(empty["images"].([]Reference)) != 0 {
		t.Errorf("no references yet: %v", empty)
	}

	thumb := referenceThumb(cwd, path)
	raw, _ := base64.StdEncoding.DecodeString(thumb["data"].(string))
	img, err := jpeg.Decode(bytes.NewReader(raw))
	if thumb["mime"] != "image/jpeg" || err != nil || len(raw) > thumbMax ||
		img.Bounds().Dx() != thumbSide || img.Bounds().Dy() != 170 {
		t.Fatalf("thumb: %v %v %d bytes %v", thumb["mime"], err, len(raw), img.Bounds())
	}

	// A GIF's first frame.
	var g bytes.Buffer
	pal := image.NewPaletted(image.Rect(0, 0, 40, 30), []color.Color{color.Black, color.White})
	if err := gif.Encode(&g, pal, nil); err != nil {
		t.Fatal(err)
	}
	gp := upload(t, cwd, "g", "anim.gif", g.Bytes())["path"].(string)
	if th := referenceThumb(cwd, gp); th["mime"] != "image/jpeg" {
		t.Errorf("gif thumb: %v", th)
	}
	// What the standard library cannot decode is sent as is when small.
	webp := append([]byte("RIFF\x10\x00\x00\x00WEBPVP8 "), make([]byte, 20)...)
	wp := upload(t, cwd, "w", "pic.webp", webp)
	if wp["success"] != true {
		t.Fatalf("webp upload: %v", wp)
	}
	if th := referenceThumb(cwd, wp["path"].(string)); th["mime"] != "image/webp" {
		t.Errorf("webp thumb: %v", th)
	}
}

func TestReferenceLimits(t *testing.T) {
	cwd := t.TempDir()
	png := pngBytes(t, 4, 4)
	refs := filepath.Join(cwd, ".pantheon", "references")
	for name, c := range map[string]ReferenceChunk{
		"svg":        {Name: "x.svg", Data: base64.StdEncoding.EncodeToString([]byte("<svg/>")), Final: true},
		"exe":        {Name: "x.exe"},
		"no ext":     {Name: "png"},
		"upload id":  {UploadID: "../x", Name: "a.png"},
		"base64":     {Name: "a.png", Data: "%%%"},
		"not a png":  {Name: "a.png", Data: base64.StdEncoding.EncodeToString([]byte("hello, not an image")), Final: true},
		"jpg as png": {Name: "a.jpg", Data: base64.StdEncoding.EncodeToString(png), Final: true},
		"relative":   {Cwd: "rel", Name: "a.png"},
	} {
		if c.Cwd == "" {
			c.Cwd = cwd
		}
		if c.UploadID == "" {
			c.UploadID = "id"
		}
		if r := referencePut(c); r["success"] != false {
			t.Errorf("%s: %v", name, r)
		}
	}
	if left, _ := filepath.Glob(filepath.Join(refs, "*")); len(left) != 0 {
		t.Errorf("refused uploads left files: %v", left)
	}

	// Names cannot leave the folder.
	for _, name := range []string{"../../evil.png", `..\..\evil.png`, "/etc/evil.png", ".hidden.png"} {
		r := upload(t, cwd, "t", name, png)
		if r["success"] != true || filepath.Dir(r["path"].(string)) != refs || strings.HasPrefix(filepath.Base(r["path"].(string)), ".") {
			t.Errorf("%s: %v", name, r)
		}
	}
	// Chunks come in order.
	first := base64.StdEncoding.EncodeToString(png[:10])
	referencePut(ReferenceChunk{Cwd: cwd, UploadID: "o", Name: "o.png", Data: first})
	if r := referencePut(ReferenceChunk{Cwd: cwd, UploadID: "o", Name: "o.png", Offset: 50, Data: first}); r["success"] != false {
		t.Errorf("a gap: %v", r)
	}
	// 20 MB at most.
	big := make([]byte, referenceMax+1)
	copy(big, png)
	if r := referencePut(ReferenceChunk{Cwd: cwd, UploadID: "big", Name: "big.png", Data: base64.StdEncoding.EncodeToString(big), Final: true}); r["success"] != false || !strings.Contains(r["error"].(string), "20 MB") {
		t.Errorf("too big: %v", r)
	}
	// A references folder that leads out of the workspace is refused.
	other := t.TempDir()
	linked := t.TempDir()
	if err := os.Symlink(other, filepath.Join(linked, ".pantheon")); err != nil {
		t.Fatal(err)
	}
	if r := upload(t, linked, "l", "a.png", png); r["success"] != false {
		t.Errorf("linked out: %v", r)
	}

	// Thumbnails only for files in a references folder.
	elsewhere := filepath.Join(cwd, "a.png")
	writeFile(t, elsewhere, string(png))
	if err := os.Symlink(elsewhere, filepath.Join(refs, "link.png")); err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{elsewhere, "/etc/passwd", filepath.Join(refs, "link.png"), filepath.Join(refs, "..", "..", "a.png"), "rel/.pantheon/references/a.png"} {
		if r := referenceThumb(cwd, p); r["success"] != false {
			t.Errorf("thumb of %s: %v", p, r)
		}
	}
}

// The manifest and the handlers agree, and agent_send's canvas parameters
// arrive as the bus decodes them.
func TestCanvasTools(t *testing.T) {
	record := fakeAgent(t, "claude", claudeTurn)
	a := NewApp(nil, "", t.TempDir())
	tools, err := Tools(a)
	if err != nil {
		t.Fatal(err)
	}
	byName := map[string]func(map[string]any) map[string]any{}
	for _, tool := range tools {
		h := tool.Handler
		byName[tool.Name] = func(p map[string]any) map[string]any {
			out, _ := h(context.Background(), p)
			return out.(map[string]any)
		}
	}
	for _, name := range []string{"agents_skills", "agents_repo_info", "agents_folder_tree", "agents_reference_put",
		"agents_reference_list", "agents_reference_thumb"} {
		if byName[name] == nil {
			t.Errorf("no tool %s", name)
		}
	}
	var params map[string]any
	_ = json.Unmarshal([]byte(`{"agent":"claude","cwd":"`+t.TempDir()+`","prompt":"hi","origin":"canvas","effort":"max",
		"research":false,"subagents":[{"name":"tester","description":"Runs tests","prompt":"","write":true}]}`), &params)
	res := byName["agent_send"](params)
	if res["success"] != true {
		t.Fatal(res)
	}
	waitIdle(t, a, res["conversation_id"].(string))
	calls, _ := os.ReadFile(record)
	if !strings.Contains(string(calls), "--effort max") || !strings.Contains(string(calls), `--agents {"tester":`) ||
		!strings.Contains(string(calls), "--disallowedTools WebSearch,WebFetch") {
		t.Errorf("args: %s", calls)
	}
	params["subagents"] = "not a list"
	if r := byName["agent_send"](params); r["success"] != false {
		t.Errorf("a malformed subagents: %v", r)
	}
	// A plain send stays a plain turn.
	_ = json.Unmarshal([]byte(`{"agent":"claude","prompt":"plain","research":true}`), &params)
	delete(params, "subagents")
	delete(params, "effort")
	delete(params, "origin")
	r := byName["agent_send"](params)
	waitIdle(t, a, r["conversation_id"].(string))
	if c := a.snapshot(r["conversation_id"].(string)); c["canvas"] != nil {
		t.Errorf("a plain send recorded canvas options: %v", c)
	}
}

// pngHeader is a PNG that is only its header: enough for DecodeConfig to
// read its size, nothing to decode.
func pngHeader(w, h uint32, depth, colorType byte) []byte {
	ihdr := make([]byte, 13)
	binary.BigEndian.PutUint32(ihdr[0:], w)
	binary.BigEndian.PutUint32(ihdr[4:], h)
	ihdr[8], ihdr[9] = depth, colorType
	chunk := append([]byte("IHDR"), ihdr...)
	out := append([]byte("\x89PNG\r\n\x1a\n"), 0, 0, 0, 13)
	out = append(out, chunk...)
	return binary.BigEndian.AppendUint32(out, crc32.ChecksumIEEE(chunk))
}

// An upload never writes through a link: the part file has a name of the
// service's own, and a link put in its place stops the upload.
func TestReferenceUploadIgnoresPlantedLinks(t *testing.T) {
	cwd := t.TempDir()
	victim := filepath.Join(t.TempDir(), "victim.txt")
	writeFile(t, victim, "keep me")
	refs := filepath.Join(cwd, ".pantheon", "references")
	writeFile(t, filepath.Join(refs, ".keep"), "")
	// A link named after the client's upload id is not used.
	if err := os.Symlink(victim, filepath.Join(refs, ".upload-u.part")); err != nil {
		t.Fatal(err)
	}
	data := pngBytes(t, 300, 300)
	if r := upload(t, cwd, "u", "a.png", data); r["success"] != true {
		t.Fatalf("upload: %v", r)
	}
	// A part swapped for a link mid-upload is refused.
	first := base64.StdEncoding.EncodeToString(data[:100])
	if r := referencePut(ReferenceChunk{Cwd: cwd, UploadID: "s", Name: "s.png", Data: first}); r["success"] != true {
		t.Fatal(r)
	}
	parts, _ := filepath.Glob(filepath.Join(refs, ".upload-*.part"))
	for _, p := range parts {
		if fi, _ := os.Lstat(p); fi.Mode().IsRegular() {
			_ = os.Remove(p)
			if err := os.Symlink(victim, p); err != nil {
				t.Fatal(err)
			}
		}
	}
	if r := referencePut(ReferenceChunk{Cwd: cwd, UploadID: "s", Name: "s.png", Offset: 100, Data: first}); r["success"] != false {
		t.Errorf("a swapped part: %v", r)
	}
	if b, _ := os.ReadFile(victim); string(b) != "keep me" {
		t.Errorf("the link's target was written: %q", b)
	}
	// An id that was never started cannot continue.
	if r := referencePut(ReferenceChunk{Cwd: cwd, UploadID: "never", Name: "n.png", Offset: 10, Data: first}); r["success"] != false {
		t.Errorf("an unknown upload: %v", r)
	}
}

// Images too large to decode safely are refused at upload, and a preview
// of one put there otherwise is refused without decoding it.
func TestReferencePixelLimits(t *testing.T) {
	cwd := t.TempDir()
	for name, b := range map[string][]byte{
		"10000x10000": pngHeader(10000, 10000, 8, 6),
		"16-bit":      pngHeader(5000, 5000, 16, 6), // 25 M pixels × 8 bytes
	} {
		if r := upload(t, cwd, "x", "big.png", b); r["success"] != false || !strings.Contains(r["error"].(string), "too large") {
			t.Errorf("%s: %v", name, r)
		}
	}
	refs := filepath.Join(cwd, ".pantheon", "references")
	if left, _ := filepath.Glob(filepath.Join(refs, "*")); len(left) != 0 {
		t.Errorf("refused images were kept: %v", left)
	}
	planted := filepath.Join(refs, "planted.png")
	writeFile(t, planted, string(pngHeader(10000, 10000, 8, 6)))
	if r := referenceThumb(cwd, planted); r["success"] != false || !strings.Contains(r["error"].(string), "too large") {
		t.Errorf("thumb of a huge image: %v", r)
	}
	if err := fitsBudget(image.Config{Width: 5000, Height: 5000, ColorModel: color.RGBAModel}); err != nil {
		t.Errorf("25 M pixels of 8-bit colour fit: %v", err)
	}
}

// A thumbnail is only of the named workspace's references.
func TestReferenceThumbNeedsItsWorkspace(t *testing.T) {
	a, b := t.TempDir(), t.TempDir()
	path := upload(t, a, "u", "a.png", pngBytes(t, 20, 20))["path"].(string)
	if r := referenceThumb(a, path); r["success"] != true {
		t.Fatalf("own workspace: %v", r)
	}
	upload(t, b, "u", "b.png", pngBytes(t, 20, 20))
	for _, cwd := range []string{b, "", "relative", filepath.Dir(a)} {
		if r := referenceThumb(cwd, path); r["success"] != false {
			t.Errorf("cwd %q: %v", cwd, r)
		}
	}
}

// The references folder is checked before anything is made in it: a
// .pantheon linked out of the workspace gets nothing new.
func TestReferenceDirCheckedBeforeMaking(t *testing.T) {
	cwd, outside := t.TempDir(), t.TempDir()
	if err := os.Symlink(outside, filepath.Join(cwd, ".pantheon")); err != nil {
		t.Fatal(err)
	}
	if r := upload(t, cwd, "u", "a.png", pngBytes(t, 4, 4)); r["success"] != false {
		t.Errorf("upload: %v", r)
	}
	if _, err := os.Stat(filepath.Join(outside, "references")); !os.IsNotExist(err) {
		t.Errorf("a folder was made outside the workspace: %v", err)
	}
	// A link that stays inside is fine.
	inside := t.TempDir()
	if err := os.MkdirAll(filepath.Join(inside, "store"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(inside, "store"), filepath.Join(inside, ".pantheon")); err != nil {
		t.Fatal(err)
	}
	if r := upload(t, inside, "u", "a.png", pngBytes(t, 4, 4)); r["success"] != true {
		t.Errorf("a link inside: %v", r)
	}
}

// Parts left for over an hour are removed; fresh ones stay.
func TestStaleUploadPartsRemoved(t *testing.T) {
	cwd := t.TempDir()
	refs := filepath.Join(cwd, ".pantheon", "references")
	old, fresh := filepath.Join(refs, ".upload-old.part"), filepath.Join(refs, ".upload-fresh.part")
	writeFile(t, old, "x")
	writeFile(t, fresh, "x")
	past := time.Now().Add(-2 * time.Hour)
	if err := os.Chtimes(old, past, past); err != nil {
		t.Fatal(err)
	}
	upload(t, cwd, "u", "a.png", pngBytes(t, 4, 4))
	if _, err := os.Stat(old); !os.IsNotExist(err) {
		t.Error("an hour-old part stayed")
	}
	if _, err := os.Stat(fresh); err != nil {
		t.Error("a fresh part was removed")
	}
}

func TestFolderTreeRefusesLinksOut(t *testing.T) {
	repo, outside := t.TempDir(), t.TempDir()
	writeFile(t, filepath.Join(outside, "secret", "f"), "x")
	if err := os.Symlink(outside, filepath.Join(repo, "escape")); err != nil {
		t.Fatal(err)
	}
	for _, dir := range []string{"escape", filepath.Join(repo, "escape"), "escape/secret"} {
		if r := folderTree(repo, dir); r["success"] != false {
			t.Errorf("%s: %v", dir, r)
		}
	}
	// A project reached through a link still lists.
	linkedRepo := filepath.Join(t.TempDir(), "repo")
	writeFile(t, filepath.Join(repo, "src", "a"), "x")
	if err := os.Symlink(repo, linkedRepo); err != nil {
		t.Fatal(err)
	}
	if r := folderTree(linkedRepo, "src"); r["success"] != true {
		t.Errorf("a linked project: %v", r)
	}
}

// pluginHome installs two plugins in a temp Claude Code home: one enabled
// (a skill and a nested command), one installed but switched off, and one
// installed for another project.
func pluginHome(t *testing.T, home, project string) {
	t.Helper()
	claude := filepath.Join(home, ".claude")
	install := func(name string) string {
		p := filepath.Join(claude, "plugins", "cache", "market", name, "1.0.0")
		writeFile(t, filepath.Join(p, ".claude-plugin", "plugin.json"), `{"name":"`+strings.ToUpper(name[:1])+name[1:]+`"}`)
		writeFile(t, filepath.Join(p, "skills", "capture", "SKILL.md"), "---\nname: capture\ndescription: Capture notes\n---\n")
		writeFile(t, filepath.Join(p, "commands", "find.md"), "---\ndescription: Find pages\n---\n")
		writeFile(t, filepath.Join(p, "commands", "tasks", "build.md"), "---\ndescription: Build a task\n---\n")
		return p
	}
	notes, off, other := install("notes"), install("off"), install("other")
	b, _ := json.Marshal(map[string]any{"version": 2, "plugins": map[string]any{
		"notes@market": []any{map[string]any{"scope": "user", "installPath": notes}},
		"off@market":   []any{map[string]any{"scope": "user", "installPath": off}},
		"other@market": []any{map[string]any{"scope": "project", "projectPath": "/somewhere/else", "installPath": other}},
	}})
	writeFile(t, filepath.Join(claude, "plugins", "installed_plugins.json"), string(b))
	writeFile(t, filepath.Join(claude, "settings.json"),
		`{"enabledPlugins":{"notes@market":true,"off@market":true,"other@market":true}}`)
	// The project switches one off.
	writeFile(t, filepath.Join(project, ".claude", "settings.json"), `{"enabledPlugins":{"off@market":false}}`)
}

func TestPluginSkills(t *testing.T) {
	home, proj := t.TempDir(), t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	pluginHome(t, home, proj)
	var got []string
	for _, s := range skills(proj)["skills"].([]Skill) {
		if s.Source == "plugin" {
			got = append(got, s.Plugin+" "+s.Name+" "+s.Description)
		}
	}
	want := []string{"Notes Notes:capture Capture notes", "Notes Notes:find Find pages", "Notes Notes:tasks:build Build a task"}
	if !slices.Equal(got, want) {
		t.Errorf("plugin skills:\n got %q\nwant %q", got, want)
	}
	// Without the project, its settings do not apply.
	if n := len(skills("")["skills"].([]Skill)); n != 6 {
		t.Errorf("without a project: %d skills", n)
	}
}

// A prompt that starts with skills shows them used: Claude Code expands
// them itself, without a tool call.
func TestSlashSkillsShowAsUsed(t *testing.T) {
	fakeAgent(t, "claude", claudeTurn)
	home := os.Getenv("HOME")
	proj := t.TempDir()
	writeFile(t, filepath.Join(home, ".claude", "skills", "pdf", "SKILL.md"), "---\nname: pdf\ndescription: PDFs\n---\n")
	pluginHome(t, home, proj)
	a := NewApp(nil, "", t.TempDir())
	id := a.send(SendArgs{Agent: "claude", Cwd: proj, Prompt: "/pdf /Notes:find /compact /pdf read /nope this"})["conversation_id"].(string)
	waitIdle(t, a, id)
	ev := a.conversation(id, 0)["events"].([]codingagents.Event)
	if eventTypes(ev[:4]) != "user tool tool session" || ev[1].Name != "Skill" || ev[1].Input != "pdf" ||
		ev[2].Input != "Notes:find" || ev[1].Status != "done" || ev[1].ID == ev[2].ID {
		t.Errorf("events: %+v", ev[:4])
	}
	// Only at the start, and only Claude Code's.
	id2 := a.send(SendArgs{Agent: "claude", Cwd: proj, Prompt: "please /pdf"})["conversation_id"].(string)
	waitIdle(t, a, id2)
	if ev := a.conversation(id2, 0)["events"].([]codingagents.Event); ev[1].Type != "session" {
		t.Errorf("a skill later in the prompt: %+v", ev[1])
	}
}

// A remote's credentials never leave the node.
func TestRemoteWithoutCredentials(t *testing.T) {
	for in, want := range map[string]string{
		"https://user:tok@github.com/o/r.git": "https://github.com/o/r.git",
		"https://github.com/o/r.git":          "https://github.com/o/r.git",
		"git@github.com:o/r.git":              "git@github.com:o/r.git",
	} {
		if got := withoutCredentials(in); got != want {
			t.Errorf("%s: %s, want %s", in, got, want)
		}
	}
}

// Each turn of a resumed session states its own settings, so switching
// research back on or dropping the sub-agents is not lost to an earlier turn.
func TestCanvasTurnStatesItsSettings(t *testing.T) {
	cwd := t.TempDir()
	on, err := (&CanvasTurn{Research: true}).prepare(cwd)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"Web research is on for this turn", "No sub-agents this turn", "replace any from earlier turns"} {
		if !strings.Contains(on.system, want) {
			t.Errorf("research on, no roster: %q missing from %q", want, on.system)
		}
	}
	off, err := (&CanvasTurn{Research: false, Subagents: []Subagent{{Name: "explorer", Description: "Explores"}}}).prepare(cwd)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(off.system, "Web research is off for this turn") || strings.Contains(off.system, "No sub-agents") {
		t.Errorf("research off, a roster: %q", off.system)
	}
}
