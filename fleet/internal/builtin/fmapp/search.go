package fmapp

// glob + grep, mirroring pantheon/toolsets/file/grep_glob.py: shell out to
// fd / rg when the node has them (same preference the Python side has),
// pure-Go fallback otherwise.

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/bmatcuk/doublestar/v4"
)

var gitignoreNames = map[string]bool{
	".git": true, "node_modules": true, "__pycache__": true,
	"dist": true, "build": true, ".venv": true, ".pytest_cache": true,
}

func shouldIgnore(rel string) bool {
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		if strings.HasPrefix(part, ".") || gitignoreNames[part] {
			return true
		}
	}
	return false
}

func (a *App) searchDir(path string) (string, map[string]any) {
	dir := a.root
	if path != "" {
		dir = a.resolve(path)
	}
	st, err := os.Stat(dir)
	if err != nil {
		which := path
		if which == "" {
			which = "workspace root"
		}
		return "", errResult("Directory does not exist: %s", which)
	}
	if !st.IsDir() {
		return "", errResult("Path is not a directory: %s", path)
	}
	if resolved, err := filepath.Abs(dir); err == nil && resolved == "/" {
		return "", errResult("Cannot glob at root directory. Please specify a more specific path.")
	}
	return dir, nil
}

func fileInfoEntry(path string, root string) map[string]any {
	info, err := os.Lstat(path)
	if err != nil {
		return nil
	}
	rel, err := filepath.Rel(root, path)
	if err != nil || strings.HasPrefix(rel, "..") {
		rel = path
	}
	var t string
	if info.Mode()&os.ModeSymlink != 0 {
		if _, err := os.Stat(path); err == nil {
			t = "symlink"
		} else {
			t = "symlink (broken)"
		}
	} else if info.IsDir() {
		t = "directory"
	} else {
		t = "file"
	}
	return map[string]any{
		"path": rel, "name": filepath.Base(path), "size": info.Size(),
		"modified": isoTime(info.ModTime()), "type": t,
	}
}

// ---- glob -----------------------------------------------------------------

func runFd(pattern, dir, root string, respectGitIgnore bool, typeFilter string, excludes []string, maxDepth int) ([]map[string]any, error) {
	cmd := []string{"fd", "--glob", pattern}
	switch typeFilter {
	case "file":
		cmd = append(cmd, "--type", "f")
	case "directory":
		cmd = append(cmd, "--type", "d")
	}
	for _, e := range excludes {
		cmd = append(cmd, "--exclude", e)
	}
	if maxDepth > 0 {
		cmd = append(cmd, "--max-depth", strconv.Itoa(maxDepth))
	}
	if !respectGitIgnore {
		cmd = append(cmd, "--no-ignore", "--hidden")
	}
	cmd = append(cmd, dir)
	out, err := exec.Command(cmd[0], cmd[1:]...).Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok && ee.ExitCode() == 1 {
			// no matches
		} else {
			return nil, err
		}
	}
	files := []map[string]any{}
	sc := bufio.NewScanner(bytes.NewReader(out))
	sc.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for sc.Scan() {
		line := strings.TrimSuffix(sc.Text(), "/")
		if line == "" {
			continue
		}
		if e := fileInfoEntry(line, root); e != nil {
			files = append(files, e)
		}
	}
	return files, nil
}

func globFallback(pattern, dir, root string, respectGitIgnore bool, typeFilter string, excludes []string, maxDepth int) ([]map[string]any, error) {
	globPattern := pattern
	if !strings.Contains(pattern, "**") {
		globPattern = "**/" + pattern
	}
	fsys := os.DirFS(dir)
	matches, err := doublestar.Glob(fsys, globPattern)
	if err != nil {
		return nil, err
	}
	files := []map[string]any{}
	for _, rel := range matches {
		full := filepath.Join(dir, rel)
		info, err := os.Lstat(full)
		if err != nil {
			continue
		}
		isDir := info.IsDir()
		isLink := info.Mode()&os.ModeSymlink != 0
		isFile := info.Mode().IsRegular() || isLink
		if respectGitIgnore && shouldIgnore(rel) {
			continue
		}
		switch typeFilter {
		case "file":
			if !isFile {
				continue
			}
		case "directory":
			if !isDir {
				continue
			}
		case "any":
		default: // None → files (and symlinks) only, backward compat
			if !isFile {
				continue
			}
		}
		if maxDepth > 0 && len(strings.Split(rel, "/")) > maxDepth {
			continue
		}
		excluded := false
		for _, ex := range excludes {
			if ok, _ := doublestar.PathMatch(ex, rel); ok {
				excluded = true
				break
			}
			if ok, _ := doublestar.PathMatch(ex, filepath.Base(rel)); ok {
				excluded = true
				break
			}
		}
		if excluded {
			continue
		}
		if e := fileInfoEntry(full, root); e != nil {
			files = append(files, e)
		}
	}
	sort.Slice(files, func(i, j int) bool {
		return files[i]["path"].(string) < files[j]["path"].(string)
	})
	return files, nil
}

