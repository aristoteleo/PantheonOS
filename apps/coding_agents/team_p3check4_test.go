package codingagentsapp

// Fixes after the fourth Phase 3 check (docs/apps/team-chat.md, "Fixes after
// the fourth Phase 3 check"): every request is vouched for whatever its size,
// and one too large or with a line PantheonOS does not know is refused; a
// request file swapped or rewritten in the inbox is never answered as the
// turn's own command, as the digest the turn's process vouches with is of
// the request in its memory.

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// teamRun runs one Team Chat turn of a fake Claude Code whose script is body
// and returns what reached the controller and a reader of the agent's files.
func teamRun(t *testing.T, body string) ([]map[string]any, func(string) string) {
	t.Helper()
	dir := fakeAgents(t, map[string]string{"claude": teamCommand + `d="$(dirname "$0")"
box="$(dirname "$team")/inbox"
` + body + claudeDone})
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	var mu sync.Mutex
	var reqs []map[string]any
	a.teamCaller = func(req map[string]any) (map[string]any, error) {
		mu.Lock()
		defer mu.Unlock()
		reqs = append(reqs, req)
		return map[string]any{"code": float64(0), "text": "ANSWERED " + str(req["op"]) + " " + strings.Join(req["args"].([]string), " ")}, nil
	}
	folder := filepath.Join(t.TempDir(), "coder")
	if r := teamSetup(folder, "brief", "", false); r["success"] != true {
		t.Fatal(r)
	}
	res := a.send(SendArgs{Agent: "claude", Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
		Team: &TeamTurn{MID: "a1", Token: "tok", Handle: "coder", Name: "Coder", Turn: "t1", Cred: "c1"}})
	if res["success"] != true {
		t.Fatal(res)
	}
	waitIdle(t, a, res["conversation_id"].(string))
	mu.Lock()
	defer mu.Unlock()
	return append([]map[string]any(nil), reqs...), func(name string) string {
		b, _ := os.ReadFile(filepath.Join(dir, name))
		return string(b)
	}
}

// Finding A: a request padded past the old 36 KB size check (an ignored
// header line) is vouched for like any other: an escaped process's is
// refused; one a process of the turn vouches for is refused for its size or
// its unknown line; the script refuses text over 32 KB itself.
func TestEveryTeamRequestIsVouchedWhateverItsSize(t *testing.T) {
	if !procVouch {
		t.Skip("this system cannot read another process's arguments")
	}
	if _, err := exec.LookPath("perl"); err != nil {
		t.Skip("no perl to start an escaped process")
	}
	reqs, read := teamRun(t, `
# hand ID REQUEST-FILE: vouch for a request written by hand, from the
# process running it (in the turn, or escaped).
cat > "$d/hand.sh" <<'HAND'
box=$1; id=$2; src=$3; out=$4
sum=$(shasum -a 256 < "$src"); sum=${sum%% *}
cp "$src" "$box/.$id.tmp"
sh -c 'sleep 4; :' pantheon-team-wait "$box" "$id" "$sum" &
sleep 0.3
mv "$box/.$id.tmp" "$box/$id.msg"
i=0; while [ ! -f "$box/$id.reply" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
cat "$box/$id.reply" > "$out" 2>/dev/null || echo "no answer" > "$out"
HAND
pad=$(head -c 40000 /dev/zero | tr '\0' A)
printf 'op: read\narg: secret\npad: %s\n\n' "$pad" > "$d/padded.req"
printf 'op: read\narg: secret\npad: x\n\n' > "$d/unknown.req"
# An escaped process (fork, setsid, fork) with the padded request.
perl -MPOSIX -e 'exit 0 if fork; POSIX::setsid(); exit 0 if fork; exec "sh", @ARGV' "$d/hand.sh" "$box" 7-1 "$d/padded.req" "$d/escaped.txt" < /dev/null > /dev/null 2>&1
i=0; while [ ! -s "$d/escaped.txt" ] && [ $i -lt 80 ]; do sleep 0.1; i=$((i+1)); done
# The same, and one with an unknown line, vouched for inside the turn.
sh "$d/hand.sh" "$box" 7-2 "$d/padded.req" "$d/inside.txt"
sh "$d/hand.sh" "$box" 7-3 "$d/unknown.req" "$d/unknown.txt"
head -c 40000 /dev/zero | tr '\0' a | "$team" post general - > "$d/long.txt" 2>&1
"$team" read general > "$d/own.txt" 2>&1
`)
	if got := read("escaped.txt"); !strings.Contains(got, "refused") || strings.Contains(got, "ANSWERED") {
		t.Fatalf("an escaped process's padded request: %q", got)
	}
	if got := read("inside.txt"); !strings.Contains(got, "refused: a request is at most") {
		t.Fatalf("a padded request vouched for inside the turn: %q", got)
	}
	if got := read("unknown.txt"); !strings.Contains(got, "does not know (pad: x)") {
		t.Fatalf("a request with an unknown line: %q", got)
	}
	if got := read("long.txt"); !strings.Contains(got, "not sent: a message is at most 32 KB") {
		t.Fatalf("text over 32 KB: %q", got)
	}
	if got := read("own.txt"); !strings.Contains(got, "ANSWERED read general") {
		t.Fatalf("the turn's own command: %q", got)
	}
	if len(reqs) != 1 {
		t.Fatalf("what reached the controller: %v", reqs)
	}
}

// Finding B: a process that swaps its own request into the inbox under the
// turn's request file (before or after it is moved into place) gets
// nothing: the turn's process vouches with the digest of the request in its
// memory, so the swapped file is refused and nothing it asks reaches the
// controller. No file in the inbox ever holds the turn's credential.
func TestASwappedTeamRequestFileIsNeverAnswered(t *testing.T) {
	if !procVouch {
		t.Skip("this system cannot read another process's arguments")
	}
	reqs, read := teamRun(t, `
: > "$d/swaps"
( n=0; while [ $n -lt 4000 ] && [ ! -f "$d/stop" ]; do
	for f in "$box"/.*.tmp "$box"/*.msg; do
		[ -f "$f" ] || continue
		grep -q secret "$f" 2>/dev/null && continue
		cat "$f" >> "$d/seen" 2>/dev/null
		printf 'op: read\narg: secret\n\n' > "$box/.swap"
		mv "$box/.swap" "$f" 2>/dev/null && echo "$f" >> "$d/swaps"
	done
	n=$((n+1))
done ) &
for i in 1 2 3 4 5; do "$team" channels >> "$d/out.txt" 2>&1; done
touch "$d/stop"
wait
`)
	for _, r := range reqs {
		if r["op"] != "channels" {
			t.Fatalf("a swapped request reached the controller: %v", r)
		}
	}
	swaps := strings.Count(read("swaps"), "\n")
	if swaps == 0 {
		t.Skip("no file was swapped in time on this computer")
	}
	// A swap may cost the turn that command (refused), never more.
	if got := read("out.txt"); strings.Contains(got, "ANSWERED read") || strings.Count(got, "ANSWERED channels") > len(reqs) {
		t.Fatalf("%d swaps, answers: %q", swaps, got)
	}
	if seen := read("seen"); strings.Contains(seen, "c1") || strings.Contains(seen, "turn:") {
		t.Fatalf("an inbox file held the credential: %q", seen)
	}
}
