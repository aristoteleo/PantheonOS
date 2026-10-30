package codingagentsapp

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestConversationFile(t *testing.T) {
	root, cwd, other := t.TempDir(), t.TempDir(), t.TempDir()
	write := func(path, body string) string {
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	inside := write(filepath.Join(cwd, "a.txt"), "inside")
	named := write(filepath.Join(other, "x.txt"), strings.Repeat("z", fileChunkMax+10))
	secret := write(filepath.Join(other, "secret.txt"), "no")
	a := &App{root: root, convs: map[string]*Conversation{"c1": {ID: "c1", Cwd: cwd}}}
	write(a.logPath("c1"), `{"type":"tool","name":"Bash","input":"cd `+other+` && wc -l x.txt"}`+"\n")

	data := func(res map[string]any) string {
		if res["success"] != true {
			t.Fatalf("read failed: %v", res)
		}
		b, _ := base64.StdEncoding.DecodeString(res["data"].(string))
		return string(b)
	}
	if got := data(a.conversationFile("c1", inside, 0, 0)); got != "inside" {
		t.Errorf("inside the folder: %q", got)
	}
	first := a.conversationFile("c1", named, 0, 0)
	if len(data(first)) != fileChunkMax || first["size"] != int64(fileChunkMax+10) {
		t.Errorf("first piece: %d bytes of %v", len(data(first)), first["size"])
	}
	if got := data(a.conversationFile("c1", named, fileChunkMax, 0)); got != "zzzzzzzzzz" {
		t.Errorf("last piece: %q", got)
	}
	for _, p := range []string{secret, "x.txt", other, filepath.Join(cwd, "missing")} {
		if res := a.conversationFile("c1", p, 0, 0); res["success"] == true {
			t.Errorf("%s was read", p)
		}
	}
	if res := a.conversationFile("nope", inside, 0, 0); res["success"] == true {
		t.Error("read for a conversation that is not there")
	}
}
