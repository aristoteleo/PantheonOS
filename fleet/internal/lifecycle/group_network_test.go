package lifecycle

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestGroupNetworkManifestAdmissionAndIdentity(t *testing.T) {
	def := definition()
	def.AppID = "model-service"
	def.Dependencies.ContainerEngine = &EngineDependency{Provider: "docker", Provision: "never"}
	def.Requires.Caps = []string{"model-group-private-network"}
	c := &def.Components[0]
	c.GroupNetwork = true
	c.GroupPeer = true
	c.Runtime = "container"
	c.RunAsOwner = true
	c.Image = "python@sha256:" + strings.Repeat("a", 64)
	c.Resources = &ResourceRequest{MemoryBytes: 64 << 20}
	c.Ports = map[string]int{"http": 30001}
	if err := def.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, variant := range []string{"missing-capability", "process", "no-peer", "no-command"} {
		t.Run(variant, func(t *testing.T) {
			copy := clone(def)
			switch variant {
			case "missing-capability":
				copy.Requires.Caps = nil
			case "process":
				copy.Components[0].Runtime = "process"
			case "no-peer":
				copy.Components[0].GroupPeer = false
			case "no-command":
				copy.Components[0].Argv = nil
			}
			if copy.Validate() == nil {
				t.Fatal("accepted unadmitted network")
			}
		})
	}
	var original containerInfo
	if err := json.Unmarshal([]byte(`{"Id":"original","State":{"Running":true,"Pid":123,"StartedAt":"start"},"HostConfig":{"NetworkMode":"none","CapDrop":["ALL"],"RestartPolicy":{"Name":"no"}}}`), &original); err != nil {
		t.Fatal(err)
	}
	if !sameContainer(original, original) {
		t.Fatal("rejected original")
	}
	for _, variant := range []string{"id", "pid", "restart", "bridge", "privileged", "capability"} {
		copy := clone(original)
		switch variant {
		case "id":
			copy.ID = "other"
		case "pid":
			copy.State.Pid++
		case "restart":
			copy.State.StartedAt = "restart"
		case "bridge":
			copy.HostConfig.NetworkMode = "bridge"
		case "privileged":
			copy.HostConfig.Privileged = true
		case "capability":
			copy.HostConfig.CapAdd = []string{"NET_ADMIN"}
		}
		if sameContainer(original, copy) {
			t.Fatal("accepted changed container", variant)
		}
	}
}
