package codingagents

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// NoteLaunch gives the note to an agent without an instructions flag
// (Instructions) in a terminal launch, through what each one reads at start,
// written under the service's own folder; the user's configuration and the
// project are left alone:
//   - OpenCode: an instructions file named in OPENCODE_CONFIG_CONTENT (merged
//     into the configuration env already gives, else the node's);
//   - Gemini CLI: a GEMINI.md in a folder given with --include-directories;
//   - Cursor Agent: a local plugin (--plugin-dir) whose sessionStart hook
//     adds the note as context.
//
// It returns the arguments to add and env with the variables set; nothing
// for other agents.
func NoteLaunch(agent, note string, env []string) ([]string, []string, error) {
	if note == "" {
		return nil, env, nil
	}
	dir := filepath.Join(Root(), "instructions", agent)
	write := func(name string, data []byte, mode os.FileMode) (string, error) {
		path := filepath.Join(dir, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			return "", err
		}
		tmp := path + ".tmp"
		if err := os.WriteFile(tmp, data, mode); err != nil {
			return "", err
		}
		return path, os.Rename(tmp, path)
	}
	switch agent {
	case "opencode":
		path, err := write("pantheonos.md", []byte(note+"\n"), 0o600)
		if err != nil {
			return nil, env, err
		}
		const name = "OPENCODE_CONFIG_CONTENT"
		config, at := os.Getenv(name), -1
		for i, kv := range env {
			if v, ok := strings.CutPrefix(kv, name+"="); ok {
				config, at = v, i
			}
		}
		doc := map[string]any{}
		if config != "" {
			if err := json.Unmarshal([]byte(config), &doc); err != nil || doc == nil {
				return nil, env, fmt.Errorf("OPENCODE_CONFIG_CONTENT is not a JSON object")
			}
		}
		// OpenCode joins the instructions of all its configurations.
		list, _ := doc["instructions"].([]any)
		doc["instructions"] = append(list, path)
		b, err := json.Marshal(doc)
		if err != nil {
			return nil, env, err
		}
		out := append([]string(nil), env...)
		if at < 0 {
			return nil, append(out, name+"="+string(b)), nil
		}
		out[at] = name + "=" + string(b)
		return nil, out, nil
	case "gemini":
		// It reads GEMINI.md in each included folder as context.
		if _, err := write("GEMINI.md", []byte(note+"\n"), 0o600); err != nil {
			return nil, env, err
		}
		return []string{"--include-directories", dir}, env, nil
	case "cursor":
		if runtime.GOOS == "windows" {
			return nil, env, nil
		}
		context, _ := json.Marshal(map[string]string{"additional_context": note})
		file, err := write("context.json", context, 0o600)
		if err != nil {
			return nil, env, err
		}
		manifest := []byte(`{"name":"pantheonos","version":"1.0.0","description":"The PantheonOS environment"}`)
		if _, err := write(filepath.Join(".cursor-plugin", "plugin.json"), manifest, 0o600); err != nil {
			return nil, env, err
		}
		hooks, _ := json.Marshal(map[string]any{"version": 1, "hooks": map[string]any{
			"sessionStart": []any{map[string]string{"command": "cat '" + strings.ReplaceAll(file, "'", `'\''`) + "'"}}}})
		if _, err := write(filepath.Join("hooks", "hooks.json"), hooks, 0o600); err != nil {
			return nil, env, err
		}
		return []string{"--plugin-dir", dir}, env, nil
	}
	return nil, env, nil
}
