package codingagents

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	sessionHead = 256 << 10 // where the first prompt is
	sessionTail = 256 << 10 // where the latest title is
)

// claudeDir is where Claude Code keeps its state for this user.
func claudeDir() string {
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		return dir
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".claude")
}

// ClaudeSessions lists Claude Code's sessions on this machine, newest first,
// from its own session files: the first prompt, the folder and branch it ran
// in, and the title Claude Code gave it. Only each file's head and tail are
// read.
func ClaudeSessions(limit int) []NativeSession {
	return claudeSessionsIn(filepath.Join(claudeDir(), "projects"), limit)
}

func claudeSessionsIn(root string, limit int) []NativeSession {
	type file struct {
		path string
		mod  time.Time
	}
	var files []file
	projects, _ := os.ReadDir(root)
	for _, p := range projects {
		if !p.IsDir() {
			continue
		}
		entries, _ := os.ReadDir(filepath.Join(root, p.Name()))
		for _, e := range entries {
			// Sub-agent transcripts live in subdirectories or as agent-*.
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") || strings.HasPrefix(e.Name(), "agent-") {
				continue
			}
			if info, err := e.Info(); err == nil && info.Size() > 0 {
				files = append(files, file{filepath.Join(root, p.Name(), e.Name()), info.ModTime()})
			}
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mod.After(files[j].mod) })
	var out []NativeSession
	for _, f := range files {
		if len(out) >= limit {
			break
		}
		if s, ok := readClaudeSession(f.path); ok {
			s.UpdatedAt = float64(f.mod.Unix())
			out = append(out, s)
		}
	}
	return out
}

func readClaudeSession(path string) (NativeSession, bool) {
	f, err := os.Open(path)
	if err != nil {
		return NativeSession{}, false
	}
	defer f.Close()
	s := NativeSession{Agent: "claude", ID: strings.TrimSuffix(filepath.Base(path), ".jsonl")}
	var aiTitle, customTitle, lastPrompt string
	scan := func(r io.Reader, head bool) {
		sc := bufio.NewScanner(r)
		sc.Buffer(make([]byte, 64*1024), 8<<20)
		for sc.Scan() {
			line := sc.Bytes()
			if !bytes.Contains(line, []byte(`"type"`)) {
				continue
			}
			var m map[string]any
			if json.Unmarshal(line, &m) != nil {
				continue
			}
			switch str(m["type"]) {
			case "ai-title":
				aiTitle = firstNonEmpty(str(m["aiTitle"]), aiTitle)
			case "custom-title":
				customTitle = firstNonEmpty(str(m["customTitle"]), str(m["title"]), customTitle)
			case "summary":
				aiTitle = firstNonEmpty(aiTitle, str(m["summary"]))
			case "last-prompt":
				lastPrompt = firstNonEmpty(str(m["lastPrompt"]), lastPrompt)
			case "user":
				if !head || s.Preview != "" || m["isSidechain"] == true {
					continue
				}
				if text := promptText(obj(m["message"])["content"]); text != "" {
					s.Preview = firstLine(text)
					s.Cwd, s.Branch = str(m["cwd"]), str(m["gitBranch"])
					if t, err := time.Parse(time.RFC3339Nano, str(m["timestamp"])); err == nil {
						s.CreatedAt = float64(t.Unix())
					}
				}
			}
		}
	}
	scan(io.LimitReader(f, sessionHead), true)
	if info, err := f.Stat(); err == nil && info.Size() > sessionHead {
		start := max(info.Size()-sessionTail, sessionHead)
		if _, err := f.Seek(start, io.SeekStart); err == nil {
			r := bufio.NewReader(f)
			_, _ = r.ReadBytes('\n') // a partial line
			scan(r, false)
		}
	}
	if s.Preview == "" && lastPrompt == "" {
		return s, false // no prompt: a session that never started
	}
	s.Title = firstNonEmpty(customTitle, aiTitle, s.Preview, firstLine(lastPrompt))
	return s, true
}

// promptText is a user message's own text: a string, or its text blocks
// (tool results are not prompts).
func promptText(content any) string {
	switch c := content.(type) {
	case string:
		if strings.HasPrefix(c, "<") { // command wrappers and system notes
			return ""
		}
		return c
	case []any:
		for _, b := range c {
			if m := obj(b); str(m["type"]) == "text" && !strings.HasPrefix(str(m["text"]), "<") {
				return str(m["text"])
			}
		}
	}
	return ""
}
