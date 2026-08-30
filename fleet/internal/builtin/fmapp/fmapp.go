// Package fmapp is the Go builtin implementation of the `file-manager` App
// — the fs core of pantheon/toolsets/file, mirrored tool-for-tool: read /
// write / update / apply_patch / glob / grep plus the hidden directory-
// management face. Deliberately NOT ported (python-bound, excluded from
// this surface by decision): view_file_outline and symbol reads
// (tree-sitter), the vision/PDF tools, image generation, LaTeX, and the
// Modal volume reload.
package fmapp

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Limits mirror the Python side's settings defaults.
const (
	maxReadChars     = 8 * 1024 * 1024 // _read_lines_bounded cap
	maxFileReadLines = 800             // settings.max_file_read_lines
	maxFileReadChars = 500000          // settings.max_file_read_chars
	maxGlobResults   = 100             // settings.max_glob_results
	maxGrepResults   = 100
)

// App is the file-manager rooted in one workspace directory.
type App struct {
	root string
}

func NewApp(root string) *App {
	if root == "" {
		root, _ = os.Getwd()
	}
	return &App{root: root}
}

// resolve mirrors _resolve_path: absolute passes through, ~ expands,
// relative resolves against the workspace root.
func (a *App) resolve(p string) string {
	if strings.HasPrefix(p, "~") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(strings.TrimPrefix(p, "~"), "/"))
		}
	}
	if filepath.IsAbs(p) {
		return p
	}
	return filepath.Join(a.root, p)
}

func errResult(format string, args ...any) map[string]any {
	return map[string]any{"success": false, "error": fmt.Sprintf(format, args...)}
}

// ---- read_file ------------------------------------------------------------

// splitKeepEnds mirrors str.splitlines(keepends=True) for \n endings.
func splitKeepEnds(s string) []string {
	if s == "" {
		return nil
	}
	var lines []string
	for {
		i := strings.IndexByte(s, '\n')
		if i < 0 {
			lines = append(lines, s)
			break
		}
		lines = append(lines, s[:i+1])
		s = s[i+1:]
		if s == "" {
			break
		}
	}
	return lines
}

func looksBinary(data []byte) bool {
	n := len(data)
	if n > 8192 {
		n = 8192
	}
	for _, b := range data[:n] {
		if b == 0 {
			return true
		}
	}
	return false
}

