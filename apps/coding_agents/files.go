package codingagentsapp

import (
	"bytes"
	"encoding/base64"
	"mime"
	"os"
	"path/filepath"
)

// fileChunkMax keeps a reply under the size NATS carries without stalling.
const fileChunkMax = 48 * 1024

// conversationFile reads a piece of a file a conversation worked with, so
// the canvas can show what the agent read or made wherever it lies (the
// node's file service reaches only its shared folders). A file qualifies
// when it is in the conversation's folder, or when the conversation named
// it: its path, or its name and its folder (`cd dir && cat name`).
func (a *App) conversationFile(id, path string, offset, length int64) map[string]any {
	a.mu.Lock()
	c := a.convs[id]
	cwd := ""
	if c != nil {
		cwd = c.Cwd
	}
	a.mu.Unlock()
	if c == nil {
		return failure("No conversation %s on this node", id)
	}
	if !filepath.IsAbs(path) {
		return failure("%s is not an absolute path", path)
	}
	path = filepath.Clean(path)
	if !within(cwd, path) {
		a.logMu.Lock()
		log, err := os.ReadFile(a.logPath(id))
		a.logMu.Unlock()
		named := err == nil && (bytes.Contains(log, []byte(path)) ||
			bytes.Contains(log, []byte(filepath.Base(path))) && bytes.Contains(log, []byte(filepath.Dir(path))))
		if !named {
			return failure("%s is not a file this conversation worked with", path)
		}
	}
	info, err := os.Stat(path)
	if err != nil {
		return failure("%v", err)
	}
	if !info.Mode().IsRegular() {
		return failure("%s is not a file", path)
	}
	f, err := os.Open(path)
	if err != nil {
		return failure("%v", err)
	}
	defer f.Close()
	if length <= 0 || length > fileChunkMax {
		length = fileChunkMax
	}
	offset = max(0, min(offset, info.Size()))
	buf := make([]byte, min(length, info.Size()-offset))
	n, err := f.ReadAt(buf, offset)
	if err != nil && n < len(buf) {
		return failure("%v", err)
	}
	return map[string]any{"success": true, "size": info.Size(), "offset": offset, "modified": info.ModTime().Unix(),
		"mime": mime.TypeByExtension(filepath.Ext(path)), "data": base64.StdEncoding.EncodeToString(buf[:n])}
}