func (a *App) glob(pattern, path string, respectGitIgnore bool, typeFilter string, excludes []string, maxDepth int) map[string]any {
	dir, errRes := a.searchDir(path)
	if errRes != nil {
		return errRes
	}
	var files []map[string]any
	var err error
	if _, lookErr := exec.LookPath("fd"); lookErr == nil {
		files, err = runFd(pattern, dir, a.root, respectGitIgnore, typeFilter, excludes, maxDepth)
	} else {
		err = fmt.Errorf("fd not available")
	}
	if err != nil {
		files, err = globFallback(pattern, dir, a.root, respectGitIgnore, typeFilter, excludes, maxDepth)
		if err != nil {
			return errResult("%s", err)
		}
	}
	total := len(files)
	capped := false
	msg := fmt.Sprintf("Found %d file(s) matching '%s'", total, pattern)
	list := files
	if total > maxGlobResults {
		list = files[:maxGlobResults]
		capped = true
		msg = fmt.Sprintf("Results capped at %d. Total matches: %d. Refine pattern to narrow results.",
			maxGlobResults, total)
	}
	out := make([]any, len(list))
	for i, f := range list {
		out[i] = f
	}
	var tf any
	if typeFilter != "" {
		tf = typeFilter
	}
	var exc any
	if excludes != nil {
		exc = excludes
	}
	var md any
	if maxDepth > 0 {
		md = maxDepth
	}
	return map[string]any{
		"success": true, "files": out, "total": total, "pattern": pattern,
		"message": msg, "capped": capped,
		"filters_applied": map[string]any{"type": tf, "excludes": exc, "max_depth": md},
	}
}

// ---- grep -----------------------------------------------------------------

func runRipgrep(pattern, searchPath, root, filePattern string, contextLines int, caseSensitive, respectGitIgnore bool) ([]map[string]any, int, error) {
	cmd := []string{"rg", "--json", pattern}
	if !caseSensitive {
		cmd = append(cmd, "--ignore-case")
	}
	if contextLines > 0 {
		cmd = append(cmd, "-C", strconv.Itoa(contextLines))
	}
	if !respectGitIgnore {
		cmd = append(cmd, "--no-ignore", "--hidden")
	}
	if filePattern != "" {
		cmd = append(cmd, "--glob", filePattern)
	}
	cmd = append(cmd, "--max-count", strconv.Itoa(maxGrepResults), searchPath)
	out, err := exec.Command(cmd[0], cmd[1:]...).Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); !ok || ee.ExitCode() != 1 {
			return nil, 0, err
		}
	}
	matches := []map[string]any{}
	filesMatched := map[string]bool{}
	var pendingBefore []string
	sc := bufio.NewScanner(bytes.NewReader(out))
	sc.Buffer(make([]byte, 64*1024), 8*1024*1024)
	for sc.Scan() {
		var msg struct {
			Type string `json:"type"`
			Data struct {
				Path       struct{ Text string } `json:"path"`
				LineNumber int                   `json:"line_number"`
				Lines      struct{ Text string } `json:"lines"`
				Submatches []struct {
					Start int `json:"start"`
				} `json:"submatches"`
			} `json:"data"`
		}
		if json.Unmarshal(sc.Bytes(), &msg) != nil {
			continue
		}
		switch msg.Type {
		case "context":
			line := strings.TrimRight(msg.Data.Lines.Text, "\n")
			if contextLines > 0 {
				if len(matches) > 0 {
					last := matches[len(matches)-1]
					if after, ok := last["context_after"].([]string); ok && len(after) < contextLines {
						last["context_after"] = append(after, line)
						continue
					}
				}
				pendingBefore = append(pendingBefore, line)
			}
		case "match":
			rel, err := filepath.Rel(root, msg.Data.Path.Text)
			if err != nil || strings.HasPrefix(rel, "..") {
				rel = msg.Data.Path.Text
			}
			filesMatched[rel] = true
			col := 1
			if len(msg.Data.Submatches) > 0 {
				col = msg.Data.Submatches[0].Start + 1
			}
			m := map[string]any{
				"file": rel, "line_number": msg.Data.LineNumber,
				"line_content": strings.TrimRight(msg.Data.Lines.Text, "\n"),
				"column":       col,
			}
			if contextLines > 0 {
				before := pendingBefore
				if len(before) > contextLines {
					before = before[len(before)-contextLines:]
				}
				if before == nil {
					before = []string{}
				}
				m["context_before"] = before
				m["context_after"] = []string{}
				pendingBefore = nil
			}
			matches = append(matches, m)
		}
	}
	return matches, len(filesMatched), nil
}

