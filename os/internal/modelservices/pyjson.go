// Package modelservices manages an owner's model services from the Fleet
// controller (formerly the Python model-management App,
// pantheonos/models/manager.py and friends; docs/fleet-orchestration.md §15).
package modelservices

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
)

// Ordered is a JSON object that keeps its key order (Python dict insertion
// order) when encoded without sort_keys.
type Ordered struct {
	Keys   []string
	Values map[string]any
}

func ordered(pairs ...any) Ordered {
	o := Ordered{Values: map[string]any{}}
	for i := 0; i+1 < len(pairs); i += 2 {
		k := pairs[i].(string)
		o.Keys = append(o.Keys, k)
		o.Values[k] = pairs[i+1]
	}
	return o
}

// PyJSON encodes like Python's json.dumps(value, sort_keys=sortKeys,
// separators=(item, key)) with ensure_ascii=True, so digests and generated
// package files match the ones the Python implementation produced.
func PyJSON(v any, sortKeys bool, item, key string) ([]byte, error) {
	var b bytes.Buffer
	if err := pyEncode(&b, normalize(v), sortKeys, item, key); err != nil {
		return nil, err
	}
	return b.Bytes(), nil
}

// Compact is json.dumps(v, sort_keys=True, separators=(',', ':')).
func Compact(v any) []byte {
	raw, err := PyJSON(v, true, ",", ":")
	if err != nil {
		panic(err)
	}
	return raw
}

// normalize turns Go values (structs, typed maps) into the generic JSON
// shapes pyEncode handles; Ordered and json.Number pass through.
func normalize(v any) any {
	switch t := v.(type) {
	case nil, bool, string, float64, int, int64, json.Number, Ordered:
		return t
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, x := range t {
			out[k] = normalize(x)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = normalize(x)
		}
		return out
	}
	raw, err := json.Marshal(v)
	if err != nil {
		return v
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var generic any
	if decoder.Decode(&generic) != nil {
		return v
	}
	return normalize(generic)
}

func pyEncode(b *bytes.Buffer, v any, sortKeys bool, item, key string) error {
	switch t := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if t {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case string:
		pyString(b, t)
	case int:
		b.WriteString(strconv.Itoa(t))
	case int64:
		b.WriteString(strconv.FormatInt(t, 10))
	case json.Number:
		if strings.ContainsAny(string(t), ".eE") {
			f, err := t.Float64()
			if err != nil {
				return err
			}
			b.WriteString(pyFloat(f))
		} else {
			b.WriteString(string(t))
		}
	case float64:
		if t == math.Trunc(t) && math.Abs(t) < 1<<53 {
			// Values decoded from JSON integers arrive as float64.
			b.WriteString(strconv.FormatInt(int64(t), 10))
		} else {
			b.WriteString(pyFloat(t))
		}
	case []any:
		b.WriteByte('[')
		for i, x := range t {
			if i > 0 {
				b.WriteString(item)
			}
			if err := pyEncode(b, x, sortKeys, item, key); err != nil {
				return err
			}
		}
		b.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		if !sortKeys && len(keys) > 1 {
			return fmt.Errorf("an unordered object needs sort_keys")
		}
		sort.Strings(keys)
		return pyObject(b, keys, t, sortKeys, item, key)
	case Ordered:
		keys := t.Keys
		if sortKeys {
			keys = append([]string{}, keys...)
			sort.Strings(keys)
		}
		values := map[string]any{}
		for k, x := range t.Values {
			values[k] = normalize(x)
		}
		return pyObject(b, keys, values, sortKeys, item, key)
	default:
		return fmt.Errorf("cannot encode %T", v)
	}
	return nil
}

func pyObject(b *bytes.Buffer, keys []string, values map[string]any, sortKeys bool, item, key string) error {
	b.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			b.WriteString(item)
		}
		pyString(b, k)
		b.WriteString(key)
		if err := pyEncode(b, values[k], sortKeys, item, key); err != nil {
			return err
		}
	}
	b.WriteByte('}')
	return nil
}

// pyString is Python's ensure_ascii string encoding.
func pyString(b *bytes.Buffer, s string) {
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if r < 0x20 || r > 0x7e {
				if r > 0xffff {
					a, c := utf16.EncodeRune(r)
					fmt.Fprintf(b, `\u%04x\u%04x`, a, c)
				} else {
					fmt.Fprintf(b, `\u%04x`, r)
				}
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}

// pyFloat is Python's float repr (shortest round-trip, its exponent rules).
func pyFloat(f float64) string {
	switch {
	case math.IsInf(f, 1):
		return "Infinity"
	case math.IsInf(f, -1):
		return "-Infinity"
	case math.IsNaN(f):
		return "NaN"
	}
	digits := strconv.FormatFloat(f, 'e', -1, 64) // d.ddde±XX
	mantissa, exp, _ := strings.Cut(digits, "e")
	e, _ := strconv.Atoi(exp)
	if e < -4 || e >= 16 {
		sign := "+"
		if e < 0 {
			sign, e = "-", -e
		}
		return fmt.Sprintf("%se%s%02d", mantissa, sign, e)
	}
	out := strconv.FormatFloat(f, 'f', -1, 64)
	if !strings.Contains(out, ".") {
		out += ".0"
	}
	return out
}

// Digest is the SHA-256 hex digest helpers use for identities.
func pyDefault(v any) []byte {
	raw, err := PyJSON(v, false, ", ", ": ")
	if err != nil {
		panic(err)
	}
	return raw
}
