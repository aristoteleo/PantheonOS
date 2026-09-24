package groupcredentials

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func overlayFixture(t *testing.T) (OverlayStore, Manifest, []OverlayEndpoint) {
	t.Helper()
	_, _, m, _, _, _ := fixture(t)
	var endpoints []OverlayEndpoint
	var local OverlayStore
	for rank, member := range m.Topology.Members {
		s := OverlayStore{Root: filepath.Join(t.TempDir(), "overlays"), Owner: m.Topology.Owner, Node: member.Node}
		mr := m
		mr.Rank = &rank
		address := "192.168.50.10:51890"
		if rank == 1 {
			address = "192.168.50.11:51890"
		}
		status, err := s.Prepare(mr, address)
		if err != nil {
			t.Fatal(err)
		}
		endpoints = append(endpoints, *status.Endpoint)
		if rank == *m.Rank {
			local = s
		}
	}
	return local, m, endpoints
}

func TestOverlayOriginalIdentitySurvivesLostRepliesAndRestart(t *testing.T) {
	s, m, endpoints := overlayFixture(t)
	if _, _, err := s.Material(m); err == nil {
		t.Fatal("uncommitted roster exposed key")
	}
	first, err := s.Pin(m.Topology.Group, m.Fingerprint(), endpoints)
	if err != nil {
		t.Fatal(err)
	}
	key, roster, err := s.Material(m)
	if err != nil || !reflect.DeepEqual(roster, endpoints) {
		t.Fatal(err)
	}
	pub, err := OverlayPublicKey(key)
	if err != nil || pub != endpoints[*m.Rank].PublicKey {
		t.Fatal("wrong key", err)
	}
	restarted := OverlayStore{Root: s.Root, Owner: s.Owner, Node: s.Node}
	again, err := restarted.Prepare(m, endpoints[*m.Rank].Address)
	if err != nil || !reflect.DeepEqual(again, first) {
		t.Fatal("replaced identity", err)
	}
	again, err = restarted.Pin(m.Topology.Group, m.Fingerprint(), endpoints)
	if err != nil || !reflect.DeepEqual(again, first) {
		t.Fatal("replaced roster", err)
	}
	key2, _, err := restarted.Material(m)
	if err != nil || !bytes.Equal(key, key2) {
		t.Fatal("rotated private key", err)
	}
	data, _ := json.Marshal(first)
	if bytes.Contains(data, []byte("private_key")) || bytes.Contains(data, []byte(s.Root)) {
		t.Fatal("secret state exposed")
	}
	// Returned structures are independent of the persisted original.
	again.Endpoints[0].Address = "192.168.50.99:51890"
	status, err := restarted.Status(m.Topology.Group, m.Fingerprint())
	if err != nil || !reflect.DeepEqual(status, first) {
		t.Fatal("caller mutated durable roster", err)
	}
}

func TestOverlayFencesLatePreparePinAndMaterial(t *testing.T) {
	for _, before := range []bool{true, false} {
		t.Run(map[bool]string{true: "before_prepare", false: "after_pin"}[before], func(t *testing.T) {
			s, m, endpoints := overlayFixture(t)
			if before {
				s.Root = filepath.Join(t.TempDir(), "fresh")
			} else if _, err := s.Pin(m.Topology.Group, m.Fingerprint(), endpoints); err != nil {
				t.Fatal(err)
			}
			closed, err := s.Close(m.Topology.Group, m.Fingerprint())
			if err != nil || closed.State != "closed" {
				t.Fatal(err)
			}
			restarted := OverlayStore{s.Root, s.Owner, s.Node}
			status, err := restarted.Prepare(m, endpoints[*m.Rank].Address)
			if err != nil || !reflect.DeepEqual(status, closed) {
				t.Fatal("late prepare reopened group", err)
			}
			if _, err := restarted.Pin(m.Topology.Group, m.Fingerprint(), endpoints); err == nil {
				t.Fatal("late pin reopened group")
			}
			if _, _, err := restarted.Material(m); err == nil {
				t.Fatal("closed key exported")
			}
			status, err = restarted.Close(m.Topology.Group, m.Fingerprint())
			if err != nil || !reflect.DeepEqual(status, closed) {
				t.Fatal("close not idempotent", err)
			}
			dir, _, _ := restarted.directory(m.Topology.Group, m.Fingerprint(), false)
			defer dir.Close()
			r, err := restarted.read(dir, m.Topology.Group, m.Fingerprint())
			if err != nil || r.Key != "" {
				t.Fatal("private key retained", err)
			}
		})
	}
}

