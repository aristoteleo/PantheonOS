package hosted

// Daily snapshots of the DESKTOP bucket. The staging controller runs one NATS
// server with one copy of the bucket; a snapshot file per day (seven kept)
// is what a lost JetStream store is restored from (docs/os/desktop-state.md).

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/nats-io/nats.go/jetstream"
)

// KeepDesktopSnapshots is how many daily snapshots stay on disk.
const KeepDesktopSnapshots = 7

type desktopSnapshotEntry struct {
	Key   string          `json:"key"`
	Value json.RawMessage `json:"value"`
}

// SnapshotPath is the day's snapshot file.
func (d *DesktopState) SnapshotPath(day time.Time) string {
	return filepath.Join(d.Backups, "desktop-"+day.UTC().Format("2006-01-02")+".json")
}

// Snapshot writes every live key of the bucket to the day's file, replacing
// it atomically, and removes all but the newest KeepDesktopSnapshots.
// Entries are streamed: the file is one JSON object whose "entries" list has
// one {key, value} per line.
func (d *DesktopState) Snapshot(ctx context.Context, day time.Time) (string, int, error) {
	if d.Backups == "" {
		return "", 0, fmt.Errorf("no backup directory")
	}
	kv, err := d.bucket(ctx)
	if err != nil {
		return "", 0, err
	}
	if err := os.MkdirAll(d.Backups, 0o700); err != nil {
		return "", 0, err
	}
	path := d.SnapshotPath(day)
	tmp, err := os.CreateTemp(d.Backups, ".desktop-snapshot-*")
	if err != nil {
		return "", 0, err
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()
	if err := tmp.Chmod(0o600); err != nil {
		return "", 0, err
	}
	w := bufio.NewWriter(tmp)
	header, _ := json.Marshal(map[string]any{"bucket": DesktopBucket, "taken": time.Now().UTC().Format(time.RFC3339)})
	// {"bucket":…,"taken":…,"entries":[\n{…},\n{…}\n]}
	fmt.Fprintf(w, "%s,\"entries\":[", strings.TrimSuffix(string(header), "}"))
	watcher, err := kv.WatchAll(ctx, jetstream.IgnoreDeletes())
	if err != nil {
		return "", 0, err
	}
	defer watcher.Stop()
	count := 0
	for done := false; !done; {
		select {
		case <-ctx.Done():
			return "", 0, ctx.Err()
		case entry := <-watcher.Updates():
			if entry == nil {
				done = true
				break
			}
			if !json.Valid(entry.Value()) {
				log.Printf("[desktop-state] snapshot skips %s: not JSON", entry.Key())
				continue
			}
			line, err := json.Marshal(desktopSnapshotEntry{entry.Key(), entry.Value()})
			if err != nil {
				return "", 0, err
			}
			if count > 0 {
				w.WriteString(",")
			}
			w.WriteString("\n")
			w.Write(line)
			count++
		}
	}
	w.WriteString("\n]}\n")
	if err := w.Flush(); err != nil {
		return "", 0, err
	}
	if err := tmp.Sync(); err != nil {
		return "", 0, err
	}
	if err := tmp.Close(); err != nil {
		return "", 0, err
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		return "", 0, err
	}
	d.prune()
	return path, count, nil
}

// prune keeps the newest KeepDesktopSnapshots snapshot files.
func (d *DesktopState) prune() {
	files, _ := filepath.Glob(filepath.Join(d.Backups, "desktop-????-??-??.json"))
	sort.Strings(files) // dated names sort by day
	for len(files) > KeepDesktopSnapshots {
		if err := os.Remove(files[0]); err != nil {
			log.Printf("[desktop-state] prune %s: %v", filepath.Base(files[0]), err)
		}
		files = files[1:]
	}
}

// Restore puts a snapshot's entries back into the bucket. A key that exists
// is kept (the bucket is newer than any snapshot of it); the counts say how
// many were restored and kept.
func (d *DesktopState) Restore(ctx context.Context, path string) (restored, kept int, err error) {
	kv, err := d.bucket(ctx)
	if err != nil {
		return 0, 0, err
	}
	f, err := os.Open(path)
	if err != nil {
		return 0, 0, err
	}
	defer f.Close()
	var snapshot struct {
		Bucket  string                 `json:"bucket"`
		Entries []desktopSnapshotEntry `json:"entries"`
	}
	if err := json.NewDecoder(bufio.NewReader(f)).Decode(&snapshot); err != nil {
		return 0, 0, fmt.Errorf("snapshot %s is unreadable: %w", filepath.Base(path), err)
	}
	if snapshot.Bucket != DesktopBucket {
		return 0, 0, fmt.Errorf("snapshot %s is not of the %s bucket", filepath.Base(path), DesktopBucket)
	}
	// The bucket changes under the cached desktops.
	defer d.forgetAll()
	for _, e := range snapshot.Entries {
		if _, err := kv.Create(ctx, e.Key, e.Value); err != nil {
			if conflict(err) {
				kept++
				continue
			}
			return restored, kept, fmt.Errorf("restore %s: %w", e.Key, err)
		}
		restored++
	}
	return restored, kept, nil
}

// Run takes the day's snapshot when it is missing (checked hourly) and clears
// old delete markers of closed windows, until ctx ends.
func (d *DesktopState) Run(ctx context.Context, every time.Duration) {
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		if d.Backups != "" {
			if _, err := os.Stat(d.SnapshotPath(time.Now())); os.IsNotExist(err) {
				if path, n, err := d.Snapshot(ctx, time.Now()); err != nil {
					log.Printf("[desktop-state] snapshot: %v", err)
				} else {
					log.Printf("[desktop-state] snapshot %s: %d keys", filepath.Base(path), n)
					if kv, err := d.bucket(ctx); err == nil {
						if err := kv.PurgeDeletes(ctx); err != nil {
							log.Printf("[desktop-state] purge delete markers: %v", err)
						}
					}
				}
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
