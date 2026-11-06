package codingagentsapp

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// T35 (docs/apps/team-chat.md): a home folder, a folder that holds homes and
// the whole disk are what they are under every other name this computer
// has for them: a symlink, another case, macOS's data volume.
func TestFolderFactsSeeThroughOtherNames(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Skip("no home")
	}
	links := t.TempDir()
	for name, target := range map[string]string{"home": home, "root": "/", "parent": filepath.Dir(home)} {
		if err := os.Symlink(target, filepath.Join(links, name)); err != nil {
			t.Fatal(err)
		}
	}
	type want struct{ home, root, holds bool }
	cases := map[string]want{
		home:                           {home: true},
		filepath.Join(links, "home"):   {home: true},
		filepath.Join(links, "root"):   {root: true, holds: true},
		filepath.Join(links, "parent"): {holds: true},
		filepath.Dir(home):             {holds: true},
		filepath.Join(home, "PantheonAgents", "coder"):      {},
		filepath.Join(links, "home", "PantheonAgents", "x"): {},
	}
	if runtime.GOOS == "darwin" {
		cases[macData+home] = want{home: true}
		cases[macData+filepath.Dir(home)] = want{holds: true}
		cases[macData] = want{root: true, holds: true}
		cases[strings.ToLower(home)] = want{home: true}
		cases[strings.ToUpper(home)] = want{home: true}
		cases[macData+home+"/PantheonAgents/x"] = want{}
		if entries, err := os.ReadDir("/Volumes"); err == nil {
			for _, e := range entries {
				if v := "/Volumes/" + e.Name(); sameFile(v, "/") {
					cases[v] = want{root: true, holds: true}
					cases[v+home] = want{home: true}
				}
			}
		}
	}
	for folder, w := range cases {
		real, isHome, isRoot, holds := folderFacts(folder)
		if isHome != w.home || isRoot != w.root || holds != w.holds {
			t.Errorf("%s (%s): home %v root %v holds %v, want %+v", folder, real, isHome, isRoot, holds, w)
		}
	}
	r := teamSetup(macData+home, "", "", true)
	if runtime.GOOS == "darwin" && (r["home"] != true || r["real"] != home) {
		t.Errorf("teamSetup check: %v", r)
	}
}
