package pantheoncli

// Team Chat for a Pantheon Agent turn (docs/apps/team-chat.md, "Pantheon
// Agent turns: Team Chat tools").
//
// Two ends of one channel:
//   - `pantheon team <command> …`, run by the Agent in its conversation's
//     shell, sends the command to the Agent (pantheon_command, op "team"),
//     with the words pantheon-team takes. The Agent accepts it only from a
//     command of a conversation whose running Team Chat turn opened it.
//   - `pantheon agent chat --json --team-tool <script>` is that turn: it
//     opens its conversation in the Agent (team_turn_open), takes the
//     requests (team_turn_next), runs each as its own child with the turn's
//     pantheon-team script, which the node answers only for a process of the
//     running turn, and returns the answer (team_turn_answer).

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"sync"
	"time"
)

const (
	teamUsage = "usage: pantheon team inbox | read TARGET | post '#CHANNEL' - <<'EOF' | reply ID - <<'EOF' | " +
		"dm @HANDLE - <<'EOF' | members | channels | join '#CHANNEL' | edit ID - <<'EOF' | skip [REASON] | " +
		"task ... | remind ... | search WORDS (quote a channel: an unquoted # starts a shell comment)"
	// teamTextMax is the most text a request carries (the node refuses
	// more than 32 KB of text).
	teamTextMax = 64 << 10
	// teamWait is how long `pantheon team` waits for its answer: the Agent
	// waits 130 s for the turn, which waits 120 s for the node.
	teamWait = 150 * time.Second
	// teamPoll is how long one team_turn_next call holds.
	teamPoll = 20
	// teamParallel requests of a turn run at once.
	teamParallel = 4
	// teamOutputMax is the most output of one request sent back.
	teamOutputMax = 60 << 10
)

var (
	teamCommandName = regexp.MustCompile(`^[a-z_]{1,32}$`)
	teamOptName     = regexp.MustCompile(`^[a-z][a-z_-]{0,31}$`)
)

// teamRequest is one Team Chat command, as pantheon-team takes it.
type teamRequest struct {
	ID      string            `json:"id,omitempty"`
	Command string            `json:"command"`
	Args    []string          `json:"args"`
	Opts    map[string]string `json:"opts"`
	Files   []string          `json:"files"`
	Text    *string           `json:"text"`
}

// check: one command, no word with a line break or that the script would
// read as an option, absolute files.
func (q *teamRequest) check() error {
	if !teamCommandName.MatchString(q.Command) {
		return errors.New(teamUsage)
	}
	words := slices.Concat(q.Args, q.Files)
	for k, v := range q.Opts {
		if !teamOptName.MatchString(k) || k == "file" {
			return fmt.Errorf("pantheon team: an invalid option --%s", k)
		}
		words = append(words, v)
	}
	for _, w := range words {
		if strings.ContainsAny(w, "\n\r\x00") {
			return errors.New("pantheon team: a word of the command has a line break; put text on stdin (-)")
		}
	}
	for _, a := range q.Args {
		if a == "-" || strings.HasPrefix(a, "--") {
			return fmt.Errorf("pantheon team: an invalid word %q", a)
		}
	}
	for _, f := range q.Files {
		if !filepath.IsAbs(f) {
			return errors.New("pantheon team: --file takes a path on the node where the Agent works")
		}
	}
	if q.Text != nil && len(*q.Text) > teamTextMax {
		return errors.New("pantheon team: not sent: a message is at most 32 KB; attach longer text with --file.")
	}
	return nil
}

