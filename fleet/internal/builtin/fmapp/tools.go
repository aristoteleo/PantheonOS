package fmapp

import (
	"context"

	"github.com/aristoteleo/pantheon-fleet/internal/appsvc"
)

func strP(p map[string]any, name string) string {
	if v, ok := p[name].(string); ok {
		return v
	}
	return ""
}

func intP(p map[string]any, name string, def int) int {
	switch v := p[name].(type) {
	case float64:
		return int(v)
	case int:
		return v
	}
	return def
}

func boolP(p map[string]any, name string, def bool) bool {
	if v, ok := p[name].(bool); ok {
		return v
	}
	return def
}

func floatP(p map[string]any, name string, def float64) float64 {
	switch v := p[name].(type) {
	case float64:
		return v
	case int:
		return float64(v)
	}
	return def
}

func strListP(p map[string]any, name string) []string {
	switch v := p[name].(type) {
	case []any:
		out := make([]string, 0, len(v))
		for _, item := range v {
			if s, ok := item.(string); ok {
				out = append(out, s)
			}
		}
		return out
	case string: // a lone pattern string, coerced like the Python side does
		return []string{v}
	}
	return nil
}

func req(name, typ string) appsvc.Param {
	return appsvc.Param{Type: typ, Range: nil, Default: appsvc.NotDefined, Name: name, Doc: nil}
}
func opt(name, typ string, def any) appsvc.Param {
	return appsvc.Param{Type: typ, Range: nil, Default: def, Name: name, Doc: nil}
}

// Tools returns the file-manager surface this builtin serves: the fs core
// of the Python FileManagerToolSet, signatures mirrored from the committed
// file-manager.app.json. Not served (python-bound, by decision):
// view_file_outline + read_file's symbol mode (tree-sitter — symbol answers
// the same "requires tree-sitter" error the Python side gives without the
// library), the vision/PDF tools, generate_image, compile_latex, the
// fetch_* frontend helpers, and reload_workspace_volume.
func Tools(app *App) []*appsvc.Tool {
	return []*appsvc.Tool{
		{
			Name: "read_file",
			Doc:  "Read the contents of a text file.",
			Inputs: []appsvc.Param{
				req("file_path", "str"),
				opt("start_line", "int | None", nil),
				opt("end_line", "int | None", nil),
				opt("max_chars", "int | None", nil),
				opt("symbol", "str | None", nil),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.readFile(strP(p, "file_path"), intP(p, "start_line", 0),
					intP(p, "end_line", 0), intP(p, "max_chars", 0), strP(p, "symbol")), nil
			},
		},
		{
			Name: "write_file",
			Doc:  "Create a new file, overwrite an existing one, or append to it.",
			Inputs: []appsvc.Param{
				req("file_path", "str"),
				opt("content", "str", ""),
				opt("overwrite", "bool", true),
				opt("append", "bool", false),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.writeFile(strP(p, "file_path"), strP(p, "content"),
					boolP(p, "overwrite", true), boolP(p, "append", false)), nil
			},
		},
		{
			Name: "update_file",
			Doc:  "Edit an existing file by exact string replacement.",
			Inputs: []appsvc.Param{
				req("file_path", "str"),
				req("old_string", "str"),
				req("new_string", "str"),
				opt("replace_all", "bool", false),
				opt("start_line", "int | None", nil),
				opt("end_line", "int | None", nil),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.updateFile(strP(p, "file_path"), strP(p, "old_string"),
					strP(p, "new_string"), boolP(p, "replace_all", false),
					intP(p, "start_line", 0), intP(p, "end_line", 0)), nil
			},
		},
		{
			Name: "apply_patch",
			Doc:  "Apply patches to files with fuzzy matching support.",
			Inputs: []appsvc.Param{
				req("patch", "str"),
				opt("file_path", "str | None", nil),
				opt("fuzzy_threshold", "float", 0.5),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.applyPatch(strP(p, "patch"), strP(p, "file_path"),
					floatP(p, "fuzzy_threshold", 0.5)), nil
			},
		},
		{
			Name: "glob",
			Doc:  "Search for files and subdirectories using glob patterns.",
			Inputs: []appsvc.Param{
				req("pattern", "str"),
				opt("path", "str | None", nil),
				opt("respect_git_ignore", "bool", true),
				opt("type_filter", "typing.Optional[typing.Literal['file', 'directory', 'any']]", nil),
				opt("excludes", "list[str] | None", nil),
				opt("max_depth", "int | None", nil),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.glob(strP(p, "pattern"), strP(p, "path"),
					boolP(p, "respect_git_ignore", true), strP(p, "type_filter"),
					strListP(p, "excludes"), intP(p, "max_depth", 0)), nil
			},
		},
		{
			Name: "grep",
			Doc:  "Search for text patterns within file contents.",
			Inputs: []appsvc.Param{
				req("pattern", "str"),
				opt("path", "str | None", nil),
				opt("file_pattern", "str | None", nil),
				opt("context_lines", "int", 0),
				opt("case_sensitive", "bool", false),
				opt("respect_git_ignore", "bool", true),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.grep(strP(p, "pattern"), strP(p, "path"),
					strP(p, "file_pattern"), intP(p, "context_lines", 0),
					boolP(p, "case_sensitive", false),
					boolP(p, "respect_git_ignore", true)), nil
			},
		},
		{
			Name:   "list_files",
			Doc:    "List files and directories in the workspace.",
			Hidden: true,
			Inputs: []appsvc.Param{
				opt("sub_dir", "str | None", nil),
				opt("recursive", "bool", false),
				opt("max_depth", "int", 5),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.listFiles(strP(p, "sub_dir"),
					boolP(p, "recursive", false), intP(p, "max_depth", 5)), nil
			},
		},
		{
			Name:   "create_directory",
			Doc:    "Create one or more directories.",
			Hidden: true,
			Inputs: []appsvc.Param{req("sub_dir", "str | list[str]")},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.createDirectory(p["sub_dir"]), nil
			},
		},
		{
			Name:   "delete_path",
			Doc:    "Delete files or directories with optional recursion.",
			Hidden: true,
			Inputs: []appsvc.Param{
				req("path", "str | list[str]"),
				opt("recursive", "bool", false),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.deletePath(p["path"], boolP(p, "recursive", false)), nil
			},
		},
		{
			Name:   "move_file",
			Doc:    "Move or rename a file.",
			Hidden: true,
			Inputs: []appsvc.Param{req("old_path", "str"), req("new_path", "str")},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.moveFile(strP(p, "old_path"), strP(p, "new_path")), nil
			},
		},
		{
			Name:   "manage_path",
			Doc:    "Unified tool for managing files and directories.",
			Hidden: true,
			Inputs: []appsvc.Param{
				req("operation", "str"),
				req("path", "str"),
				opt("new_path", "str | None", nil),
				opt("recursive", "bool", false),
			},
			Handler: func(_ context.Context, p map[string]any) (any, error) {
				return app.managePath(strP(p, "operation"), strP(p, "path"),
					strP(p, "new_path"), boolP(p, "recursive", false)), nil
			},
		},
		{
			Name:   "get_cwd",
			Doc:    "Get current working directory.",
			Hidden: true,
			Inputs: []appsvc.Param{},
			Handler: func(_ context.Context, _ map[string]any) (any, error) {
				return app.getCwd(), nil
			},
		},
		{
			Name:   "get_home_dir",
			Doc:    "Return the runtime user home directory.",
			Hidden: true,
			Inputs: []appsvc.Param{},
			Handler: func(_ context.Context, _ map[string]any) (any, error) {
				return app.getHomeDir(), nil
			},
		},
	}
}
