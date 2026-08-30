package fmapp

// apply_patch, ported from pantheon/toolsets/file/apply_patch.py on the
// same engine: diff-match-patch (sergi/go-diff is the Go port of the
// library the Python side uses), so fuzzy application behaves alike.

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/sergi/go-diff/diffmatchpatch"
)

type fileOp struct {
	opType string // update | create | delete
	file   string
	patch  string
}

func detectPatchFormat(patch string) string {
	if strings.Contains(patch, "*** Begin Patch") ||
		strings.Contains(patch, "*** Update File:") ||
		strings.Contains(patch, "*** Create File:") {
		return "v4a"
	}
	return "unified"
}

var v4aMarkers = []struct {
	re *regexp.Regexp
	op string
}{
	{regexp.MustCompile(`^\*\*\* Update File:\s*(.+)`), "update"},
	{regexp.MustCompile(`^\*\*\* (?:Create|Add) File:\s*(.+)`), "create"},
	{regexp.MustCompile(`^\*\*\* (?:Delete|Remove) File:\s*(.+)`), "delete"},
}

func parseV4A(patch string) []fileOp {
	var ops []fileOp
	var curOp, curFile string
	var curContent []string
	flush := func() {
		if curOp != "" && curFile != "" {
			ops = append(ops, fileOp{curOp, curFile, strings.Join(curContent, "\n")})
		}
	}
	for _, line := range strings.Split(patch, "\n") {
		t := strings.TrimSpace(line)
		if t == "*** Begin Patch" || t == "*** End Patch" {
			continue
		}
		matched := false
		for _, m := range v4aMarkers {
			if g := m.re.FindStringSubmatch(line); g != nil {
				flush()
				if m.op == "delete" {
					ops = append(ops, fileOp{"delete", g[1], ""})
					curOp, curFile, curContent = "", "", nil
				} else {
					curOp, curFile, curContent = m.op, g[1], nil
				}
				matched = true
				break
			}
		}
		if !matched && curOp != "" {
			curContent = append(curContent, line)
		}
	}
	flush()
	return ops
}

var (
	plusHeader  = regexp.MustCompile(`(?m)^\+\+\+ (?:b/)?(.+?)(?:\t|$)`)
	minusHeader = regexp.MustCompile(`(?m)^--- (?:a/)?(.+?)(?:\t|$)`)
	fileSplit   = regexp.MustCompile(`(?m)^--- `)
)

func parseUnifiedMultiFile(patch, defaultFile string) []fileOp {
	var ops []fileOp
	// split keeping the "--- " prefix on each block
	idxs := fileSplit.FindAllStringIndex(patch, -1)
	var blocks []string
	if len(idxs) == 0 {
		blocks = []string{patch}
	} else {
		if idxs[0][0] > 0 {
			blocks = append(blocks, patch[:idxs[0][0]])
		}
		for i, loc := range idxs {
			end := len(patch)
			if i+1 < len(idxs) {
				end = idxs[i+1][0]
			}
			blocks = append(blocks, patch[loc[0]:end])
		}
	}
	for _, block := range blocks {
		block = strings.TrimSpace(block)
		if block == "" {
			continue
		}
		plus := plusHeader.FindStringSubmatch(block)
		minus := minusHeader.FindStringSubmatch(block)
		if plus != nil {
			isNew := minus != nil && minus[1] == "/dev/null"
			op := "update"
			if isNew {
				op = "create"
			}
			ops = append(ops, fileOp{op, plus[1], block})
		} else if minus != nil {
			ops = append(ops, fileOp{"update", minus[1], block})
		}
	}
	if len(ops) == 0 && defaultFile != "" {
		ops = append(ops, fileOp{"update", defaultFile, patch})
	}
	return ops
}