// parseTeam reads `pantheon team` words as pantheon-team does: the command,
// then --file PATH, --name VALUE, - (the text on stdin) and arguments.
func parseTeam(words []string, stdin io.Reader, cwd string) (*teamRequest, error) {
	if len(words) == 0 {
		return nil, errors.New(teamUsage)
	}
	q := &teamRequest{Command: words[0], Args: []string{}, Opts: map[string]string{}, Files: []string{}}
	stdinText := false
	for i := 1; i < len(words); i++ {
		w := words[i]
		switch {
		case w == "--file":
			if i+1 >= len(words) {
				return nil, errors.New(teamUsage)
			}
			i++
			f := words[i]
			if !filepath.IsAbs(f) {
				f = filepath.Join(cwd, f)
			}
			q.Files = append(q.Files, f)
		case strings.HasPrefix(w, "--") && len(w) > 2:
			if i+1 >= len(words) {
				return nil, errors.New(teamUsage)
			}
			i++
			q.Opts[w[2:]] = words[i]
		case w == "-":
			stdinText = true
		default:
			q.Args = append(q.Args, w)
		}
	}
	if stdinText {
		data, err := io.ReadAll(io.LimitReader(stdin, teamTextMax+1))
		if err != nil {
			return nil, err
		}
		text := string(data)
		q.Text = &text
	}
	if err := q.check(); err != nil {
		return nil, err
	}
	return q, nil
}

// teamMain is `pantheon team …`: one Team Chat command of the Agent's
// conversation, through the Agent, answered by its running Team Chat turn.
func teamMain(words []string, stdin io.Reader, stdout, stderr io.Writer) int {
	link := agentFromEnv()
	if link == nil {
		fmt.Fprintln(stderr, "pantheon team: refused: Team Chat commands work only in a Pantheon Agent conversation "+
			"that a running Team Chat turn started.")
		return 1
	}
	cwd, _ := os.Getwd()
	q, err := parseTeam(words, stdin, cwd)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	params := map[string]any{"command": q.Command, "args": q.Args, "opts": q.Opts, "files": q.Files}
	if q.Text != nil {
		params["text"] = *q.Text
	}
	result, err := link.do(context.Background(), "team", params, teamWait)
	if err != nil {
		msg := err.Error()
		if !strings.HasPrefix(msg, "pantheon team") {
			msg = "pantheon team: " + msg
		}
		fmt.Fprintln(stderr, msg)
		return 1
	}
	var out struct {
		Code   int    `json:"code"`
		Output string `json:"output"`
	}
	if json.Unmarshal(result, &out) != nil {
		fmt.Fprintln(stderr, "pantheon team: the Agent returned an invalid answer")
		return 1
	}
	w := stdout
	if out.Code != 0 {
		w = stderr
	}
	if out.Output != "" {
		fmt.Fprintln(w, strings.TrimRight(out.Output, "\n"))
	}
	return min(max(out.Code, 0), 125)
}

// ---- the turn's end --------------------------------------------------------

// teamServer runs a Team Chat turn's requests: its conversation's
// `pantheon team` commands, as the turn's own pantheon-team.
type teamServer struct {
	t     *agentTurnState
	lease string
	tool  string
	dir   string
	mu    sync.Mutex
	ran   map[string]bool // each request id runs once
}

// openTeam opens the turn's conversation in the Agent; nil when the Agent
// cannot (an Agent from before Team Chat tools: `pantheon team` is then
// refused there).
func (t *agentTurnState) openTeam(ctx context.Context, tool, dir string) *teamServer {
	if tool == "" {
		return nil
	}
	result, err := t.r.s.Call(ctx, t.b, "team_turn_open", map[string]any{"chat_id": t.chatID}, 60*time.Second)
	if err == nil {
		err = failed(result)
	}
	var opened struct {
		Lease string `json:"lease"`
	}
	if err != nil || json.Unmarshal(result, &opened) != nil || opened.Lease == "" {
		return nil
	}
	return &teamServer{t: t, lease: opened.Lease, tool: tool, dir: dir, ran: map[string]bool{}}
}