func TestOverlayRejectsIdentityAndRosterReplacement(t *testing.T) {
	s, m, endpoints := overlayFixture(t)
	if _, err := s.Pin(m.Topology.Group, m.Fingerprint(), endpoints); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*Manifest){
		func(m *Manifest) { m.CAHash = strings.Repeat("f", 64) },
		func(m *Manifest) { m.Topology.Launch = strings.Repeat("f", 64) },
		func(m *Manifest) { r := 1 - *m.Rank; m.Rank = &r },
		func(m *Manifest) { m.Topology.Owner = "f_ffffffffffffffff" },
	} {
		changed := m
		mutate(&changed)
		if _, err := s.Prepare(changed, endpoints[*m.Rank].Address); err == nil {
			t.Fatal("changed identity accepted")
		}
		if _, _, err := s.Material(changed); err == nil {
			t.Fatal("changed identity received key")
		}
	}
	if _, err := s.Prepare(m, "192.168.50.99:51890"); err == nil {
		t.Fatal("own endpoint replaced")
	}
	for _, mutate := range []func([]OverlayEndpoint){
		func(e []OverlayEndpoint) { e[0].Address = "192.168.50.99:51890" },
		func(e []OverlayEndpoint) { e[1].Address = "192.168.50.99:51890" },
		func(e []OverlayEndpoint) { e[1].PublicKey = e[0].PublicKey },
		func(e []OverlayEndpoint) { e[1].Address = e[0].Address },
		func(e []OverlayEndpoint) { e[1].Address = "8.8.8.8:51890" },
		func(e []OverlayEndpoint) { e[0], e[1] = e[1], e[0] },
	} {
		e := append([]OverlayEndpoint(nil), endpoints...)
		mutate(e)
		if _, err := s.Pin(m.Topology.Group, m.Fingerprint(), e); err == nil {
			t.Fatal("changed roster accepted")
		}
	}
	if _, err := s.Close(m.Topology.Group, strings.Repeat("f", 64)); err == nil {
		t.Fatal("changed plan closed original")
	}
}

func TestOverlayDamagedStateNeverRegeneratesKeys(t *testing.T) {
	for _, damage := range []string{"missing", "truncated", "duplicate", "unknown", "key", "world_readable", "symlink"} {
		t.Run(damage, func(t *testing.T) {
			s, m, endpoints := overlayFixture(t)
			path := filepath.Join(s.Root, hash([]byte(s.Owner+"\x00"+s.Node+"\x00"+m.Topology.Group)), "overlay.json")
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			switch damage {
			case "missing":
				err = os.Remove(path)
			case "truncated":
				err = os.WriteFile(path, []byte("{"), 0600)
			case "duplicate":
				err = os.WriteFile(path, append([]byte(`{"protocol":1,`), data[1:]...), 0600)
			case "unknown":
				err = os.WriteFile(path, append([]byte(`{"extra":1,`), data[1:]...), 0600)
			case "key":
				var r map[string]any
				_ = json.Unmarshal(data, &r)
				r["private_key"] = "bad"
				data, _ = json.Marshal(r)
				err = os.WriteFile(path, data, 0600)
			case "world_readable":
				err = os.Chmod(path, 0644)
			case "symlink":
				dest := filepath.Join(t.TempDir(), "outside")
				err = os.WriteFile(dest, data, 0600)
				if err == nil {
					err = os.Remove(path)
				}
				if err == nil {
					err = os.Symlink(dest, path)
				}
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := s.Prepare(m, endpoints[*m.Rank].Address); err == nil {
				t.Fatal("damaged state recreated")
			}
			if _, err := s.Close(m.Topology.Group, m.Fingerprint()); err == nil {
				t.Fatal("uncertain state erased")
			}
		})
	}
}
