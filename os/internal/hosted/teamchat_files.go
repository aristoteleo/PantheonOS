package hosted

// Files in messages: uploaded in pieces (by browsers through file_put, by
// agents through their node), kept in the TEAMCHAT_FILES object store, read
// in pieces (file_get), and saved into an agent's folder when a turn's
// messages carry them.

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"mime"
	"os"
	"path"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/nats-io/nats.go/jetstream"
)

type filePutArgs struct {
	UploadID string `json:"upload_id"`
	Name     string `json:"name"`
	Mime     string `json:"mime"`
	Offset   int64  `json:"offset"`
	Data     string `json:"data"`
	Final    bool   `json:"final"`
}

type tcUpload struct {
	mu        sync.Mutex
	fid, from string
	name      string
	mime      string
	path      string
	size      int64
	used      time.Time
}

// cleanName keeps a file's own name: no folders, no leading dots, at most
// 128 characters.
func cleanName(name string) string {
	name = strings.ReplaceAll(name, `\`, "/")
	name = path.Base(strings.TrimSpace(name))
	name = strings.TrimLeft(name, ".")
	name = strings.Map(func(r rune) rune {
		if r < 32 || r == 127 {
			return -1
		}
		return r
	}, name)
	if r := []rune(name); len(r) > 128 {
		name = string(r[len(r)-128:])
	}
	if name == "" || name == "/" {
		name = "file"
	}
	return name
}

func (t *TeamChat) dir(sub string) (string, error) {
	base := t.Dir
	if base == "" {
		base = filepath.Join(os.TempDir(), "team-chat")
	}
	d := filepath.Join(base, sub)
	return d, os.MkdirAll(d, 0o700)
}

// filePut takes one piece of a file; the first starts an upload (its reply
// names it), the last (final) stores the file and returns it.
func (t *TeamChat) filePut(ctx context.Context, fid, from string, a filePutArgs) (any, error) {
	if !desktopFleetRE.MatchString(fid) {
		return nil, fmt.Errorf("invalid Fleet")
	}
	data, err := base64.StdEncoding.DecodeString(a.Data)
	if err != nil {
		return nil, &ArgumentError{"data is base64"}
	}
	var up *tcUpload
	if a.UploadID == "" {
		if a.Offset != 0 {
			return nil, &ArgumentError{"an upload starts at offset 0"}
		}
		dir, err := t.dir("uploads")
		if err != nil {
			return nil, err
		}
		id := "u" + shortID() + shortID()
		up = &tcUpload{fid: fid, from: from, name: cleanName(a.Name), mime: a.Mime, path: filepath.Join(dir, id), used: time.Now()}
		if up.mime == "" {
			up.mime = mime.TypeByExtension(filepath.Ext(up.name))
		}
		if err := os.WriteFile(up.path, nil, 0o600); err != nil {
			return nil, err
		}
		t.uploads.Store(id, up)
		a.UploadID = id
	} else {
		v, ok := t.uploads.Load(a.UploadID)
		if !ok {
			return nil, &ArgumentError{"no upload " + a.UploadID + " (it expired; start again)"}
		}
		up = v.(*tcUpload)
		if up.fid != fid || up.from != from {
			return nil, &ArgumentError{"no upload " + a.UploadID}
		}
	}
	up.mu.Lock()
	defer up.mu.Unlock()
	up.used = time.Now()
	if a.Offset != up.size {
		return nil, &ArgumentError{fmt.Sprintf("the upload is at %d bytes, not %d", up.size, a.Offset)}
	}
	if up.size+int64(len(data)) > tcMaxFile {
		t.dropUpload(a.UploadID, up)
		return nil, &ArgumentError{fmt.Sprintf("a file is at most %d MB", tcMaxFile>>20)}
	}
	if len(data) > 0 {
		f, err := os.OpenFile(up.path, os.O_WRONLY|os.O_APPEND, 0)
		if err != nil {
			return nil, err
		}
		_, err = f.Write(data)
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return nil, err
		}
		up.size += int64(len(data))
	}
	if !a.Final {
		return map[string]any{"upload_id": a.UploadID, "size": up.size}, nil
	}
	defer t.dropUpload(a.UploadID, up)
	f, err := os.Open(up.path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	fl, err := t.lockFleet(ctx, fid)
	if err != nil {
		return nil, err
	}
	defer fl.mu.Unlock()
	file, err := t.storeFile(ctx, fl, from, up.name, up.mime, f, up.size)
	if err != nil {
		return nil, err
	}
	return frozen(map[string]any{"file": file})
}

func (t *TeamChat) dropUpload(id string, up *tcUpload) {
	t.uploads.Delete(id)
	_ = os.Remove(up.path)
}

// storeFile keeps a file of the space; fl.mu is held.
func (t *TeamChat) storeFile(ctx context.Context, fl *tcFleet, from, name, mimeType string, r io.Reader, size int64) (*tcFile, error) {
	var used int64
	for _, f := range fl.files {
		used += f.Size
	}
	if used+size > tcMaxSpaceFiles {
		return nil, &ArgumentError{fmt.Sprintf("this space's files take %d MB of %d MB; delete messages with files first",
			used>>20, int64(tcMaxSpaceFiles)>>20)}
	}
	id := "f" + strings.ToLower(tcNewID(t.clock()))
	if _, err := t.obj.Put(ctx, jetstream.ObjectMeta{Name: fl.fid + "/" + id}, r); err != nil {
		return nil, err
	}
	file := &tcFile{tcFileRef: tcFileRef{ID: id, Name: cleanName(name), Mime: mimeType, Size: size}, From: from, At: t.stamp()}
	if err := t.put(ctx, fl, "file."+id, file); err != nil {
		return nil, err
	}
	fl.files[id] = file
	return file, nil
}

func (t *TeamChat) storeBytes(ctx context.Context, fl *tcFleet, from, name, mimeType string, b []byte) (*tcFile, error) {
	return t.storeFile(ctx, fl, from, name, mimeType, bytes.NewReader(b), int64(len(b)))
}

// cached is a stored file on the controller's disk, read from the object
// store once and kept for an hour after its last use.
func (t *TeamChat) cached(ctx context.Context, fid, id string) (string, error) {
	dir, err := t.dir("cache")
	if err != nil {
		return "", err
	}
	p := filepath.Join(dir, fid+"-"+id)
	if _, err := os.Stat(p); err == nil {
		now := time.Now()
		_ = os.Chtimes(p, now, now)
		return p, nil
	}
	tmp := p + ".part" + shortID()
	if err := t.obj.GetFile(ctx, fid+"/"+id, tmp); err != nil {
		_ = os.Remove(tmp)
		return "", err
	}
	if err := os.Rename(tmp, p); err != nil {
		return "", err
	}
	return p, nil
}

func (t *TeamChat) fileGet(ctx context.Context, fid, id string, offset, length int64) (any, error) {
	fl, err := t.lockFleet(ctx, fid)
	if err != nil {
		return nil, err
	}
	f := fl.files[id]
	fl.mu.Unlock()
	if f == nil {
		return nil, &ArgumentError{"no file " + id}
	}
	if length <= 0 || length > tcChunk {
		length = tcChunk
	}
	p, err := t.cached(ctx, fid, id)
	if err != nil {
		return nil, err
	}
	data, err := readAt(p, offset, length)
	if err != nil {
		return nil, err
	}
	return map[string]any{"file": f, "offset": offset, "data": base64.StdEncoding.EncodeToString(data),
		"eof": offset+int64(len(data)) >= f.Size}, nil
}

func readAt(p string, offset, length int64) ([]byte, error) {
	fh, err := os.Open(p)
	if err != nil {
		return nil, err
	}
	defer fh.Close()
	buf := make([]byte, length)
	n, err := fh.ReadAt(buf, offset)
	if err != nil && err != io.EOF {
		return nil, err
	}
	return buf[:n], nil
}

// pushFile saves a message's file into an agent's folder on its node
// (files/<id>-<name>), unless it is there already; the path is relative to
// the folder.
func (t *TeamChat) pushFile(ctx context.Context, fid, nodeID, folder string, f tcFileRef) (string, error) {
	rel := "files/" + f.ID[:min(len(f.ID), 12)] + "-" + cleanName(f.Name)
	p, err := t.cached(ctx, fid, f.ID)
	if err != nil {
		return "", err
	}
	for offset := int64(0); ; {
		data, err := readAt(p, offset, tcNodeChunk)
		if err != nil {
			return "", err
		}
		final := offset+int64(len(data)) >= f.Size
		reply, err := t.Nodes.Call(ctx, fid, nodeID, "team_file_put", map[string]any{"folder": folder, "name": rel,
			"offset": offset, "size": f.Size, "data": base64.StdEncoding.EncodeToString(data), "final": final}, 30*time.Second)
		if err != nil {
			return "", err
		}
		if reply["success"] != true {
			return "", fmt.Errorf("%s", str(reply["error"]))
		}
		if reply["exists"] == true || final {
			return rel, nil
		}
		offset += int64(len(data))
	}
}

// sweepFiles drops uploads idle for an hour and cached files unused for one.
func (t *TeamChat) sweepFiles() {
	t.uploads.Range(func(k, v any) bool {
		up := v.(*tcUpload)
		up.mu.Lock()
		idle := time.Since(up.used) > time.Hour
		up.mu.Unlock()
		if idle {
			t.dropUpload(k.(string), up)
		}
		return true
	})
	if t.Dir == "" {
		return
	}
	entries, _ := os.ReadDir(filepath.Join(t.Dir, "cache"))
	for _, e := range entries {
		if info, err := e.Info(); err == nil && time.Since(info.ModTime()) > time.Hour {
			_ = os.Remove(filepath.Join(t.Dir, "cache", e.Name()))
		}
	}
}
