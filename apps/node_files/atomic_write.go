package nodefiles

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"time"
)

// Checksum and replacement stay on the file's node. The browser never needs
// another full download just to check for concurrent edits.
type atomicUpdate struct {
	root                           *sharedRoot
	target, temp, expected, result string
	size                           int64
	mode                           os.FileMode
	reuse                          map[reuseKey]reusableRange
}

func (h *handle) removeUpdate() {
	if h.update != nil {
		_ = h.update.root.dir.Remove(h.update.temp)
	}
}

func validHash(s string) bool {
	b, err := hex.DecodeString(s)
	return err == nil && len(b) == sha256.Size
}

func fileDigest(f *os.File) (string, error) {
	before, err := regular(f)
	if err != nil {
		return "", err
	}
	hash := sha256.New()
	n, err := io.Copy(hash, io.NewSectionReader(f, 0, before.Size()))
	if err != nil {
		return "", err
	}
	after, err := f.Stat()
	if err != nil {
		return "", err
	}
	if n != before.Size() || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return "", errors.New("file changed while checking it; retry saving")
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func currentDigest(root *sharedRoot, rel string) (string, os.FileMode, error) {
	info, err := root.dir.Lstat(rel)
	if os.IsNotExist(err) {
		return "", 0644, nil
	}
	if err != nil {
		return "", 0, err
	}
	// Renaming over a symlink would change which file the user is editing.
	if !info.Mode().IsRegular() {
		return "", 0, errors.New("save target must be a regular file, not a symlink or folder")
	}
	f, err := root.dir.Open(rel)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil {
		return "", 0, err
	}
	if !os.SameFile(info, opened) {
		return "", 0, errors.New("file changed while opening it; retry saving")
	}
	digest, err := fileDigest(f)
	latest, statErr := root.dir.Lstat(rel)
	if statErr != nil || !os.SameFile(opened, latest) {
		return "", 0, errors.New("file moved or changed while checking it; retry saving")
	}
	return digest, info.Mode().Perm(), err
}

// Called under a.mu, including commit, so two Office writes through this node
// cannot interleave their checks and replacement.
func (a *App) beginAtomicWrite(p map[string]any) (any, error) {
	expected, result := str(p, "expected_sha256"), str(p, "sha256")
	size := number(p, "total_size", -1)
	if (expected != "" && !validHash(expected)) || !validHash(result) || size < 0 || size > 512*1024*1024 {
		return nil, errors.New("invalid checksum or atomic write size (maximum 512 MiB)")
	}
	if len(a.handles) >= maxHandles {
		return nil, errors.New("too many open files")
	}
	path := filepath.FromSlash(str(p, "file_path"))
	if !filepath.IsAbs(path) {
		return nil, errors.New("save path must be absolute")
	}
	// Resolve a platform alias on the parent only. Resolving the final entry
	// would silently follow a symlink before currentDigest can reject it.
	root, parent, err := a.locate(filepath.Dir(path))
	if err != nil {
		return nil, err
	}
	rel := filepath.Join(parent, filepath.Base(path))
	actual, mode, err := currentDigest(root, rel)
	if err != nil {
		return nil, err
	}
	if actual == result {
		return map[string]any{"success": true, "already_written": true, "sha256": actual}, nil
	}
	if actual != expected {
		return nil, errors.New("file changed outside Office, was moved, or the destination already exists; download a copy to preserve both versions")
	}
	token := make([]byte, 16)
	if _, err = rand.Read(token); err != nil {
		return nil, err
	}
	id := hex.EncodeToString(token)
	temp := filepath.Join(filepath.Dir(rel), ".pantheon-save-"+id)
	f, err := root.dir.OpenFile(temp, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	update := &atomicUpdate{root: root, target: rel, temp: temp, expected: expected, result: result, size: size, mode: mode}
	var reusable []reusableRange
	if boolean(p, "reuse_zip") && expected != "" {
		reusable = zipReuseRanges(root, rel, boolean(p, "reuse_decoded"))
		update.reuse = make(map[reuseKey]reusableRange, len(reusable))
		for _, span := range reusable {
			update.reuse[reuseKey{span.Offset, span.Encoding}] = span
		}
	}
	a.handles[id] = &handle{file: f, write: true, touched: time.Now(), update: update}
	return map[string]any{"success": true, "handle_id": id, "total_size": size, "reusable_ranges": reusable}, nil
}

func (a *App) commitAtomicWrite(id string, h *handle) (any, error) {
	u := h.update
	if u == nil {
		return nil, errors.New("not an atomic write handle")
	}
	// Any failed commit keeps the original intact and removes the temporary.
	defer func() { delete(a.handles, id); h.file.Close(); h.removeUpdate() }()
	info, err := h.file.Stat()
	if err != nil {
		return nil, err
	}
	if info.Size() != u.size {
		return nil, errors.New("saved file is incomplete")
	}
	digest, err := fileDigest(h.file)
	if err != nil {
		return nil, err
	}
	if digest != u.result {
		return nil, errors.New("saved file checksum mismatch")
	}
	if err = h.file.Chmod(u.mode); err != nil {
		return nil, err
	}
	if err = h.file.Sync(); err != nil {
		return nil, err
	}
	actual, _, err := currentDigest(u.root, u.target)
	if err != nil {
		return nil, err
	}
	if actual != u.expected {
		return nil, errors.New("file changed outside Office during saving; download a copy to preserve both versions")
	}
	if err = h.file.Close(); err != nil {
		return nil, err
	}
	if u.expected == "" {
		// Link is an atomic create-if-absent; never clobber a newly created sibling.
		err = u.root.dir.Link(u.temp, u.target)
	} else {
		err = u.root.dir.Rename(u.temp, u.target)
	}
	if err != nil {
		return nil, err
	}
	return map[string]any{"success": true, "sha256": digest, "total_size": u.size}, nil
}
