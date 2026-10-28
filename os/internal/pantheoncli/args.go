package pantheoncli

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"
)

// parsed is a command line after its group and action: flags, positional
// words, and the arguments to pass to an App method.
type parsed struct {
	flags      map[string]string
	bools      map[string]bool
	positional []string
	args       map[string]any
	rest       []string // after --
}

// Flags that take a value. Anything else written --name is a boolean, and
// key=value is a method argument.
var valueFlags = map[string]bool{
	"hub": true, "key": true, "username": true, "args": true, "args-file": true, "timeout": true,
	"node": true, "version": true, "job": true, "query": true, "kind": true, "out": true,
	"repository": true, "profile": true, "team": true, "resume": true,
	"agent": true, "cwd": true, "conversation": true, "model": true,
}

func parse(words []string, stdin io.Reader) (*parsed, error) {
	p := &parsed{flags: map[string]string{}, bools: map[string]bool{}, args: map[string]any{}}
	readArgs := func(data []byte, from string) error {
		var obj map[string]any
		if err := json.Unmarshal(data, &obj); err != nil {
			return fmt.Errorf("%s is not a JSON object: %v", from, err)
		}
		for k, v := range obj {
			p.args[k] = v
		}
		return nil
	}
	for i := 0; i < len(words); i++ {
		w := words[i]
		switch {
		case w == "--":
			p.rest = words[i+1:]
			return p, nil
		case w == "-":
			data, err := io.ReadAll(io.LimitReader(stdin, 64<<20))
			if err != nil {
				return nil, err
			}
			if err := readArgs(data, "stdin"); err != nil {
				return nil, err
			}
		case strings.HasPrefix(w, "--"):
			name, value, hasValue := strings.Cut(w[2:], "=")
			if !valueFlags[name] {
				if hasValue {
					b, err := strconv.ParseBool(value)
					if err != nil {
						return nil, fmt.Errorf("--%s takes no value", name)
					}
					p.bools[name] = b
				} else {
					p.bools[name] = true
				}
				continue
			}
			if !hasValue {
				if i+1 >= len(words) {
					return nil, fmt.Errorf("--%s needs a value", name)
				}
				i++
				value = words[i]
			}
			switch name {
			case "args":
				if err := readArgs([]byte(value), "--args"); err != nil {
					return nil, err
				}
			case "args-file":
				data, err := os.ReadFile(value)
				if err != nil {
					return nil, err
				}
				if err := readArgs(data, value); err != nil {
					return nil, err
				}
			default:
				p.flags[name] = value
			}
		case isArgument(w):
			key, value, _ := strings.Cut(w, "=")
			p.args[key] = argValue(value)
		default:
			p.positional = append(p.positional, w)
		}
	}
	return p, nil
}

// isArgument says whether a word is key=value: a name before the "=". Text
// that only contains one (a quoted task "… answer as COUNT=<n>") is a word.
func isArgument(w string) bool {
	key, _, found := strings.Cut(w, "=")
	if !found || key == "" {
		return false
	}
	for _, c := range key {
		if !(c == '_' || c == '-' || c == '.' || c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z') {
			return false
		}
	}
	return true
}

// argValue reads key=value: JSON when it is JSON (numbers, true, objects,
// quoted strings), otherwise the text itself.
func argValue(value string) any {
	var v any
	if json.Unmarshal([]byte(value), &v) == nil {
		return v
	}
	return value
}

func (p *parsed) want(n int, usage string) error {
	if len(p.positional) != n {
		return fmt.Errorf("usage: %s", usage)
	}
	return nil
}

func (p *parsed) timeout(def time.Duration) (time.Duration, error) {
	raw, ok := p.flags["timeout"]
	if !ok {
		return def, nil
	}
	seconds, err := strconv.Atoi(raw)
	if err != nil || seconds < 1 || seconds > 3600 {
		return 0, errors.New("--timeout takes seconds, 1 to 3600")
	}
	return time.Duration(seconds) * time.Second, nil
}
