package groupcredentials

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
)

// RuntimePath is internal node state, never an RPC response or manifest path.
// Preparation is deliberately not part of the runtime directory name: cleanup
// after restart needs only the original instance/revision/started generation.
func RuntimePath(root string, b Binding) (string, error) {
	if !filepath.IsAbs(root) || !ownerRE.MatchString(b.Owner) || !nodeRE.MatchString(b.Node) ||
		!instanceRE.MatchString(b.Instance) || !digestRE.MatchString(b.Revision) || b.Generation == 0 || b.Generation >= 1<<63 {
		return "", fmt.Errorf("invalid group runtime binding")
	}
	name := hash([]byte(b.Owner + "\x00" + b.Node + "\x00" + b.Instance + "\x00" + b.Revision + "\x00" + strconv.FormatUint(b.Generation, 10)))
	return filepath.Join(root, name), nil
}

// Materialize validates the still-valid installed leaf and creates a dedicated
// read-only bundle. The complete enrollment record and leader key are NEVER
// exported. Native processes share the Runner OS user; modes do not sandbox
// that user. Containers additionally receive this directory as a read-only bind.
func (s Store) Materialize(root string, b Binding, manifest Manifest) (string, error) {
	path, err := RuntimePath(root, b)
	if err != nil {
		return "", err
	}
	stored, _, err := s.directory(b, manifest, false)
	if err != nil {
		return "", err
	}
	defer stored.Close()
	value, err := readMaterial(stored, b, manifest)
	if err != nil {
		return "", err
	}
	cert, ca, err := validateCertificate(value, value.Certificate, value.CA)
	if err != nil {
		return "", err
	}
	public, err := json.Marshal(manifest)
	if err != nil {
		return "", err
	}
	files := map[string][]byte{"key.pem": []byte(value.Key), "certificate.pem": []byte(cert), "ca.pem": []byte(ca), "group-peer.json": public}
	dir, fresh, err := openPrivateState(root, filepath.Base(path), true, 0)
	if err != nil {
		return "", err
	}
	defer dir.Close()
	if fresh {
		for name, data := range files {
			if err := writePrivateFile(dir, name, data); err != nil {
				return "", err
			}
			if err := dir.Chmod(name, 0400); err != nil {
				return "", err
			}
		}
		if err := dir.Chmod(".", 0500); err != nil {
			return "", err
		}
		f, err := dir.Open(".")
		if err != nil {
			return "", err
		}
		if err := errors.Join(f.Sync(), f.Close()); err != nil {
			return "", err
		}
	}
	if err := CheckRuntime(path); err != nil {
		return "", err
	}
	for name, data := range files {
		f, err := dir.Open(name)
		if err != nil {
			return "", err
		}
		actual, err := io.ReadAll(io.LimitReader(f, 16385))
		f.Close()
		if err != nil || !bytes.Equal(data, actual) {
			return "", fmt.Errorf("group runtime bundle changed; do not repair a possibly consumed attempt")
		}
	}
	return path, nil
}

// CheckRuntime rejects incomplete/unsealed/symlink bundles before driver use.
// Contents are matched to the signed material by Materialize before launch.
func CheckRuntime(path string) error {
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0500 {
		return fmt.Errorf("group runtime bundle is not sealed")
	}
	dir, err := os.OpenRoot(path)
	if err != nil {
		return err
	}
	defer dir.Close()
	f, err := dir.Open(".")
	if err != nil {
		return err
	}
	entries, readErr := f.ReadDir(5)
	f.Close()
	if (readErr != nil && readErr != io.EOF) || len(entries) != 4 {
		return fmt.Errorf("group runtime bundle has unexpected files")
	}
	for _, name := range []string{"key.pem", "certificate.pem", "ca.pem", "group-peer.json"} {
		info, err := dir.Lstat(name)
		if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0400 || info.Size() == 0 || info.Size() > 16384 {
			return fmt.Errorf("group runtime bundle has invalid files")
		}
	}
	return nil
}

// RemoveRuntime runs only after the lifecycle confirms that all original owned
// resources have exited (or an unconsumed preparation is cancelled). It never
// removes the durable enrollment or authority record and never follows symlinks.
func RemoveRuntime(root string, b Binding) error {
	path, err := RuntimePath(root, b)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(root); errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err := privateDirectory(root); err != nil {
		return err
	}
	dir, err := os.OpenRoot(root)
	if err != nil {
		return err
	}
	defer dir.Close()
	name := filepath.Base(path)
	info, err := dir.Lstat(name)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return fmt.Errorf("group runtime cleanup found an invalid directory")
	}
	if err := dir.Chmod(name, 0700); err != nil {
		return err
	}
	if err := dir.RemoveAll(name); err != nil {
		return err
	}
	f, err := dir.Open(".")
	if err != nil {
		return err
	}
	return errors.Join(f.Sync(), f.Close())
}