// serve takes and runs the turn's requests until ctx ends.
func (s *teamServer) serve(ctx context.Context) {
	var wg sync.WaitGroup
	defer wg.Wait()
	slots := make(chan struct{}, teamParallel)
	pause := time.Second
	for ctx.Err() == nil {
		result, err := s.t.r.s.Call(ctx, s.t.b, "team_turn_next", map[string]any{"chat_id": s.t.chatID,
			"lease": s.lease, "wait": teamPoll}, (teamPoll+25)*time.Second)
		var page struct {
			Success  bool          `json:"success"`
			Closed   bool          `json:"closed"`
			Requests []teamRequest `json:"requests"`
		}
		if err == nil && json.Unmarshal(result, &page) == nil && !page.Success && page.Closed {
			return // the Agent ended the turn's lease
		}
		if err != nil || !page.Success {
			select {
			case <-ctx.Done():
			case <-time.After(pause):
			}
			pause = min(2*pause, 10*time.Second)
			continue
		}
		pause = time.Second
		for _, q := range page.Requests {
			s.mu.Lock()
			again := q.ID == "" || s.ran[q.ID]
			if !again {
				s.ran[q.ID] = true
			}
			s.mu.Unlock()
			if again {
				continue // a request id runs at most once
			}
			wg.Add(1)
			slots <- struct{}{}
			go func(q teamRequest) {
				defer wg.Done()
				defer func() { <-slots }()
				code, text := s.run(ctx, q)
				ans, cancel := context.WithTimeout(context.Background(), 30*time.Second)
				defer cancel()
				_, _ = s.t.r.s.Call(ans, s.t.b, "team_turn_answer", map[string]any{"chat_id": s.t.chatID,
					"lease": s.lease, "request_id": q.ID, "code": code, "text": text}, 30*time.Second)
			}(q)
		}
	}
}

// close ends the turn's lease: what still waits in the Agent is told the
// turn ended.
func (s *teamServer) close() {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	_, _ = s.t.r.s.Call(ctx, s.t.b, "team_turn_close", map[string]any{"chat_id": s.t.chatID, "lease": s.lease},
		15*time.Second)
}

// run runs one request with the turn's pantheon-team, as a child of this
// process: a process of the running turn, which the node answers.
func (s *teamServer) run(ctx context.Context, q teamRequest) (int, string) {
	if err := q.check(); err != nil {
		return 2, err.Error()
	}
	argv := append([]string{s.tool, q.Command}, q.Args...)
	opts := make([]string, 0, len(q.Opts))
	for k := range q.Opts {
		opts = append(opts, k)
	}
	slices.Sort(opts)
	for _, k := range opts {
		argv = append(argv, "--"+k, q.Opts[k])
	}
	for _, f := range q.Files {
		argv = append(argv, "--file", f)
	}
	cmd := exec.CommandContext(ctx, "/bin/sh", argv...)
	cmd.Dir = s.dir
	cmd.Env = teamToolEnv(os.Environ())
	if q.Text != nil {
		cmd.Args = append(cmd.Args, "-")
		cmd.Stdin = strings.NewReader(*q.Text)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &capped{b: &stdout}, &capped{b: &stderr}
	err := cmd.Run()
	code := 0
	var exit *exec.ExitError
	switch {
	case errors.As(err, &exit):
		code = exit.ExitCode()
		if code <= 0 {
			code = 1
		}
	case err != nil:
		return 1, "pantheon team: the turn could not run its Team Chat tool (" + err.Error() + ")"
	}
	if code == 0 {
		return 0, stdout.String()
	}
	return code, strings.TrimSpace(stderr.String() + "\n" + stdout.String())
}

// teamToolEnv is the environment of the turn's pantheon-team: what a shell
// script needs, never the turn's PantheonOS key or Hub.
func teamToolEnv(env []string) []string {
	keep := map[string]bool{"PATH": true, "HOME": true, "USER": true, "LOGNAME": true, "SHELL": true,
		"TMPDIR": true, "TMP": true, "TEMP": true, "LANG": true, "LANGUAGE": true, "TZ": true, "TERM": true}
	var out []string
	for _, kv := range env {
		k, _, _ := strings.Cut(kv, "=")
		if keep[k] || strings.HasPrefix(k, "LC_") {
			out = append(out, kv)
		}
	}
	return out
}

// capped keeps the first teamOutputMax bytes written.
type capped struct{ b *bytes.Buffer }

func (c *capped) Write(p []byte) (int, error) {
	if room := teamOutputMax - c.b.Len(); room > 0 {
		c.b.Write(p[:min(len(p), room)])
	}
	return len(p), nil
}
