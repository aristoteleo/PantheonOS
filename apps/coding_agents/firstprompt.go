package codingagentsapp

import "strings"

// promptArgs are the arguments that start an agent's own interface with a
// first message already sent, as a new workspace's agent starts; nil for an
// agent that cannot take one on its command line.
func promptArgs(agent, prompt string) []string {
	prompt = strings.TrimSpace(prompt)
	if prompt == "" {
		return nil
	}
	if strings.HasPrefix(prompt, "-") {
		// Not to be read as a flag.
		prompt = " " + prompt
	}
	switch agent {
	case "claude", "codex", "cursor":
		// The last argument, after options that each take one value.
		return []string{prompt}
	case "gemini", "qwen":
		return []string{"--prompt-interactive", prompt}
	}
	return nil
}
