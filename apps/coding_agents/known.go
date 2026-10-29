package codingagentsapp

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// The agents' own sessions this desktop knows: started or resumed in a
// PantheonOS terminal here, or imported by the user. The desktop lists these
// and leaves the rest of the sessions on the machine out.

const knownLimit = 2000

type knownSession struct {
	Agent string  `json:"agent"`
	ID    string  `json:"id"`
	At    float64 `json:"at"`
}

func (a *App) knownPath() string { return filepath.Join(a.root, "known_sessions.json") }

// loadKnown reads the list; callers hold a.knownMu.
func (a *App) loadKnown() map[string]knownSession {
	out := map[string]knownSession{}
	b, err := os.ReadFile(a.knownPath())
	if err != nil {
		return out
	}
	var list []knownSession
	if json.Unmarshal(b, &list) == nil {
		for _, k := range list {
			out[k.Agent+"/"+k.ID] = k
		}
	}
	return out
}

func (a *App) saveKnown(m map[string]knownSession) error {
	list := make([]knownSession, 0, len(m))
	for _, k := range m {
		list = append(list, k)
	}
	sort.Slice(list, func(i, j int) bool { return list[i].At > list[j].At })
	if len(list) > knownLimit {
		list = list[:knownLimit]
	}
	b, err := json.Marshal(list)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(a.root, 0o700); err != nil {
		return err
	}
	tmp := a.knownPath() + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, a.knownPath())
}

// remember marks sessions as the desktop's; forget unmarks them.
func (a *App) remember(refs ...knownSession) error {
	a.knownMu.Lock()
	defer a.knownMu.Unlock()
	m := a.loadKnown()
	now := float64(time.Now().Unix())
	for _, r := range refs {
		if r.Agent == "" || r.ID == "" {
			continue
		}
		r.At = now
		m[r.Agent+"/"+r.ID] = r
	}
	return a.saveKnown(m)
}

func (a *App) forget(refs ...knownSession) error {
	a.knownMu.Lock()
	defer a.knownMu.Unlock()
	m := a.loadKnown()
	for _, r := range refs {
		delete(m, r.Agent+"/"+r.ID)
	}
	return a.saveKnown(m)
}

func (a *App) knownSet() map[string]knownSession {
	a.knownMu.Lock()
	defer a.knownMu.Unlock()
	return a.loadKnown()
}
