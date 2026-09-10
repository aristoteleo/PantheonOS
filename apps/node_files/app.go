// Package nodefiles provides a portable filesystem App scoped to locally shared roots.
package nodefiles

import (
	"context"
	"crypto/rand"
	_ "embed"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/aristoteleo/pantheon-fleet/appsvc"
)

const MaxChunk = 256 * 1024
const maxHandles = 128
const handleTTL = 10 * time.Minute

//go:embed app.json
var manifest []byte

type sharedRoot struct {
	path string
	dir  *os.Root
}
type handle struct {
	file    *os.File
	write   bool
	touched time.Time
}
type App struct {
	roots   []sharedRoot
	node    string
	mu      sync.Mutex
	handles map[string]*handle
}

func NormalizeRoots(paths []string) ([]string, error) {
	out := []string{}
	seen := map[string]bool{}
	for _, path := range paths {
		if path == "~" || strings.HasPrefix(path, "~/") || strings.HasPrefix(path, `~\`) {
			home, err := os.UserHomeDir()
			if err != nil {
				return nil, err
			}
			path = filepath.Join(home, strings.TrimLeft(path[1:], `/\`))
		}
		if !filepath.IsAbs(path) {
			return nil, fmt.Errorf("shared folder must be absolute: %s", path)
		}
		resolved, err := filepath.EvalSymlinks(path)
		if err != nil {
			return nil, err
		}
		info, err := os.Stat(resolved)
		if err != nil {
			return nil, err
		}
		if !info.IsDir() {
			return nil, fmt.Errorf("shared folder is not a directory: %s", path)
		}
		if !seen[resolved] {
			out = append(out, resolved)
			seen[resolved] = true
		}
	}
	return out, nil
}

func New(paths []string, node string) (*App, error) {
	paths, err := NormalizeRoots(paths)
	if err != nil {
		return nil, err
	}
	if len(paths) == 0 {
		return nil, errors.New("no shared folders configured on this node")
	}
	app := &App{node: node, handles: map[string]*handle{}}
	for _, path := range paths {
		root, err := os.OpenRoot(path)
		if err != nil {
			app.Close()
			return nil, err
		}
		app.roots = append(app.roots, sharedRoot{path, root})
	}
	// Prefer the most specific root for overlapping shares.
	sort.SliceStable(app.roots, func(i, j int) bool { return len(app.roots[i].path) > len(app.roots[j].path) })
	return app, nil
}
func (a *App) Close() {
	a.mu.Lock()
	defer a.mu.Unlock()
	for id, h := range a.handles {
		h.file.Close()
		delete(a.handles, id)
	}
	for _, r := range a.roots {
		r.dir.Close()
	}
}
func str(p map[string]any, k string) string { s, _ := p[k].(string); return s }
func number(p map[string]any, k string, def int64) int64 {
	switch n := p[k].(type) {
	case float64:
		return int64(n)
	case int:
		return int64(n)
	case int64:
		return n
	}
	return def
}
func boolean(p map[string]any, k string) bool { b, _ := p[k].(bool); return b }
func ok() map[string]any                      { return map[string]any{"success": true} }
func (a *App) locate(path string) (*sharedRoot, string, error) {
	path = filepath.FromSlash(path)
	if !filepath.IsAbs(path) {
		return nil, "", errors.New("use an absolute path inside a shared folder")
	}
	for i := range a.roots {
		r := &a.roots[i]
		rel, err := filepath.Rel(r.path, path)
		if err == nil && (rel == "." || filepath.IsLocal(rel)) {
			return r, rel, nil
		}
	}
	// A configured root may have a platform alias (/var -> /private/var on macOS).
	// Resolve the nearest existing ancestor, then still open through os.Root.
	ancestor, suffix := path, ""
	for {
		resolved, err := filepath.EvalSymlinks(ancestor)
		if err == nil {
			canonical := filepath.Join(resolved, suffix)
			if canonical != path {
				for i := range a.roots {
					r := &a.roots[i]
					rel, err := filepath.Rel(r.path, canonical)
					if err == nil && (rel == "." || filepath.IsLocal(rel)) {
						return r, rel, nil
					}
				}
			}
			break
		}
		if !os.IsNotExist(err) || filepath.Dir(ancestor) == ancestor {
			break
		}
		suffix = filepath.Join(filepath.Base(ancestor), suffix)
		ancestor = filepath.Dir(ancestor)
	}
	return nil, "", errors.New("path is outside this node's shared folders; configure sharing on the node")
}
func regular(f *os.File) (os.FileInfo, error) {
	info, err := f.Stat()
	if err == nil && !info.Mode().IsRegular() {
		err = errors.New("only regular files can be read or written")
	}
	return info, err
}
func (a *App) open(path string, write, appendMode, overwrite bool) (*os.File, error) {
	r, rel, err := a.locate(path)
	if err != nil {
		return nil, err
	}
	if rel == "." {
		return nil, errors.New("cannot write or read a shared folder as a file")
	}
	flags := os.O_RDONLY
	if write {
		if err = r.dir.MkdirAll(filepath.Dir(rel), 0755); err != nil {
			return nil, err
		}
		flags = os.O_WRONLY | os.O_CREATE
		if appendMode {
			flags |= os.O_APPEND
		} else if !overwrite {
			flags |= os.O_EXCL
		}
	}
	// os.Root enforces the boundary during the actual open, including symlink races.
	if info, e := r.dir.Stat(rel); e == nil && !info.Mode().IsRegular() {
		return nil, errors.New("only regular files can be read or written")
	}
	f, err := r.dir.OpenFile(rel, flags, 0644)
	if err != nil {
		return nil, err
	}
	if _, err = regular(f); err != nil {
		f.Close()
		return nil, err
	}
	if write && !appendMode {
		if err = f.Truncate(0); err != nil {
			f.Close()
			return nil, err
		}
	}
	return f, nil
}
func entry(name, path string, info os.FileInfo) map[string]any {
	kind := "file"
	if info.IsDir() {
		kind = "directory"
	} else if !info.Mode().IsRegular() {
		kind = "other"
	}
	return map[string]any{"name": name, "path": filepath.ToSlash(path), "type": kind, "size": info.Size(), "last_modified": info.ModTime().Format("2006-01-02 15:04:05")}
}
func (a *App) list(path string) (any, error) {
	files := []map[string]any{}
	if (path == "" || path == "/") && !(len(a.roots) == 1 && a.roots[0].path == string(filepath.Separator)) {
		for _, r := range a.roots {
			info, err := r.dir.Stat(".")
			if err != nil {
				return nil, err
			}
			name := filepath.Base(r.path)
			for _, other := range a.roots {
				if other.path != r.path && filepath.Base(other.path) == name {
					name = filepath.ToSlash(r.path)
					break
				}
			}
			files = append(files, entry(name, r.path, info))
		}
	} else {
		r, rel, err := a.locate(path)
		if err != nil {
			return nil, err
		}
		d, err := r.dir.Open(rel)
		if err != nil {
			return nil, err
		}
		defer d.Close()
		entries, err := d.ReadDir(-1)
		if err != nil {
			return nil, err
		}
		for _, e := range entries {
			sub := filepath.Join(rel, e.Name())
			info, err := r.dir.Stat(sub)
			if err != nil {
				continue
			}
			files = append(files, entry(e.Name(), filepath.Join(r.path, sub), info))
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i]["name"].(string) < files[j]["name"].(string) })
	return map[string]any{"success": true, "files": files}, nil
}
func (a *App) stat(path string) (any, error) {
	r, rel, err := a.locate(path)
	if err != nil {
		return nil, err
	}
	info, err := r.dir.Stat(rel)
	if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	return map[string]any{"success": true, "exists": err == nil, "is_dir": info != nil && info.IsDir(), "node_id": a.node, "path": filepath.ToSlash(filepath.Join(r.path, rel)), "store_path": filepath.ToSlash(filepath.Join(r.path, rel))}, nil
}
func (a *App) mutate(method string, p map[string]any) (any, error) {
	path := str(p, "path")
	if method == "create_directory" {
		path = str(p, "sub_dir")
	}
	if method == "move_file" {
		path = str(p, "old_path")
	}
	r, rel, err := a.locate(path)
	if err != nil {
		return nil, err
	}
	if rel == "." {
		return nil, errors.New("shared folder roots cannot be renamed or deleted")
	}
	switch method {
	case "create_directory":
		err = r.dir.MkdirAll(rel, 0755)
	case "delete_path":
		if boolean(p, "recursive") {
			err = r.dir.RemoveAll(rel)
		} else {
			err = r.dir.Remove(rel)
		}
	case "move_file":
		dest := str(p, "new_path")
		r2, rel2, e := a.locate(dest)
		if e != nil {
			return nil, e
		}
		if r2 != r {
			return nil, errors.New("moving between shared roots requires copying the file")
		}
		if rel2 == "." {
			return nil, errors.New("cannot overwrite a shared folder")
		}
		if _, e = r.dir.Lstat(rel2); e == nil {
			return nil, errors.New("destination already exists")
		} else if !os.IsNotExist(e) {
			return nil, e
		}
		err = r.dir.Rename(rel, rel2)
	}
	return ok(), err
}
func (a *App) read(p map[string]any) (any, error) {
	if str(p, "symbol") != "" {
		return nil, errors.New("symbol analysis requires the Python file tools; use line ranges on this node")
	}
	f, err := a.open(str(p, "file_path"), false, false, false)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	limit := number(p, "max_chars", 200000)
	if limit <= 0 || limit > MaxChunk {
		limit = MaxChunk
	}
	data, err := io.ReadAll(io.LimitReader(f, int64(MaxChunk)+1))
	if err != nil {
		return nil, err
	}
	if !utf8.Valid(data) {
		return nil, errors.New("binary file; use chunked file transfer")
	}
	text := string(data)
	lines := strings.Split(text, "\n")
	start, end := number(p, "start_line", 1), number(p, "end_line", int64(len(lines)))
	if start < 1 || end < start {
		return nil, errors.New("invalid line range")
	}
	if start > int64(len(lines)) {
		text = ""
	} else {
		end = min(end, int64(len(lines)))
		text = strings.Join(lines[start-1:end], "\n")
	}
	chars := []rune(text)
	truncated := int64(len(chars)) > limit || len(data) > MaxChunk
	if int64(len(chars)) > limit {
		text = string(chars[:limit])
	}
	return map[string]any{"success": true, "content": text, "total_lines": len(lines), "truncated": truncated}, nil
}
func (a *App) write(p map[string]any) (any, error) {
	text := str(p, "content")
	if len(text) > MaxChunk {
		return nil, errors.New("text exceeds 256 KiB; use chunked file transfer")
	}
	overwrite := true
	if v, exists := p["overwrite"]; exists {
		overwrite, _ = v.(bool)
	}
	f, err := a.open(str(p, "file_path"), true, boolean(p, "append"), overwrite)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	_, err = f.WriteString(text)
	return ok(), err
}
func (a *App) transfer(method string, p map[string]any) (any, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for id, h := range a.handles {
		if time.Since(h.touched) > handleTTL {
			h.file.Close()
			delete(a.handles, id)
		}
	}
	switch method {
	case "open_file_for_read", "open_file_for_write":
		if len(a.handles) >= maxHandles {
			return nil, errors.New("too many open files; close unused transfers")
		}
		write := method == "open_file_for_write"
		f, err := a.open(str(p, "file_path"), write, false, true)
		if err != nil {
			return nil, err
		}
		info, err := f.Stat()
		if err != nil {
			f.Close()
			return nil, err
		}
		idBytes := make([]byte, 16)
		if _, err = rand.Read(idBytes); err != nil {
			f.Close()
			return nil, err
		}
		id := hex.EncodeToString(idBytes)
		a.handles[id] = &handle{f, write, time.Now()}
		return map[string]any{"success": true, "handle_id": id, "total_size": info.Size()}, nil
	case "close_file", "read_chunk_at", "read_chunk", "write_chunk":
		id := str(p, "handle_id")
		h := a.handles[id]
		if h == nil {
			return nil, errors.New("file handle is closed or expired")
		}
		h.touched = time.Now()
		if method == "close_file" {
			delete(a.handles, id)
			err := h.file.Close()
			return ok(), err
		}
		if method == "write_chunk" {
			if !h.write {
				return nil, errors.New("handle is read-only")
			}
			encoded := str(p, "data")
			if len(encoded) > base64.StdEncoding.EncodedLen(MaxChunk) {
				return nil, errors.New("chunk exceeds 256 KiB")
			}
			data, err := base64.StdEncoding.DecodeString(encoded)
			if err != nil {
				return nil, err
			}
			n, err := h.file.Write(data)
			return map[string]any{"success": true, "bytes_written": n}, err
		}
		if h.write {
			return nil, errors.New("handle is write-only")
		}
		size, offset := number(p, "size", 48*1024), number(p, "offset", 0)
		if size <= 0 || size > MaxChunk || offset < 0 {
			return nil, errors.New("invalid range: chunks must be 1–262144 bytes")
		}
		data := make([]byte, size)
		var n int
		var err error
		if method == "read_chunk_at" {
			n, err = h.file.ReadAt(data, offset)
		} else {
			offset, _ = h.file.Seek(0, io.SeekCurrent)
			n, err = h.file.Read(data)
		}
		if err != nil && err != io.EOF {
			return nil, err
		}
		info, err := h.file.Stat()
		if err != nil {
			return nil, err
		}
		return map[string]any{"success": true, "data": base64.StdEncoding.EncodeToString(data[:n]), "bytes_read": n, "offset": offset, "eof": offset+int64(n) >= info.Size()}, nil
	default:
		return nil, fmt.Errorf("file transfer method %q is not supported", method)
	}
}
func (a *App) Call(method string, p map[string]any) (any, error) {
	switch method {
	case "get_cwd":
		return map[string]any{"success": true, "cwd": "/"}, nil
	case "list_files":
		if boolean(p, "recursive") {
			return nil, errors.New("list one folder at a time")
		}
		return a.list(str(p, "sub_dir"))
	case "stat_path":
		return a.stat(str(p, "file_path"))
	case "create_directory", "delete_path", "move_file":
		return a.mutate(method, p)
	case "manage_path":
		copy := map[string]any{}
		for k, v := range p {
			copy[k] = v
		}
		switch str(p, "operation") {
		case "create_dir":
			copy["sub_dir"] = p["path"]
			return a.mutate("create_directory", copy)
		case "delete":
			return a.mutate("delete_path", copy)
		case "move":
			copy["old_path"] = p["path"]
			return a.mutate("move_file", copy)
		}
	case "read_file":
		return a.read(p)
	case "write_file":
		return a.write(p)
	case "file_transfer":
		args, _ := p["args"].(map[string]any)
		return a.transfer(str(p, "method"), args)
	}
	return nil, fmt.Errorf("file method %q is not supported", method)
}
func Tools(app *App) ([]*appsvc.Tool, error) {
	handlers := map[string]appsvc.Handler{}
	for _, name := range []string{"get_cwd", "list_files", "stat_path", "create_directory", "delete_path", "move_file", "manage_path", "read_file", "write_file", "file_transfer"} {
		handlers[name] = func(_ context.Context, p map[string]any) (any, error) {
			result, err := app.Call(name, p)
			if err != nil {
				return map[string]any{"success": false, "error": err.Error()}, nil
			}
			return result, nil
		}
	}
	return appsvc.ManifestTools(manifest, handlers)
}