func (a *App) readFile(filePath string, startLine, endLine, maxChars int, symbol string) map[string]any {
	if symbol != "" {
		// Same answer the Python toolset gives when tree-sitter is absent.
		return errResult("Code navigation requires tree-sitter")
	}
	target := a.resolve(filePath)
	st, err := os.Stat(target)
	if err != nil {
		return errResult("File does not exist")
	}
	if st.IsDir() {
		return errResult("Path is not a file")
	}

	f, err := os.Open(target)
	if err != nil {
		return errResult("%s", err)
	}
	defer f.Close()
	buf := make([]byte, maxReadChars+1)
	n, _ := readFull(f, buf)
	truncatedBySize := n > maxReadChars
	if truncatedBySize {
		n = maxReadChars
	}
	data := buf[:n]
	if looksBinary(data) {
		return errResult("File is not a valid text file (binary or encoding issue)")
	}
	content := string(data)
	lines := splitKeepEnds(content)
	totalLines := len(lines)
	format := strings.ToLower(filepath.Ext(target))

	charLimit := maxFileReadChars
	if maxChars > 0 {
		charLimit = maxChars
	}

	if truncatedBySize {
		c := content
		if len(c) > charLimit {
			c = c[:charLimit]
		}
		return map[string]any{
			"success": true, "content": c, "total_lines": totalLines,
			"format": format, "truncated": true,
			"hint": fmt.Sprintf(
				"⚠️ File is too large to return in one reply; showing the first %d chars of its first %d+ lines. Page with start_line/end_line, or stream it with open_file_for_read + read_chunk_at.",
				len(c), totalLines),
		}
	}
	if totalLines == 0 {
		return map[string]any{"success": true, "content": "",
			"total_lines": 0, "format": "1-indexed"}
	}

	var selected string
	if startLine > 0 || endLine > 0 {
		startIdx := 0
		if startLine > 0 {
			startIdx = startLine - 1
		}
		endIdx := totalLines
		if endLine > 0 {
			endIdx = endLine
		}
		if startIdx < 0 {
			return errResult("start_line must be >= 1")
		}
		if startIdx >= totalLines {
			return errResult("start_line %d is out of range (file has %d lines)", startLine, totalLines)
		}
		if endIdx > totalLines {
			endIdx = totalLines
		}
		if startIdx >= endIdx {
			return errResult("start_line must be less than or equal to end_line")
		}
		selected = strings.Join(lines[startIdx:endIdx], "")
	} else {
		if totalLines > maxFileReadLines {
			selected = strings.Join(lines[:maxFileReadLines], "")
			return map[string]any{
				"success": true, "content": selected, "total_lines": totalLines,
				"format": format, "truncated": true,
				"hint": fmt.Sprintf("Showing first %d of %d lines. Use start_line/end_line to read more.",
					maxFileReadLines, totalLines),
			}
		}
		selected = content
	}

	if len(selected) > charLimit {
		return map[string]any{
			"success": true, "content": selected[:charLimit],
			"total_lines": totalLines, "format": format, "truncated": true,
			"hint": fmt.Sprintf(
				"⚠️ Content truncated: %d chars → %d chars (%.1f%% shown). Use other tools to read/process the full file.",
				len(selected), charLimit, float64(charLimit)/float64(len(selected))*100),
		}
	}
	return map[string]any{
		"success": true, "content": selected, "total_lines": totalLines,
		"format": format, "truncated": false,
	}
}

func readFull(f *os.File, buf []byte) (int, error) {
	total := 0
	for total < len(buf) {
		n, err := f.Read(buf[total:])
		total += n
		if err != nil {
			return total, nil
		}
	}
	return total, nil
}

// ---- write_file -----------------------------------------------------------

func (a *App) writeFile(filePath, content string, overwrite, appendMode bool) map[string]any {
	target := a.resolve(filePath)
	if appendMode {
		if _, err := os.Stat(target); err != nil {
			return map[string]any{
				"success": false,
				"error": fmt.Sprintf("File '%s' does not exist. Use write_file without append=True to create it first.",
					filePath),
				"reason": "file_not_found",
			}
		}
		f, err := os.OpenFile(target, os.O_APPEND|os.O_WRONLY, 0o644)
		if err != nil {
			return errResult("%s", err)
		}
		defer f.Close()
		if _, err := f.WriteString(content); err != nil {
			return errResult("%s", err)
		}
		return map[string]any{"success": true, "appended_chars": len(content)}
	}
	if !overwrite {
		if _, err := os.Stat(target); err == nil {
			return map[string]any{"success": false, "error": "File already exists",
				"reason": "overwrite_disabled"}
		}
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return errResult("%s", err)
	}
	if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
		return errResult("%s", err)
	}
	return map[string]any{"success": true, "overwritten": overwrite}
}

// ---- update_file ----------------------------------------------------------

