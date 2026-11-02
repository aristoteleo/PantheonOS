package codingagents

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

// opencodeSessionsQuery reads OpenCode's sessions from its own database,
// through the CLI (`opencode db <query> --format json`): its top-level
// sessions (a sub-agent's has a parent), not archived, with each one's first
// prompt and the model that last answered in it.
const opencodeSessionsQuery = `SELECT s.id, s.title, s.directory, s.time_created AS created, s.time_updated AS updated,
 (SELECT json_extract(p.data, '$.text') FROM message m JOIN part p ON p.message_id = m.id
   WHERE m.session_id = s.id AND json_extract(m.data, '$.role') = 'user'
     AND json_extract(p.data, '$.type') = 'text' AND json_extract(p.data, '$.synthetic') IS NOT 1
   ORDER BY m.time_created, m.id, p.id LIMIT 1) AS prompt,
 (SELECT json_extract(m.data, '$.providerID') || '/' || json_extract(m.data, '$.modelID') FROM message m
   WHERE m.session_id = s.id AND json_extract(m.data, '$.role') = 'assistant'
   ORDER BY m.time_created DESC, m.id DESC LIMIT 1) AS model
FROM session s WHERE s.parent_id IS NULL AND s.time_archived IS NULL
ORDER BY s.time_updated DESC LIMIT %d`

// opencodeRow is a session as OpenCode reports it: a row of the query, or of
// `opencode session list --format json` (no prompt or model there). Times are
// in milliseconds.
type opencodeRow struct {
	ID        string  `json:"id"`
	Title     string  `json:"title"`
	Directory string  `json:"directory"`
	Created   float64 `json:"created"`
	Updated   float64 `json:"updated"`
	Prompt    string  `json:"prompt"`
	Model     string  `json:"model"`
}

// OpenCodeSessions lists OpenCode's sessions, newest first. Its database
// holds every project's; `opencode session list` only the project of the
// folder it runs in, so it is the way when the database cannot be read (an
// OpenCode without `db`): run in each of dirs.
func OpenCodeSessions(ctx context.Context, path string, env []string, limit int, dirs []string) ([]NativeSession, error) {
	out, err := opencodeOutput(ctx, path, env, "", "db", fmt.Sprintf(opencodeSessionsQuery, limit), "--format", "json")
	var rows []opencodeRow
	if err == nil {
		if err = json.Unmarshal(out, &rows); err == nil {
			return opencodeSessions(rows, true), nil
		}
	}
	seen := map[string]bool{}
	errs := []error{err}
	listed := false
	for _, dir := range dirs {
		if info, statErr := os.Stat(dir); statErr != nil || !info.IsDir() {
			continue
		}
		b, listErr := opencodeOutput(ctx, path, env, dir, "session", "list", "--format", "json", "-n", fmt.Sprint(limit))
		var list []opencodeRow
		if listErr == nil {
			listErr = json.Unmarshal(b, &list)
		}
		if listErr != nil {
			errs = append(errs, listErr)
			continue
		}
		listed = true
		for _, r := range list {
			if !seen[r.ID] {
				seen[r.ID] = true
				rows = append(rows, r)
			}
		}
	}
	if !listed {
		return nil, errors.Join(errs...)
	}
	return opencodeSessions(rows, false), nil
}

// opencodePlaceholder is the title OpenCode gives a session until it names
// it.
var opencodePlaceholder = regexp.MustCompile(`^New session - \d{4}-`)

func opencodeSessions(rows []opencodeRow, withPrompts bool) []NativeSession {
	out := make([]NativeSession, 0, len(rows))
	for _, r := range rows {
		preview := firstLine(r.Prompt)
		// A session without a prompt never started.
		if r.ID == "" || withPrompts && preview == "" {
			continue
		}
		title := r.Title
		if opencodePlaceholder.MatchString(title) {
			title = firstNonEmpty(preview, title)
		}
		out = append(out, NativeSession{Agent: "opencode", ID: r.ID, Title: firstNonEmpty(title, preview), Preview: preview,
			Cwd: r.Directory, Model: r.Model, CreatedAt: r.Created / 1000, UpdatedAt: r.Updated / 1000})
	}
	return out
}

var ansiEscape = regexp.MustCompile(`\x1b\[[0-9;]*m`)

// opencodeOutput runs one OpenCode command in dir and returns its stdout;
// its error says what OpenCode printed last.
func opencodeOutput(ctx context.Context, path string, env []string, dir string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, path, args...)
	cmd.Env, cmd.Dir = env, dir
	cmd.Cancel = func() error { killGroup(cmd); return nil }
	cmd.WaitDelay = time.Second
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	detach(cmd)
	out, err := cmd.Output()
	if err != nil {
		lines := strings.Split(strings.TrimSpace(ansiEscape.ReplaceAllString(stderr.String(), "")), "\n")
		if msg := strings.TrimSpace(lines[len(lines)-1]); msg != "" {
			return nil, fmt.Errorf("opencode %s: %s", args[0], msg)
		}
		return nil, fmt.Errorf("opencode %s: %w", args[0], err)
	}
	return out, nil
}
