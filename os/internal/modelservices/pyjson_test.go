package modelservices

import (
	"encoding/json"
	"os/exec"
	"testing"
)

// The encoder must match Python's json.dumps byte for byte.
func TestPyJSONMatchesPython(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 unavailable")
	}
	value := `{"b": [1, 2.5, 1e-07, 1e+16, 123456789012.0, true, null], "a": "café 😀 <tag> \"q\" \\ \n", "n": {"z": 0.1, "y": -3}}`
	for _, c := range []struct {
		sort       bool
		item, key  string
		pythonArgs string
	}{{true, ",", ":", "sort_keys=True, separators=(',', ':')"}, {true, ", ", ": ", "sort_keys=True"}} {
		out, err := exec.Command(python, "-c", "import json,sys; sys.stdout.write(json.dumps(json.loads(sys.argv[1]), "+c.pythonArgs+"))", value).Output()
		if err != nil {
			t.Fatal(err)
		}
		var v any
		decoder := json.NewDecoder(stringsReader(value))
		decoder.UseNumber()
		_ = decoder.Decode(&v)
		got, err := PyJSON(v, c.sort, c.item, c.key)
		if err != nil || string(got) != string(out) {
			t.Fatalf("\n go: %s\npy: %s (%v)", got, out, err)
		}
	}
	o := ordered("id", "model-service", "name", "x", "version", "0.1.0", "apiVersion", 2)
	if got, _ := PyJSON(o, false, ", ", ": "); string(got) != `{"id": "model-service", "name": "x", "version": "0.1.0", "apiVersion": 2}` {
		t.Fatal(string(got))
	}
}
