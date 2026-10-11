package modelservices

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func groupFixture(platform bool) (plan, record map[string]any) {
	weights := int64(16 << 30)
	record = map[string]any{"sha256": strings.Repeat("a", 64), "weights_bytes": weights,
		"config":                  map[string]any{"model_type": "llama", "num_hidden_layers": 32, "num_attention_heads": 32, "hidden_size": 4096, "num_key_value_heads": 8},
		"tensor_parallel_weights": map[string]any{"2": weights/2 + (1 << 30)},
		"files": []any{map[string]any{"path": "model.safetensors", "size": weights, "sha256": strings.Repeat("b", 64), "mtime_ns": 5},
			map[string]any{"path": "config.json", "size": 100, "sha256": strings.Repeat("c", 64)},
			map[string]any{"path": "tokenizer.json", "size": 100, "sha256": strings.Repeat("d", 64)}}}
	var members []any
	for rank := 0; rank < 2; rank++ {
		address, iface := []string{"10.231.0.1", "10.231.0.2"}[rank], "wg0"
		if platform {
			address, iface = []string{"fdaa:0:0::1", "fdaa::2"}[rank], "eth1"
		}
		members = append(members, map[string]any{"rank": rank, "node_id": []string{"n_one", "n_two"}[rank], "generation": 2,
			"address": address, "control_port": 18407, "interface": iface, "physical_gpu_bytes": []any{int64(80 << 30)},
			"resources": map[string]any{"memory_bytes": int64(64 << 30), "devices": []any{map[string]any{
				"id": []string{"GPU-aaaa", "GPU-bbbb"}[rank], "backend": "cuda", "memory_bytes": int64(70 << 30), "exclusive": true}}}})
	}
	plan = map[string]any{"protocol": 1, "owner": "f_0123456789abcdef", "group_id": "g1", "recipe_id": containerRecipe,
		"model_sha256": record["sha256"], "context_length": 4096, "parallel": 2, "tensor_parallel_size": 2,
		"rendezvous_port": 18408, "inference_protocol": 1, "members": members}
	if platform {
		plan["recipe_id"], plan["network_mode"] = processRecipe, platformPrivate
	} else {
		plan["underlay"] = []any{"192.168.1.10:51820", "192.168.1.11:51820"}
	}
	return mapOf(clone(plan)), mapOf(clone(record))
}

const groupPython = `import json, sys, io, tarfile, hashlib
sys.path[:0] = [sys.argv[1] + '/apps/model-service', sys.argv[1] + '/pantheon/models']
from pathlib import Path
v = json.load(sys.stdin)
import group_packager, sglang_group
from pantheon.models.group_creation import topology_for, lifecycle_plan
plan, model = v['plan'], group_packager.descriptor(v['record'])
peers = topology_for(plan)
members, configs, fraction, topology = sglang_group._validate(plan, model)
out = dict(creation=peers.document(), fingerprint=peers.fingerprint, launch=topology.document(), fraction=fraction, model=model)
request = dict(plan=plan, topology=topology.document(), ca_sha256='e'*64, source_sha256='f'*64, rank=1)
generated = group_packager.compile_rank(request, v['recipe'], model)
files = {}
for name in group_packager.RUNTIME_FILES:
    base = 'pantheon/models' if name in {'group_network.py', 'group_mesh.py', 'group_inference.py'} else 'apps/model-service'
    files[name] = Path(sys.argv[1], base, name).read_bytes()
files.update({name: group_packager.encode(value) for name, value in generated.items()})
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode='w') as archive:
    for name, data in sorted(files.items()):
        entry = tarfile.TarInfo(name)
        entry.size, entry.mode = len(data), 0o400
        archive.addfile(entry, io.BytesIO(data))
out['package'] = hashlib.sha256(buffer.getvalue()).hexdigest()
row = dict(owner=plan['owner'], group_id=plan['group_id'], plan=plan, topology=peers.document(), source_sha256='f'*64,
           ca_sha256='e'*64, artifacts=[dict(rank=0, digest='1'*64), dict(rank=1, digest='2'*64)])
out['lifecycle'] = lifecycle_plan(row)
print(json.dumps(out))
`