func grepFallback(pattern, searchPath, root, filePattern string, contextLines int, caseSensitive, respectGitIgnore bool) ([]map[string]any, int, bool, error) {
	flags := ""
	if !caseSensitive {
		flags = "(?i)"
	}
	re, err := regexp.Compile(flags + pattern)
	if err != nil {
		return nil, 0, false, fmt.Errorf("Invalid regex pattern: %s", err)
	}
	var candidates []string
	st, err := os.Stat(searchPath)
	if err == nil && !st.IsDir() {
		candidates = []string{searchPath}
	} else if filePattern != "" {
		fsys := os.DirFS(searchPath)
		rels, _ := doublestar.Glob(fsys, filePattern)
		for _, rel := range rels {
			candidates = append(candidates, filepath.Join(searchPath, rel))
		}
	} else {
		_ = filepath.WalkDir(searchPath, func(p string, d os.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if !d.IsDir() {
				candidates = append(candidates, p)
			}
			return nil
		})
	}
	matches := []map[string]any{}
	filesMatched := map[string]bool{}
	for _, file := range candidates {
		info, err := os.Stat(file)
		if err != nil || info.IsDir() {
			continue
		}
		if rel, err := filepath.Rel(searchPath, file); err == nil &&
			respectGitIgnore && shouldIgnore(rel) {
			continue
		}
		data, err := os.ReadFile(file)
		if err != nil || looksBinary(data) {
			continue
		}
		lines := strings.Split(string(data), "\n")
		for i, line := range lines {
			loc := re.FindStringIndex(line)
			if loc == nil {
				continue
			}
			rel, err := filepath.Rel(root, file)
			if err != nil || strings.HasPrefix(rel, "..") {
				rel = file
			}
			filesMatched[rel] = true
			m := map[string]any{
				"file": rel, "line_number": i + 1,
				"line_content": strings.TrimRight(line, "\n"),
				"column":       loc[0] + 1,
			}
			if contextLines > 0 {
				start := i - contextLines
				if start < 0 {
					start = 0
				}
				end := i + contextLines + 1
				if end > len(lines) {
					end = len(lines)
				}
				m["context_before"] = lines[start:i]
				m["context_after"] = lines[i+1 : end]
			}
			matches = append(matches, m)
			if len(matches) >= maxGrepResults {
				return matches, len(filesMatched), true, nil
			}
		}
	}
	return matches, len(filesMatched), false, nil
}

func (a *App) grep(pattern, path, filePattern string, contextLines int, caseSensitive, respectGitIgnore bool) map[string]any {
	searchPath := a.root
	if path != "" {
		searchPath = a.resolve(path)
	}
	if _, err := os.Stat(searchPath); err != nil {
		which := path
		if which == "" {
			which = "workspace root"
		}
		return errResult("Path does not exist: %s", which)
	}
	if resolved, err := filepath.Abs(searchPath); err == nil && resolved == "/" {
		return errResult("Cannot grep at root directory. Please specify a more specific path.")
	}
	var matches []map[string]any
	var filesMatched int
	capped := false
	var err error
	if _, lookErr := exec.LookPath("rg"); lookErr == nil {
		matches, filesMatched, err = runRipgrep(
			pattern, searchPath, a.root, filePattern, contextLines, caseSensitive, respectGitIgnore)
	} else {
		err = fmt.Errorf("ripgrep not available")
	}
	if err != nil {
		matches, filesMatched, capped, err = grepFallback(
			pattern, searchPath, a.root, filePattern, contextLines, caseSensitive, respectGitIgnore)
		if err != nil {
			return errResult("%s", err)
		}
	}
	out := make([]any, len(matches))
	for i, m := range matches {
		out[i] = m
	}
	msg := fmt.Sprintf("Found %d match(es) in %d file(s)", len(matches), filesMatched)
	res := map[string]any{
		"success": true, "matches": out, "total_matches": len(matches),
		"files_matched": filesMatched, "pattern": pattern, "message": msg,
	}
	if capped {
		res["capped"] = true
		res["message"] = msg + fmt.Sprintf(" (search terminated early at %d matches)", maxGrepResults)
	}
	return res
}
