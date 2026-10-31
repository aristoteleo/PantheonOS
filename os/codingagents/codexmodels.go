package codingagents

import (
	"bufio"
	"encoding/json"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"
)

// Codex's models depend on the account, so they are not listed in the
// catalog: Codex keeps the ones its account may use in models_cache.json
// under its home, and its default in config.toml there. Both are read again
// only when they change.

// Models is what an agent offers on the canvas besides its default.
type Models struct {
	IDs     []string
	Labels  map[string]string
	Default string
}

// codexHome is Codex's own folder ($CODEX_HOME, else ~/.codex).
func codexHome() string {
	if d := os.Getenv("CODEX_HOME"); d != "" {
		return d
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".codex")
}

// fileStamp tells whether a file changed: its modification time and size,
// zero for a file that cannot be read.
type fileStamp struct {
	mod  time.Time
	size int64
}

func stampOf(path string) fileStamp {
	st, err := os.Stat(path)
	if err != nil {
		return fileStamp{}
	}
	return fileStamp{st.ModTime(), st.Size()}
}

var codexModelsMemo struct {
	sync.Mutex
	home          string
	cache, config fileStamp
	models        Models
	read          bool
}

// CodexModels is the models Codex lists for its account, in its order, and
// its configured default; empty when Codex has no model cache it can read.
func CodexModels() Models {
	home := codexHome()
	if home == "" {
		return Models{}
	}
	cachePath, configPath := filepath.Join(home, "models_cache.json"), filepath.Join(home, "config.toml")
	cache, config := stampOf(cachePath), stampOf(configPath)
	m := &codexModelsMemo
	m.Lock()
	defer m.Unlock()
	if !m.read || m.home != home || m.cache != cache || m.config != config {
		ids, labels := readCodexModelCache(cachePath)
		m.models = Models{IDs: ids, Labels: labels, Default: readTOMLTopString(configPath, "model")}
		m.home, m.cache, m.config, m.read = home, cache, config, true
	}
	// Copies: the caller may change what it gets.
	return Models{IDs: append([]string(nil), m.models.IDs...), Labels: maps.Clone(m.models.Labels), Default: m.models.Default}
}

// readCodexModelCache reads the listed models of a models_cache.json, with
// the display names that differ from their ids; nil for a file missing, not
// JSON or of another shape.
func readCodexModelCache(path string) ([]string, map[string]string) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, nil
	}
	var doc struct {
		Models []json.RawMessage `json:"models"`
	}
	if json.Unmarshal(data, &doc) != nil {
		return nil, nil
	}
	var ids []string
	labels := map[string]string{}
	for _, raw := range doc.Models {
		var e struct {
			Slug        string `json:"slug"`
			DisplayName string `json:"display_name"`
			Visibility  string `json:"visibility"`
		}
		// An entry of another shape is skipped, not the whole list.
		if json.Unmarshal(raw, &e) != nil || e.Slug == "" || e.Visibility != "list" {
			continue
		}
		if slices.Contains(ids, e.Slug) {
			continue
		}
		ids = append(ids, e.Slug)
		if name := strings.TrimSpace(e.DisplayName); name != "" && name != e.Slug {
			labels[e.Slug] = name
		}
	}
	if len(labels) == 0 {
		labels = nil
	}
	return ids, labels
}

// readTOMLTopString is the string value of a top-level key of a TOML file
// (one before any [table]); empty when absent or not a plain string.
func readTOMLTopString(path, key string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if strings.HasPrefix(line, "[") {
			return ""
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok || strings.Trim(strings.TrimSpace(k), `"'`) != key {
			continue
		}
		return tomlStringValue(strings.TrimSpace(v))
	}
	return ""
}

// tomlStringValue reads a basic ("…") or literal ('…') TOML string that
// starts v, ignoring what follows it (a comment).
func tomlStringValue(v string) string {
	if len(v) < 2 {
		return ""
	}
	switch v[0] {
	case '\'':
		if end := strings.IndexByte(v[1:], '\''); end >= 0 {
			return v[1 : 1+end]
		}
	case '"':
		var b strings.Builder
		for i := 1; i < len(v); i++ {
			c := v[i]
			switch {
			case c == '"':
				return b.String()
			case c == '\\' && i+1 < len(v):
				i++
				switch v[i] {
				case 'n':
					b.WriteByte('\n')
				case 't':
					b.WriteByte('\t')
				case 'r':
					b.WriteByte('\r')
				default:
					b.WriteByte(v[i])
				}
			default:
				b.WriteByte(c)
			}
		}
	}
	return ""
}