// Group roster, plan checks, rank packages and the handoff lifecycle match
// the Python implementation the rank runtime and Hub were built with.
func TestGroupsMatchPython(t *testing.T) {
	python, runtime := pythonFor(t)
	for _, platform := range []bool{false, true} {
		plan, record := groupFixture(platform)
		recipe := map[string]any{"id": containerRecipe, "version": sglangVersion, "engine": "sglang", "runtime": "container",
			"platforms": []any{"linux-amd64"}, "image": "lmsysorg/sglang@sha256:" + strings.Repeat("9", 64)}
		if platform {
			delete(recipe, "image")
			recipe["id"], recipe["runtime"] = processRecipe, "process"
		}
		input, _ := json.Marshal(map[string]any{"plan": plan, "record": record, "recipe": recipe})
		cmd := exec.Command(python, "-c", groupPython, runtime)
		cmd.Dir, cmd.Stdin = runtime, strings.NewReader(string(input))
		raw, err := cmd.Output()
		if err != nil {
			t.Fatalf("python: %v %s", err, raw)
		}
		var want map[string]any
		if err := decode(raw, &want); err != nil {
			t.Fatal(err)
		}
		model, err := modelDescriptor(record)
		if err != nil || !same(model, want["model"]) {
			t.Fatalf("descriptor %v\n%s\n%s", err, Compact(model), Compact(want["model"]))
		}
		peers, err := topologyFor(plan)
		if err != nil || peers.Fingerprint != want["fingerprint"] || !same(peers.Document(), want["creation"]) {
			t.Fatalf("creation topology %v %s", err, Compact(peers.Document()))
		}
		v, err := validateGroupPlan(plan, model)
		if err != nil {
			t.Fatal(err)
		}
		if f, _ := want["fraction"].(json.Number).Float64(); f != v.fraction || !same(v.topology.Document(), want["launch"]) {
			t.Fatalf("launch %v %v", v.fraction, want["fraction"])
		}
		request := map[string]any{"plan": plan, "topology": v.topology.Document(), "ca_sha256": strings.Repeat("e", 64),
			"source_sha256": strings.Repeat("f", 64), "rank": number(1)}
		generated, err := compileRank(request, mapOf(clone(recipe)), model)
		if err != nil {
			t.Fatal(err)
		}
		files := map[string]string{}
		for _, name := range groupRuntimeFiles {
			base := "apps/model-service"
			if name == "group_network.py" || name == "group_mesh.py" || name == "group_inference.py" {
				base = "pantheon/models"
			}
			data, err := os.ReadFile(filepath.Join(runtime, base, name))
			if err != nil {
				t.Fatal(err)
			}
			files[name] = string(data)
		}
		_, digest, err := packageRank(files, generated)
		if err != nil || digest != want["package"] {
			t.Fatalf("package %v %s != %s", err, digest, want["package"])
		}
		row := map[string]any{"owner": plan["owner"], "group_id": plan["group_id"], "plan": plan, "topology": peers.Document(),
			"source_sha256": strings.Repeat("f", 64), "ca_sha256": strings.Repeat("e", 64),
			"artifacts": []any{map[string]any{"rank": number(0), "digest": strings.Repeat("1", 64)},
				map[string]any{"rank": number(1), "digest": strings.Repeat("2", 64)}}}
		if got := lifecyclePlan(mapOf(clone(row))); !same(got, want["lifecycle"]) {
			t.Fatalf("lifecycle\n%s\n%s", Compact(got), Compact(want["lifecycle"]))
		}
	}
}

func TestGroupPlanRejectsOversizedModel(t *testing.T) {
	plan, record := groupFixture(false)
	for _, m := range listOf(plan["members"]) {
		mapOf(listOf(mapOf(mapOf(m)["resources"])["devices"])[0])["memory_bytes"] = number(4 << 30)
	}
	model, err := modelDescriptor(record)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := validateGroupPlan(plan, model); err == nil || !strings.Contains(err.Error(), "exceed a declared GPU budget") {
		t.Fatalf("got %v", err)
	}
}

func TestInspectMemberPreparedAndMissing(t *testing.T) {
	plan, _ := groupFixture(false)
	peers, _ := topologyFor(plan)
	row := lifecyclePlan(map[string]any{"owner": plan["owner"], "group_id": plan["group_id"], "plan": plan, "topology": peers.Document(),
		"source_sha256": strings.Repeat("f", 64), "ca_sha256": strings.Repeat("e", 64),
		"artifacts": []any{map[string]any{"rank": number(0), "digest": strings.Repeat("1", 64)},
			map[string]any{"rank": number(1), "digest": strings.Repeat("2", 64)}}})
	member := members(row)[0]
	owner := strOf(row["owner"])
	snapshot := map[string]any{"protocol": number(1), "owner": owner, "node_id": "n_one"}
	if o := inspectMember(owner, member, snapshot); o["state"] != "unsubmitted" || o["clean"] != true {
		t.Fatalf("fresh %v", o)
	}
	mapOf(member["install"])["sent"], mapOf(member["install"])["staged"] = true, true
	if o := inspectMember(owner, member, snapshot); o["missing"] != "install" {
		t.Fatalf("missing install %v", o)
	}
	install := requestOf(member, "install")
	snapshot["operations"] = map[string]any{strOf(install["operation_id"]): map[string]any{"state": "succeeded", "request": clone(install)}}
	snapshot["installations"] = map[string]any{strOf(install["digest"]): map[string]any{"digest": install["digest"], "state": "installed"}}
	if o := inspectMember(owner, member, snapshot); o["state"] != "installed" {
		t.Fatalf("installed %v", o)
	}
	mapOf(member["prepare"])["sent"] = true
	prepare := requestOf(member, "prepare")
	identity := instanceIdentity(owner, mapOf(member["target"]))
	mapOf(snapshot["operations"])[strOf(prepare["operation_id"])] = map[string]any{"state": "succeeded", "request": clone(prepare)}
	snapshot["instances"] = map[string]any{identity: map[string]any{"instance_id": identity, "digest": install["digest"],
		"scope": install["scope"], "generation": number(1), "state": "prepared", "start_preparation_id": prepare["operation_id"],
		"reservations": map[string]any{"x": 1}}}
	if o := inspectMember(owner, member, mapOf(clone(snapshot))); o["state"] != "prepared" || intOf(o["stop_generation"]) != 1 {
		t.Fatalf("prepared %v", o)
	}
}
