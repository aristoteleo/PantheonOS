package lifecycle

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
)

const importReceipt = ".fleet-data-source.json"

// Called under serial, like start/stop. Neither generation may run while its
// state is copied. The original directory is retained, never moved or deleted.
func (m *Manager) cloneData(ctx context.Context, op *Operation, install *Installation, target *Instance) error {
	req := op.Request
	if install == nil || install.State != "installed" {
		return fmt.Errorf("install the destination artifact before copying state")
	}
	m.mu.Lock()
	source := clone(m.ledger.Instances[m.instanceID(req.DataSource.Digest, req.Scope)])
	m.mu.Unlock()
	if source == nil || source.Generation != req.DataSource.Generation || source.State != "stopped" ||
		len(source.Resources) != 0 || len(source.Reservations) != 0 || source.AppID != install.Definition.AppID {
		return fmt.Errorf("source must be the exact stopped generation of this App and scope")
	}
	if target != nil && (target.Generation != 0 || target.State != "stopped" || len(target.Resources) != 0 ||
		len(target.Reservations) != 0 || !reflect.DeepEqual(target.DataSource, req.DataSource)) {
		return fmt.Errorf("destination state has already been used; it will not be overwritten")
	}
	key := m.instanceID(req.Digest, req.Scope)
	if err := m.step(op, "copy_app_state", func() (Receipt, error) {
		root, err := os.OpenRoot(m.root)
		if err != nil {
			return Receipt{}, err
		}
		defer root.Close()
		data, err := root.OpenRoot("data")
		if err != nil {
			return Receipt{}, err
		}
		defer data.Close()
		if entry, err := data.Lstat(key); err == nil && (!entry.IsDir() || entry.Mode()&os.ModeSymlink != 0) {
			return Receipt{}, fmt.Errorf("destination data is not an owned directory")
		}
		// The receipt is published atomically with the directory. A crash after
		// rename but before ledger persistence can adopt this exact copy only.
		receiptPath := filepath.Join(key, importReceipt)
		if entry, err := data.Lstat(receiptPath); err == nil && (!entry.Mode().IsRegular() || entry.Size() > 1024) {
			return Receipt{}, fmt.Errorf("invalid state import receipt")
		}
		if raw, err := data.ReadFile(receiptPath); err == nil {
			var existing DataSource
			if json.Unmarshal(raw, &existing) != nil || !reflect.DeepEqual(&existing, req.DataSource) {
				return Receipt{}, fmt.Errorf("destination state has a different source")
			}
			return Receipt{Status: "succeeded"}, nil
		}
		if _, err := data.Lstat(key); !os.IsNotExist(err) {
			return Receipt{}, fmt.Errorf("destination data exists without a matching import receipt")
		}
		st, err := data.Lstat(source.ID)
		if err != nil || !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
			return Receipt{}, fmt.Errorf("source data is not an owned directory")
		}
		src, err := data.OpenRoot(source.ID)
		if err != nil {
			return Receipt{}, err
		}
		defer src.Close()
		nonce := make([]byte, 12)
		if _, err := rand.Read(nonce); err != nil {
			return Receipt{}, err
		}
		temporary := ".import-" + hex.EncodeToString(nonce)
		if err := data.Mkdir(temporary, 0700); err != nil {
			return Receipt{}, err
		}
		defer data.RemoveAll(temporary)
		dst, err := data.OpenRoot(temporary)
		if err != nil {
			return Receipt{}, err
		}
		err = copyAppState(ctx, src, dst, req.DataSource, m.resourcePolicy.StateCopy)
		closeErr := dst.Close()
		if err != nil {
			return Receipt{}, err
		}
		if closeErr != nil {
			return Receipt{}, closeErr
		}
		if err := ctx.Err(); err != nil {
			return Receipt{}, err
		}
		if err := data.Rename(temporary, key); err != nil {
			return Receipt{}, err
		}
		return Receipt{Status: "succeeded"}, nil
	}); err != nil {
		return err
	}
	return m.update(func() {
		m.ledger.Instances[key] = &Instance{ID: key, AppID: install.Definition.AppID,
			Version: install.Definition.Version, Digest: req.Digest, Scope: req.Scope,
			State: "stopped", DataSource: clone(req.DataSource), Resources: []Resource{}}
	})
}
