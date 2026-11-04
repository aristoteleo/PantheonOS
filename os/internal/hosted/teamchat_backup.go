package hosted

// Daily snapshots of Team Chat: every key of the TEAMCHAT bucket and every
// message of the TEAMCHAT_MSG stream, one file per day, seven kept, as for
// desktop-state. Files (the object store) are not in snapshots.

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// KeepTeamChatSnapshots is how many daily snapshots stay on disk.
const KeepTeamChatSnapshots = 7

type tcSnapMsg struct {
	Subject string          `json:"subject"`
	Data    json.RawMessage `json:"data"`
}

func (t *TeamChat) SnapshotPath(day time.Time) string {
	return filepath.Join(t.Backups, "teamchat-"+day.UTC().Format("2006-01-02")+".json")
}

func (t *TeamChat) dailySnapshot(ctx context.Context) {
	if _, err := os.Stat(t.SnapshotPath(t.clock())); !os.IsNotExist(err) {
		return
	}
	path, keys, msgs, err := t.Snapshot(ctx, t.clock())
	if err != nil {
		log.Printf("[team-chat] snapshot: %v", err)
		return
	}
	log.Printf("[team-chat] snapshot %s: %d keys, %d messages", filepath.Base(path), keys, msgs)
	if err := t.kv.PurgeDeletes(ctx); err != nil {
		log.Printf("[team-chat] purge delete markers: %v", err)
	}
}

// Snapshot writes the day's file atomically and keeps the newest seven:
// {"bucket", "stream", "taken", "entries": [{key, value}…], "messages": [{subject, data}…]}.
func (t *TeamChat) Snapshot(ctx context.Context, day time.Time) (string, int, int, error) {
	if t.Backups == "" {
		return "", 0, 0, fmt.Errorf("no backup directory")
	}
	kv, err := t.open(ctx)
	if err != nil {
		return "", 0, 0, err
	}
	if err := os.MkdirAll(t.Backups, 0o700); err != nil {
		return "", 0, 0, err
	}
	tmp, err := os.CreateTemp(t.Backups, ".teamchat-snapshot-*")
	if err != nil {
		return "", 0, 0, err
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()
	if err := tmp.Chmod(0o600); err != nil {
		return "", 0, 0, err
	}
	w := bufio.NewWriter(tmp)
	header, _ := json.Marshal(map[string]any{"bucket": TeamChatBucket, "stream": TeamChatStream, "taken": time.Now().UTC().Format(time.RFC3339)})
	fmt.Fprintf(w, "%s,\"entries\":[", strings.TrimSuffix(string(header), "}"))
	watcher, err := kv.WatchAll(ctx, jetstream.IgnoreDeletes())
	if err != nil {
		return "", 0, 0, err
	}
	keys := 0
	for done := false; !done; {
		select {
		case <-ctx.Done():
			watcher.Stop()
			return "", 0, 0, ctx.Err()
		case entry := <-watcher.Updates():
			if entry == nil {
				done = true
				break
			}
			if !json.Valid(entry.Value()) {
				continue
			}
			line, _ := json.Marshal(desktopSnapshotEntry{entry.Key(), entry.Value()})
			if keys > 0 {
				w.WriteString(",")
			}
			w.WriteString("\n")
			w.Write(line)
			keys++
		}
	}
	watcher.Stop()
	w.WriteString("\n],\"messages\":[")
	msgs := 0
	t.infoMu.Lock()
	info, err := t.stream.Info(ctx)
	t.infoMu.Unlock()
	if err != nil {
		return "", 0, 0, err
	}
	if info.State.Msgs > 0 {
		cons, err := t.stream.OrderedConsumer(ctx, jetstream.OrderedConsumerConfig{})
		if err != nil {
			return "", 0, 0, err
		}
		for remaining := info.State.Msgs; remaining > 0; {
			batch, err := cons.Fetch(int(min(remaining, 512)), jetstream.FetchMaxWait(5*time.Second))
			if err != nil {
				return "", 0, 0, err
			}
			n := 0
			for msg := range batch.Messages() {
				n++
				if json.Valid(msg.Data()) {
					line, _ := json.Marshal(tcSnapMsg{msg.Subject(), msg.Data()})
					if msgs > 0 {
						w.WriteString(",")
					}
					w.WriteString("\n")
					w.Write(line)
					msgs++
				}
				if meta, err := msg.Metadata(); err == nil {
					remaining = meta.NumPending
				}
			}
			if err := batch.Error(); err != nil && !errors.Is(err, nats.ErrTimeout) && !errors.Is(err, jetstream.ErrNoMessages) {
				return "", 0, 0, err
			}
			if n == 0 {
				break
			}
		}
	}
	w.WriteString("\n]}\n")
	if err := w.Flush(); err != nil {
		return "", 0, 0, err
	}
	if err := tmp.Sync(); err != nil {
		return "", 0, 0, err
	}
	if err := tmp.Close(); err != nil {
		return "", 0, 0, err
	}
	path := t.SnapshotPath(day)
	if err := os.Rename(tmp.Name(), path); err != nil {
		return "", 0, 0, err
	}
	files, _ := filepath.Glob(filepath.Join(t.Backups, "teamchat-????-??-??.json"))
	sort.Strings(files)
	for len(files) > KeepTeamChatSnapshots {
		_ = os.Remove(files[0])
		files = files[1:]
	}
	return path, keys, msgs, nil
}

// Restore puts a snapshot back: keys that are absent, and the messages when
// the stream is empty. Spaces in memory are read again.
func (t *TeamChat) Restore(ctx context.Context, path string) (keys, msgs int, err error) {
	kv, err := t.open(ctx)
	if err != nil {
		return 0, 0, err
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return 0, 0, err
	}
	var snap struct {
		Bucket   string                 `json:"bucket"`
		Entries  []desktopSnapshotEntry `json:"entries"`
		Messages []tcSnapMsg            `json:"messages"`
	}
	if err := json.Unmarshal(raw, &snap); err != nil || snap.Bucket != TeamChatBucket {
		return 0, 0, fmt.Errorf("%s is not a Team Chat snapshot", filepath.Base(path))
	}
	defer func() {
		t.fleetsMu.Lock()
		for _, fl := range t.fleets {
			fl.mu.Lock()
			fl.loaded = false
			fl.mu.Unlock()
		}
		t.fleetsMu.Unlock()
	}()
	for _, e := range snap.Entries {
		if _, err := kv.Create(ctx, e.Key, e.Value); err == nil {
			keys++
		} else if !errors.Is(err, jetstream.ErrKeyExists) {
			return keys, msgs, err
		}
	}
	t.infoMu.Lock()
	info, err := t.stream.Info(ctx)
	t.infoMu.Unlock()
	if err != nil {
		return keys, msgs, err
	}
	if info.State.Msgs == 0 {
		for _, m := range snap.Messages {
			if _, err := t.js.Publish(ctx, m.Subject, m.Data); err != nil {
				return keys, msgs, err
			}
			msgs++
		}
	}
	return keys, msgs, nil
}
