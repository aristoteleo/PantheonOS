package lifecycle

import "testing"

func TestServiceCannotRouteStaleOrUndeclaredPorts(t *testing.T) {
	m, _, d := setup(t)
	submit(t, m, d, "start", "start", "app", 0)
	id := m.instanceID(d, "app")
	_ = m.update(func() {
		m.ledger.Installations[d].Definition.Components[0].Ports = map[string]int{"http": 0}
		m.ledger.Instances[id].Resources[0].Endpoints = map[string]string{"http": "http://127.0.0.1:45678"}
	})
	if _, err := m.Service(id, d, 1, "backend", "http"); err != nil {
		t.Fatal(err)
	}
	for _, q := range []struct {
		id, rev, component, port string
		generation               uint64
	}{
		{"another-instance", d, "backend", "http", 1}, {id, d, "backend", "http", 2}, {id, "bad-revision", "backend", "http", 1}, {id, d, "backend", "admin", 1},
	} {
		if _, err := m.Service(q.id, q.rev, q.generation, q.component, q.port); err == nil {
			t.Fatal("accepted invalid binding", q)
		}
	}
	_ = m.update(func() { m.ledger.Instances[id].Resources[0].Endpoints["http"] = "http://169.254.169.254:80" })
	if _, err := m.Service(id, d, 1, "backend", "http"); err == nil {
		t.Fatal("accepted non-loopback endpoint")
	}
	_ = m.update(func() { m.ledger.Instances[id].Resources[0].Endpoints["http"] = "http://127.0.0.1:45678" })
	for _, state := range []string{"draining", "stop_blocked", "recovered"} {
		_ = m.update(func() { m.ledger.Instances[id].State = state })
		if _, err := m.Service(id, d, 1, "backend", "http"); err != nil {
			t.Fatal("existing editor cannot finish saving during drain", err)
		}
	}
	_ = m.update(func() { m.ledger.Instances[id].State = "stopped" })
	if _, err := m.Service(id, d, 1, "backend", "http"); err == nil {
		t.Fatal("accepted stopped generation")
	}
}
