package main

import (
	"errors"
	"testing"
)

func TestFleetResolverFailsClosedWithConfiguredHub(t *testing.T) {
	for _, broken := range []bool{false, true} {
		validate := func(key string) (string, bool, error) {
			if broken {
				return "", false, errors.New("offline")
			}
			if key == "valid" {
				return "alice", true, nil
			}
			return "", false, nil
		}
		resolve := fleetResolver(true, nil, validate)
		if _, ok := resolve("untrusted"); ok {
			t.Fatal("Hub failure became open access")
		}
		resolve = fleetResolver(true, map[string]bool{"local": true}, validate)
		if _, ok := resolve("local"); !ok {
			t.Fatal("explicit local key rejected")
		}
		if _, ok := resolve("other"); ok {
			t.Fatal("allowlist bypass")
		}
		if !broken {
			if id, ok := resolve("valid"); !ok || id != "alice" {
				t.Fatal("valid Hub identity lost")
			}
		}
	}
}
