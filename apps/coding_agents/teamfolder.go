package codingagentsapp

// Where a Team Chat agent's folder really is on this computer, for the
// controller's check that a shared agent does not work in a home folder, a
// folder that holds home folders, or the whole disk (docs/apps/team-chat.md,
// T35). A path can reach a home folder under another name: a symlink, a
// different case on a case-insensitive disk, macOS's data volume
// (/System/Volumes/Data/Users/<me> is ~ itself) or the boot volume under
// /Volumes/<name>. So the path is resolved here, on the computer, before it
// is compared, and compared by the file it names too.

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// macData is macOS's data volume: /Users, /private/var, … are the same
// folders under it.
const macData = "/System/Volumes/Data"

// homeParents are folders that hold people's home folders.
var homeParents = []string{"/Users", "/home", "/var/home", "/export/home", macData + "/Users"}

// folderFacts: folder (absolute) as it really is here, and whether it is a
// home folder, the whole disk, or holds a home folder.
func folderFacts(folder string) (real string, isHome, isRoot, holdsHome bool) {
	real = canonicalPath(resolvePath(folder))
	isRoot = real == "/" || sameFile(folder, "/") || runtime.GOOS == "darwin" && sameFile(folder, macData) ||
		real == filepath.VolumeName(real)+string(filepath.Separator)
	for _, h := range homeFolders() {
		switch {
		case samePath(real, h) || sameFile(folder, h):
			isHome = true
		case below(h, real):
			holdsHome = true
		}
	}
	for _, p := range homeParents {
		if samePath(real, p) || sameFile(folder, p) {
			holdsHome = true
		}
	}
	return real, isHome, isRoot, holdsHome
}

// resolvePath follows symlinks in the longest part of p that exists (the
// rest is to be made), and puts each existing part in the case the disk
// has it.
func resolvePath(p string) string {
	p = filepath.Clean(p)
	rest := ""
	for {
		if r, err := filepath.EvalSymlinks(p); err == nil {
			return filepath.Join(trueCase(r), rest)
		}
		parent := filepath.Dir(p)
		if parent == p {
			return filepath.Join(p, rest)
		}
		rest = filepath.Join(filepath.Base(p), rest)
		p = parent
	}
}

// trueCase spells an existing path as its folders list it (on a disk that
// ignores case, /users/SHARED is /Users/Shared).
func trueCase(p string) string {
	if runtime.GOOS != "darwin" && runtime.GOOS != "windows" {
		return p
	}
	vol := filepath.VolumeName(p)
	out := vol + string(filepath.Separator)
	for _, part := range strings.Split(strings.TrimPrefix(p[len(vol):], string(filepath.Separator)), string(filepath.Separator)) {
		if part == "" {
			continue
		}
		name := part
		if entries, err := os.ReadDir(out); err == nil {
			for _, e := range entries {
				if e.Name() == part {
					name = part
					break
				}
				if strings.EqualFold(e.Name(), part) {
					name = e.Name()
				}
			}
		}
		out = filepath.Join(out, name)
	}
	return out
}

// canonicalPath names a resolved path by the folder it is: on macOS the
// data volume's folders by their usual names (/System/Volumes/Data/Users is
// /Users; the data volume's root holds every home, as / does), and the boot
// volume's other names under /Volumes as /.
func canonicalPath(p string) string {
	if runtime.GOOS != "darwin" {
		return p
	}
	for i := 0; i < 4; i++ {
		switch {
		case samePath(p, macData):
			return "/"
		case hasPrefixFold(p, macData+"/"):
			p = p[len(macData):]
			continue
		case hasPrefixFold(p, "/Volumes/"):
			name, tail, _ := strings.Cut(p[len("/Volumes/"):], "/")
			if vol := "/Volumes/" + name; sameFile(vol, "/") || sameFile(vol, macData) {
				p = "/" + tail
				continue
			}
		}
		break
	}
	return filepath.Clean(p)
}

// homeFolders are this computer's home folders: the user's, and every one
// under the folders that hold homes.
func homeFolders() []string {
	var out []string
	if h, err := os.UserHomeDir(); err == nil && h != "" {
		out = append(out, canonicalPath(resolvePath(h)))
	}
	for _, parent := range []string{"/Users", "/home", "/var/home", "/export/home"} {
		entries, err := os.ReadDir(parent)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if e.IsDir() {
				out = append(out, canonicalPath(resolvePath(filepath.Join(parent, e.Name()))))
			}
		}
	}
	for _, h := range []string{"/root", "/var/root"} {
		if st, err := os.Stat(h); err == nil && st.IsDir() {
			out = append(out, canonicalPath(resolvePath(h)))
		}
	}
	return out
}

// samePath compares paths as this computer's disk does (case-insensitively
// on macOS and Windows).
func samePath(a, b string) bool {
	a, b = filepath.Clean(a), filepath.Clean(b)
	if runtime.GOOS == "darwin" || runtime.GOOS == "windows" {
		return strings.EqualFold(a, b)
	}
	return a == b
}

func hasPrefixFold(s, prefix string) bool {
	return len(s) >= len(prefix) && strings.EqualFold(s[:len(prefix)], prefix)
}

// below: inner is a folder below outer.
func below(inner, outer string) bool {
	outer = strings.TrimRight(filepath.Clean(outer), string(filepath.Separator)) + string(filepath.Separator)
	if runtime.GOOS == "darwin" || runtime.GOOS == "windows" {
		return hasPrefixFold(filepath.Clean(inner)+string(filepath.Separator), outer) && !samePath(inner, outer)
	}
	return strings.HasPrefix(filepath.Clean(inner)+string(filepath.Separator), outer) && filepath.Clean(inner) != filepath.Clean(outer)
}

// sameFile: a and b name the same folder (by device and inode).
func sameFile(a, b string) bool {
	x, err := os.Stat(a)
	if err != nil {
		return false
	}
	y, err := os.Stat(b)
	return err == nil && os.SameFile(x, y)
}
