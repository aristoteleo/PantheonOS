package codingagentsapp

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
)

// What a failed turn tells the user: one line that names the agent and
// says what went wrong, not the agent's stack trace.

// signInMarks are words an agent's error has when it could not sign in or
// its account may not use it.
var signInMarks = []string{"not signed in", "not logged in", "please log in", "please login", "log in again",
	"run codex login", "/login", "sign in", "unauthorized", "unauthenticated", "api key not valid", "invalid api key",
	"ineligible", "error authenticating", "authentication failed", "no longer supported for gemini code assist"}

func signInError(text string) bool {
	t := strings.ToLower(text)
	for _, m := range signInMarks {
		if strings.Contains(t, m) {
			return true
		}
	}
	return false
}

var (
	// errorPrefix is a label before the message itself.
	errorPrefix = regexp.MustCompile(`^(An unexpected critical error occurred|Error authenticating|Error|[A-Z][A-Za-z]*Error)\s*:\s*`)
	// nestedMessage is a "message" inside JSON, possibly escaped (an API
	// error an agent passes on as it got it).
	nestedMessage = regexp.MustCompile(`\\*"message\\*"\s*:\s*\\*"([^"\\]+)`)
	urlPattern    = regexp.MustCompile(`https?://[^\s"'<>)\]]+`)
)

// stackLine is a line of a stack trace or a dumped object, not a message.
func stackLine(l string) bool {
	t := strings.TrimSpace(l)
	return t == "" || strings.HasPrefix(t, "at ") || strings.Contains(t, "file://") || strings.Contains(t, "node:internal") ||
		strings.Trim(t, "{}[](),") == "" || strings.HasSuffix(t, ": [") || strings.HasSuffix(t, ": {")
}

// errorLine is the line of an agent's error output that says what went
// wrong: a message nested in JSON unwrapped, stack frames and dumped
// objects skipped, labels like "Error:" dropped, and a URL from further
// down added when the line has none.
func errorLine(text string) string {
	line := ""
	if strings.Contains(text, `message`) && strings.Contains(text, "{") {
		for _, m := range nestedMessage.FindAllStringSubmatch(text, -1) {
			if s := strings.TrimSpace(m[1]); s != "" && !strings.HasPrefix(s, "{") {
				line = s
			}
		}
	}
	if line == "" {
		// The line that says what failed, past warnings printed before it:
		// a sign-in failure, else one that reads as an error, else the first.
		var lines []string
		for _, l := range strings.Split(text, "\n") {
			if !stackLine(l) {
				lines = append(lines, strings.TrimSpace(l))
			}
		}
		for _, pick := range []func(string) bool{signInError, func(l string) bool {
			return errorPrefix.MatchString(l) || strings.Contains(strings.ToLower(l), "error")
		}, func(string) bool { return true }} {
			if i := slices.IndexFunc(lines, pick); i >= 0 {
				line = lines[i]
				break
			}
		}
	}
	for {
		trimmed := errorPrefix.ReplaceAllString(line, "")
		if trimmed == line {
			break
		}
		line = trimmed
	}
	if len(line) > 300 {
		line = line[:300] + "…"
	}
	if !strings.Contains(line, "://") {
		for _, u := range urlPattern.FindAllString(text, -1) {
			line = strings.TrimSpace(line + " (" + u + ")")
			break
		}
	}
	return line
}

// exitMessage is why a turn ended without the agent saying so, in one
// line: that it could not sign in (its output says so, or it is known to
// have no sign-in here), else its exit and the first line of what it
// printed.
func exitMessage(label string, err error, stderr string, signedOut bool) string {
	line := errorLine(stderr)
	switch {
	case (signedOut || signInError(stderr)) && line != "":
		return label + " could not sign in: " + line
	case signedOut || signInError(stderr):
		return label + " is not signed in on this node"
	case line != "":
		return fmt.Sprintf("%s exited (%v): %s", label, err, line)
	}
	return fmt.Sprintf("%s exited: %v", label, err)
}

// agentError is an error the agent reported itself, made one line when it
// is more than that (an API error's JSON, a stack trace); a sign-in error
// then names the agent. A one-line error stays as the agent wrote it.
func agentError(label, text string) string {
	if !strings.Contains(text, "\n") && !strings.HasPrefix(text, "[API Error:") {
		return text
	}
	line := errorLine(text)
	if line == "" {
		return text
	}
	if signInError(text) {
		return label + " could not sign in: " + line
	}
	return line
}
