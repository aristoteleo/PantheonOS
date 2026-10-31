package codingagentsapp

import (
	"math/rand/v2"
	"os"
	"strconv"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// A workspace made without a name gets two words, an adjective and a bird
// or a tree, short enough to read in a card and to type in a path.

var nameAdjectives = []string{
	"amber", "ashen", "azure", "bold", "brisk", "calm", "cedar", "clear", "coral", "crisp",
	"dawn", "deep", "dusky", "early", "fair", "fern", "fleet", "frost", "gentle", "gilded",
	"golden", "green", "hazel", "honey", "ivory", "jade", "keen", "lucid", "lunar", "maple",
	"mellow", "misty", "mossy", "noble", "ochre", "olive", "pale", "pine", "quiet", "rapid",
	"rosy", "ruby", "rustic", "sage", "sandy", "silver", "slate", "snowy", "solar", "steady",
	"stormy", "sunny", "swift", "tawny", "teal", "tidal", "velvet", "vivid", "warm", "wild",
}

var nameNouns = []string{
	"alder", "aspen", "birch", "bittern", "cedar", "crane", "curlew", "cypress", "dipper", "dunlin",
	"egret", "elm", "falcon", "finch", "gannet", "grebe", "harrier", "hawk", "hazel", "heron",
	"ibis", "jay", "juniper", "kestrel", "kite", "lark", "larch", "linnet", "magpie", "maple",
	"martin", "merlin", "oak", "osprey", "owl", "pipit", "plover", "poplar", "puffin", "raven",
	"redwood", "robin", "rowan", "sparrow", "spruce", "starling", "swift", "tern", "thrush", "wagtail",
	"warbler", "willow", "wren", "yew",
}

// newWorkspaceName is a two-word name no worktree folder, branch or kept
// workspace of the project uses yet.
func newWorkspaceName(root string, kept []codingagents.Workspace) string {
	free := func(name string) bool {
		for _, w := range kept {
			if w.Name == name {
				return false
			}
		}
		if _, err := os.Stat(worktreeDir(root, name)); err == nil {
			return false
		}
		return !branchExists(root, branchFor(root, name)) && !branchExists(root, name)
	}
	for range 40 {
		a, n := nameAdjectives[rand.IntN(len(nameAdjectives))], nameNouns[rand.IntN(len(nameNouns))]
		if a == n {
			continue
		}
		if name := a + "-" + n; free(name) {
			return name
		}
	}
	base := nameAdjectives[rand.IntN(len(nameAdjectives))] + "-" + nameNouns[rand.IntN(len(nameNouns))]
	for k := 2; ; k++ {
		if name := base + "-" + strconv.Itoa(k); free(name) {
			return name
		}
	}
}