// replaceInContent mirrors _replace_in_content.
func replaceInContent(content, oldS, newS string, replaceAll bool, startLine, endLine int) (string, int, string) {
	if startLine > 0 || endLine > 0 {
		lines := splitKeepEnds(content)
		startIdx := 0
		if startLine > 0 {
			startIdx = startLine - 1
		}
		endIdx := len(lines)
		if endLine > 0 {
			endIdx = endLine
		}
		if startIdx < 0 || startIdx >= len(lines) {
			return "", 0, fmt.Sprintf("start_line %d is out of range (file has %d lines)", startLine, len(lines))
		}
		if endIdx > len(lines) {
			return "", 0, fmt.Sprintf("end_line %d is out of range (file has %d lines)", endLine, len(lines))
		}
		if startIdx >= endIdx {
			return "", 0, "start_line must be less than end_line"
		}
		before := strings.Join(lines[:startIdx], "")
		section := strings.Join(lines[startIdx:endIdx], "")
		after := strings.Join(lines[endIdx:], "")
		count := strings.Count(section, oldS)
		if count == 0 {
			return "", 0, fmt.Sprintf("old_string not found in lines %d-%d", startLine, endLine)
		}
		if count > 1 && !replaceAll {
			return "", 0, fmt.Sprintf(
				"old_string found %d times in lines %d-%d. Set replace_all=True or narrow the line range.",
				count, startLine, endLine)
		}
		if replaceAll {
			return before + strings.ReplaceAll(section, oldS, newS) + after, count, ""
		}
		return before + strings.Replace(section, oldS, newS, 1) + after, 1, ""
	}
	count := strings.Count(content, oldS)
	if count == 0 {
		return "", 0, "old_string not found in file"
	}
	if count > 1 && !replaceAll {
		return "", 0, fmt.Sprintf(
			"old_string found %d times. Set replace_all=True or use start_line/end_line to target specific occurrence.",
			count)
	}
	if replaceAll {
		return strings.ReplaceAll(content, oldS, newS), count, ""
	}
	return strings.Replace(content, oldS, newS, 1), 1, ""
}

func (a *App) updateFile(filePath, oldS, newS string, replaceAll bool, startLine, endLine int) map[string]any {
	target := a.resolve(filePath)
	st, err := os.Stat(target)
	if err != nil {
		return errResult("File does not exist")
	}
	if st.IsDir() {
		return errResult("Path is not a file")
	}
	data, err := os.ReadFile(target)
	if err != nil {
		return errResult("%s", err)
	}
	if looksBinary(data) {
		return errResult("File is not a valid text file")
	}
	newContent, replacements, errMsg := replaceInContent(
		string(data), oldS, newS, replaceAll, startLine, endLine)
	if errMsg != "" {
		return errResult("%s", errMsg)
	}
	if err := os.WriteFile(target, []byte(newContent), st.Mode().Perm()); err != nil {
		return errResult("%s", err)
	}
	return map[string]any{"success": true, "replacements": replacements}
}

// ---- directory management -------------------------------------------------

func entryType(path string, info os.FileInfo) string {
	if info.Mode()&os.ModeSymlink != 0 {
		return "symlink"
	}
	if info.IsDir() {
		return "directory"
	}
	if info.Mode().IsRegular() {
		return "file"
	}
	return "other"
}

func (a *App) listFiles(subDir string, recursive bool, maxDepth int) map[string]any {
	target := a.root
	if subDir != "" {
		target = a.resolve(subDir)
	}
	st, err := os.Stat(target)
	if err != nil || !st.IsDir() {
		return errResult("Directory does not exist")
	}
	if !recursive {
		entries, err := os.ReadDir(target)
		if err != nil {
			return errResult("%s", err)
		}
		files := []any{}
		for _, e := range entries {
			info, err := os.Lstat(filepath.Join(target, e.Name()))
			if err != nil {
				continue
			}
			t := entryType(target, info)
			size := info.Size()
			if t == "directory" {
				size = 0
			}
			files = append(files, map[string]any{
				"name": e.Name(), "size": size, "type": t,
				"last_modified": info.ModTime().Format("2006-01-02 15:04:05"),
			})
		}
		return map[string]any{"success": true, "files": files}
	}
	var walk func(path string, depth int) map[string]any
	walk = func(path string, depth int) map[string]any {
		info, err := os.Lstat(path)
		if err != nil {
			return nil
		}
		t := entryType(path, info)
		size := info.Size()
		if t == "directory" {
			size = 0
		}
		node := map[string]any{"name": filepath.Base(path), "type": t, "size": size}
		if t == "directory" {
			children := []any{}
			if depth < maxDepth {
				entries, err := os.ReadDir(path)
				if err == nil {
					names := make([]string, 0, len(entries))
					for _, e := range entries {
						names = append(names, e.Name())
					}
					sort.Strings(names)
					for _, name := range names {
						if child := walk(filepath.Join(path, name), depth+1); child != nil {
							children = append(children, child)
						}
					}
				}
			}
			node["children"] = children
		}
		return node
	}
	return map[string]any{"success": true, "tree": walk(target, 0)}
}

