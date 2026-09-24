package main

import (
	"errors"
	"slices"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func TestPlatformNetworkCapabilityIsDerivedOnly(t *testing.T) {
	detected := node.PlatformNetwork{Mode: "modal-i6pn", Address: "fdaa::5", Interface: "eth0", Scope: "ap-1/us-east"}
	ok := func(string) (node.PlatformNetwork, error) { return detected, nil }
	failed := func(string) (node.PlatformNetwork, error) { return node.PlatformNetwork{}, errors.New("not modal") }
	cases := []struct {
		name      string
		mode      string
		detect    func(string) (node.PlatformNetwork, error)
		wantErr   bool
		advertise bool
	}{
		{name: "injected via caps without flag", mode: "", detect: ok},
		{name: "unsupported mode", mode: "wireguard", detect: ok, wantErr: true},
		{name: "detection fails", mode: "modal-i6pn", detect: failed, wantErr: true},
		{name: "detected", mode: "modal-i6pn", detect: ok, advertise: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			capa := proto.Capability{
				Caps:     []string{"proc", node.PlatformNetworkCap, node.PlatformNetworkCap},
				Runtimes: map[string]string{"runner": "v", "group-platform-address": "fdaa::bad"},
			}
			err := advertisePlatformNetwork(&capa, tc.mode, tc.detect)
			if (err != nil) != tc.wantErr {
				t.Fatal(err)
			}
			n := 0
			for _, c := range capa.Caps {
				if c == node.PlatformNetworkCap {
					n++
				}
			}
			if !slices.Contains(capa.Caps, "proc") || capa.Runtimes["runner"] != "v" {
				t.Fatal("unrelated capability lost", capa)
			}
			if !tc.advertise {
				if n != 0 || capa.Runtimes["group-platform-address"] != "" {
					t.Fatal("platform network claimed without detection", capa)
				}
				return
			}
			if n != 1 || capa.Runtimes["group-platform-network"] != "modal-i6pn" || capa.Runtimes["group-platform-address"] != "fdaa::5" ||
				capa.Runtimes["group-platform-interface"] != "eth0" || capa.Runtimes["group-platform-scope"] != "ap-1/us-east" {
				t.Fatal("detected network not advertised", capa)
			}
		})
	}
}