func extractCreateContent(content string) string {
	var out []string
	for _, line := range strings.Split(content, "\n") {
		switch {
		case strings.HasPrefix(line, "@@"):
		case strings.HasPrefix(line, "+"):
			out = append(out, line[1:])
		case strings.HasPrefix(line, "-"):
		case strings.HasPrefix(line, `\`):
		case strings.HasPrefix(line, " "):
			out = append(out, line[1:])
		case line != "":
			out = append(out, line)
		}
	}
	s := strings.Join(out, "\n")
	if s != "" && !strings.HasSuffix(s, "\n") {
		s += "\n"
	}
	return s
}

// parseDiffLines mirrors _parse_diff_lines.
func parseDiffLines(lines []string, skipHeaders bool) (string, string) {
	var oldC, newC []string
	for _, line := range lines {
		if skipHeaders && (strings.HasPrefix(line, "---") || strings.HasPrefix(line, "+++")) {
			continue
		}
		if strings.HasPrefix(line, "@@") {
			continue
		}
		switch {
		case strings.HasPrefix(line, "-"):
			oldC = append(oldC, line[1:]+"\n")
		case strings.HasPrefix(line, "+"):
			newC = append(newC, line[1:]+"\n")
		case strings.HasPrefix(line, " "):
			oldC = append(oldC, line[1:]+"\n")
			newC = append(newC, line[1:]+"\n")
		case line == "" && len(oldC) > 0 && len(newC) > 0:
			oldC = append(oldC, "\n")
			newC = append(newC, "\n")
		}
	}
	return strings.Join(oldC, ""), strings.Join(newC, "")
}

func patchesFromTexts(dmp *diffmatchpatch.DiffMatchPatch, oldText, newText string) []diffmatchpatch.Patch {
	if oldText == "" && newText == "" {
		return nil
	}
	diffs := dmp.DiffMain(oldText, newText, false)
	diffs = dmp.DiffCleanupSemantic(diffs)
	return dmp.PatchMake(oldText, diffs)
}

func convertToDMP(patchContent, format string) []diffmatchpatch.Patch {
	dmp := diffmatchpatch.New()
	if format == "v4a" {
		oldT, newT := parseDiffLines(strings.Split(patchContent, "\n"), false)
		return patchesFromTexts(dmp, oldT, newT)
	}
	// unified: hunks split on @@ markers
	var hunks [][]string
	var cur []string
	for _, line := range strings.Split(patchContent, "\n") {
		if strings.HasPrefix(line, "@@") {
			if len(cur) > 0 {
				hunks = append(hunks, cur)
			}
			cur = nil
		} else {
			cur = append(cur, line)
		}
	}
	if len(cur) > 0 {
		hunks = append(hunks, cur)
	}
	var all []diffmatchpatch.Patch
	for _, hunk := range hunks {
		oldT, newT := parseDiffLines(hunk, true)
		all = append(all, patchesFromTexts(dmp, oldT, newT)...)
	}
	return all
}

func opResult(file, action string, success bool, err string, extra map[string]any) map[string]any {
	r := map[string]any{"file": file, "action": action, "success": success}
	if err != "" {
		r["error"] = err
	}
	for k, v := range extra {
		r[k] = v
	}
	return r
}

// applyPatch is execute_patch_operations.
func (a *App) applyPatch(patch, filePath string, fuzzyThreshold float64) map[string]any {
	format := detectPatchFormat(patch)
	var ops []fileOp
	if format == "v4a" {
		ops = parseV4A(patch)
	} else {
		ops = parseUnifiedMultiFile(patch, filePath)
	}
	if len(ops) == 0 {
		return errResult("Failed to parse patch - no valid operations found. Check patch format and headers.")
	}

	var files []any
	var failed []any
	modified, created, deleted := 0, 0, 0
	for _, op := range ops {
		target := op.file
		if !filepath.IsAbs(target) {
			target = filepath.Join(a.root, op.file)
		}
		switch op.opType {
		case "delete":
			if _, err := os.Stat(target); err != nil {
				files = append(files, opResult(op.file, "delete", false, "File does not exist", nil))
				failed = append(failed, op.file)
			} else if err := os.Remove(target); err != nil {
				files = append(files, opResult(op.file, "delete", false, err.Error(), nil))
				failed = append(failed, op.file)
			} else {
				files = append(files, opResult(op.file, "delete", true, "", nil))
				deleted++
			}
		case "create":
			content := extractCreateContent(op.patch)
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				files = append(files, opResult(op.file, "create", false, err.Error(), nil))
				failed = append(failed, op.file)
				continue
			}
			if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
				files = append(files, opResult(op.file, "create", false, err.Error(), nil))
				failed = append(failed, op.file)
				continue
			}
			files = append(files, opResult(op.file, "create", true, "",
				map[string]any{"lines_added": strings.Count(content, "\n")}))
			created++
		default: // update
			res := a.applyUpdate(target, op.file, op.patch, format, fuzzyThreshold)
			if ok, _ := res["success"].(bool); ok {
				modified++
			} else {
				failed = append(failed, op.file)
			}
			files = append(files, res)
		}
	}
	total := modified + created + deleted
	allOK := len(failed) == 0
	var msg string
	if allOK {
		msg = "✓ Successfully processed " + itoa(total) + " file(s): " +
			itoa(modified) + " modified, " + itoa(created) + " created, " + itoa(deleted) + " deleted"
	} else {
		msg = "⚠ Partially completed: " + itoa(total-len(failed)) + "/" + itoa(total) +
			" succeeded, " + itoa(len(failed)) + " failed"
	}
	if failed == nil {
		failed = []any{}
	}
	return map[string]any{
		"success": allOK,
		"message": msg,
		"summary": map[string]any{
			"total_files": total, "modified": modified, "created": created,
			"deleted": deleted, "failed": len(failed),
		},
		"files":        files,
		"failed_files": failed,
	}
}

func (a *App) applyUpdate(target, name, patchContent, format string, fuzzy float64) map[string]any {
	data, err := os.ReadFile(target)
	if err != nil {
		return opResult(name, "update", false, "File does not exist", nil)
	}
	if looksBinary(data) {
		return opResult(name, "update", false, "File is not a valid text file", nil)
	}
	patches := convertToDMP(patchContent, format)
	if len(patches) == 0 {
		return opResult(name, "update", false, "No valid patches parsed",
			map[string]any{"hunks_applied": 0, "hunks_total": 0})
	}
	dmp := diffmatchpatch.New()
	dmp.MatchThreshold = fuzzy
	dmp.MatchDistance = 1000
	newText, results := dmp.PatchApply(patches, string(data))
	applied := 0
	for _, ok := range results {
		if ok {
			applied++
		}
	}
	if applied == 0 {
		return opResult(name, "update", false, "No hunks applied - content mismatch",
			map[string]any{"hunks_applied": 0, "hunks_total": len(results)})
	}
	if err := os.WriteFile(target, []byte(newText), 0o644); err != nil {
		return opResult(name, "update", false, err.Error(), nil)
	}
	return opResult(name, "update", true, "", map[string]any{
		"hunks_applied": applied, "hunks_total": len(results),
		"exact_match": applied == len(results),
	})
}

func itoa(n int) string { return strconv.Itoa(n) }