func (a *App) createDirectory(subDir any) map[string]any {
	switch v := subDir.(type) {
	case string:
		if err := os.MkdirAll(a.resolve(v), 0o755); err != nil {
			return errResult("%s", err)
		}
		return map[string]any{"success": true}
	case []any:
		results := []any{}
		all := true
		for _, item := range v {
			p, _ := item.(string)
			if err := os.MkdirAll(a.resolve(p), 0o755); err != nil {
				results = append(results, map[string]any{"path": p, "success": false, "error": err.Error()})
				all = false
			} else {
				results = append(results, map[string]any{"path": p, "success": true})
			}
		}
		return map[string]any{"success": all, "results": results}
	}
	return errResult("sub_dir must be a string or list of strings")
}

func (a *App) deleteOne(rel string, recursive bool) map[string]any {
	target := a.resolve(rel)
	st, err := os.Lstat(target)
	if err != nil {
		return map[string]any{"path": rel, "success": false, "error": "Path does not exist"}
	}
	if st.IsDir() {
		if recursive {
			err = os.RemoveAll(target)
		} else {
			err = os.Remove(target)
		}
	} else {
		err = os.Remove(target)
	}
	if err != nil {
		return map[string]any{"path": rel, "success": false, "error": err.Error()}
	}
	return map[string]any{"path": rel, "success": true}
}

func (a *App) deletePath(path any, recursive bool) map[string]any {
	switch v := path.(type) {
	case string:
		return a.deleteOne(v, recursive)
	case []any:
		results := []any{}
		all := true
		for _, item := range v {
			p, _ := item.(string)
			r := a.deleteOne(p, recursive)
			if ok, _ := r["success"].(bool); !ok {
				all = false
			}
			results = append(results, r)
		}
		return map[string]any{"success": all, "results": results}
	}
	return errResult("path must be a string or list of strings")
}

func (a *App) moveFile(oldPath, newPath string) map[string]any {
	src := a.resolve(oldPath)
	if _, err := os.Lstat(src); err != nil {
		return errResult("Old path does not exist")
	}
	dst := a.resolve(newPath)
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return errResult("%s", err)
	}
	if err := os.Rename(src, dst); err != nil {
		return errResult("%s", err)
	}
	return map[string]any{"success": true}
}

func (a *App) managePath(operation, path, newPath string, recursive bool) map[string]any {
	switch operation {
	case "create_dir":
		return a.createDirectory(path)
	case "delete":
		return a.deletePath(path, recursive)
	case "move":
		if newPath == "" {
			return errResult("new_path is required for 'move' operation")
		}
		return a.moveFile(path, newPath)
	}
	return errResult("Invalid operation '%s'. Must be one of: create_dir, delete, move", operation)
}

func (a *App) getCwd() map[string]any {
	return map[string]any{"success": true, "cwd": a.root}
}

func (a *App) getHomeDir() map[string]any {
	home, err := os.UserHomeDir()
	if err != nil {
		return errResult("%s", err)
	}
	return map[string]any{"success": true, "home": home}
}

// isoTime formats mtime the way build_file_info does (datetime.isoformat).
func isoTime(t time.Time) string {
	return t.Format("2006-01-02T15:04:05.999999")
}
