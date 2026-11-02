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

// piHead is how much of a Pi session file is read for its first prompt: the
// system message before it carries every tool's declaration.
const piHead = 2 << 20

// piSessionsDir is where Pi keeps its sessions, as Pi itself resolves it:
// PI_CODING_AGENT_SESSION_DIR, else the sessionDir of its settings (a
// relative one depends on the folder Pi starts in, and is not followed), else
// sessions/ in its folder (PI_CODING_AGENT_DIR, else ~/.pi/agent).
func piSessionsDir() string {
	if dir := os.Getenv("PI_CODING_AGENT_SESSION_DIR"); dir != "" {
		return expandTilde(dir)
	}
	agentDir := filepath.Join(homeDir(), ".pi", "agent")
	if dir := os.Getenv("PI_CODING_AGENT_DIR"); dir != "" {
		agentDir = expandTilde(dir)
	}
	if b, err := os.ReadFile(filepath.Join(agentDir, "settings.json")); err == nil {
		var s struct {
			SessionDir string `json:"sessionDir"`
		}
		if json.Unmarshal(b, &s) == nil && s.SessionDir != "" {
			if dir := expandTilde(s.SessionDir); filepath.IsAbs(dir) {
				return dir
			}
		}
	}
	return filepath.Join(agentDir, "sessions")
}

func homeDir() string {
	home, _ := os.UserHomeDir()
	return home
}

func expandTilde(p string) string {
	if p == "~" {
		return homeDir()
	}
	if rest, ok := strings.CutPrefix(p, "~/"); ok {
		return filepath.Join(homeDir(), rest)
	}
	return p
}

// PiSessions lists Pi's sessions on this machine, newest first, from its own
// session files (<sessions>/--<cwd>--/<time>_<id>.jsonl, or flat in a
// session folder the user set): the first prompt, the folder, the model and
// the name the session was given. Only each file's head and tail are read.
func PiSessions(limit int) []NativeSession {
	return piSessionsIn(piSessionsDir(), limit)
}

func piSessionsIn(root string, limit int) []NativeSession {
	type file struct {
		path string
		mod  time.Time
	}
	var files []file
	add := func(dir string, e os.DirEntry) {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			return
		}
		if info, err := e.Info(); err == nil && info.Size() > 0 {
			files = append(files, file{filepath.Join(dir, e.Name()), info.ModTime()})
		}
	}
	entries, _ := os.ReadDir(root)
	for _, e := range entries {
		if !e.IsDir() {
			add(root, e)
			continue
		}
		sub, _ := os.ReadDir(filepath.Join(root, e.Name()))
		for _, f := range sub {
			add(filepath.Join(root, e.Name()), f)
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mod.After(files[j].mod) })
	var out []NativeSession
	for _, f := range files {
		if len(out) >= limit {
			break
		}
		if s, ok := readPiSession(f.path); ok {
			s.UpdatedAt = float64(f.mod.Unix())
			out = append(out, s)
		}
	}
	return out
}

func readPiSession(path string) (NativeSession, bool) {
	f, err := os.Open(path)
	if err != nil {
		return NativeSession{}, false
	}
	defer f.Close()
	s := NativeSession{Agent: "pi"}
	var name string
	scan := func(r io.Reader, head bool) {
		sc := bufio.NewScanner(r)
		sc.Buffer(make([]byte, 64*1024), 8<<20)
		for sc.Scan() {
			line := sc.Bytes()
			// The system message is the biggest line, and says nothing here.
			if bytes.Contains(line, []byte(`"role":"system"`)) {
				continue
			}
			var m map[string]any
			if json.Unmarshal(line, &m) != nil {
				continue
			}
			switch str(m["type"]) {
			case "session":
				if head && s.ID == "" {
					s.ID, s.Cwd = str(m["id"]), str(m["cwd"])
					if t, err := time.Parse(time.RFC3339Nano, str(m["timestamp"])); err == nil {
						s.CreatedAt = float64(t.Unix())
					}
				}
			case "session_info":
				name = str(m["name"]) // the latest; cleared by an empty one
			case "model_change":
				s.Model = firstNonEmpty(str(m["modelId"]), s.Model)
			case "message":
				msg := obj(m["message"])
				switch str(msg["role"]) {
				case "user":
					if head && s.Preview == "" {
						s.Preview = firstLine(piText(msg["content"]))
					}
				case "assistant":
					s.Model = firstNonEmpty(str(msg["model"]), s.Model)
				}
			}
		}
	}
	scan(io.LimitReader(f, piHead), true)
	if info, err := f.Stat(); err == nil && info.Size() > piHead {
		start := max(info.Size()-sessionTail, piHead)
		if _, err := f.Seek(start, io.SeekStart); err == nil {
			r := bufio.NewReader(f)
			_, _ = r.ReadBytes('\n') // a partial line
			scan(r, false)
		}
	}
	if s.ID == "" || s.Preview == "" {
		return s, false // not a session, or one that never started
	}
	s.Title = firstNonEmpty(firstLine(name), s.Preview)
	return s, true
}

// piText is a Pi user message's text: a string, or its text parts.
func piText(content any) string {
	switch c := content.(type) {
	case string:
		return c
	case []any:
		for _, b := range c {
			if m := obj(b); str(m["type"]) == "text" && strings.TrimSpace(str(m["text"])) != "" {
				return str(m["text"])
			}
		}
	}
	return ""
}
