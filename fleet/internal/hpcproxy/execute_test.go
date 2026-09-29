package hpcproxy

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestAllocationTasksAndFiles(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 not installed")
	}
	home := t.TempDir()
	job := hpc.Job{JobID: "123", AllocationID: strings.Repeat("a", 32), State: "RUNNING", CPUs: 2, MemGB: 4, GPUs: 1}
	stream := func(ctx context.Context, cluster string, activity bool, in io.Reader, out, stderr io.Writer, argv ...string) error {
		if cluster != "cluster" || argv[0] != "srun" || argv[1] != "--jobid=123" {
			t.Fatalf("not a compute step: %v", argv[:2])
		}
		c := exec.CommandContext(ctx, python, "-c", argv[len(argv)-1])
		c.Env = append(os.Environ(), "HOME="+home, "SLURM_JOB_ID=123")
		c.Stdin, c.Stdout, c.Stderr = in, out, stderr
		return c.Run()
	}
	call := func(req Request) map[string]any {
		out, err := Execute(context.Background(), stream, "cluster", job, req)
		if err != nil {
			t.Fatal(err)
		}
		var v map[string]any
		if json.Unmarshal(out, &v) != nil {
			t.Fatal(string(out))
		}
		return v
	}
	result := call(Request{Operation: "task", Task: &proto.Task{TaskID: "t", Kind: "python", Code: "import os;print(os.environ['SLURM_JOB_ID']);print('x'*100000)", TimeoutS: 5}})
	if result["exit_code"] != float64(0) || !strings.HasPrefix(result["stdout"].(string), "123\n") || result["truncated"] != true {
		t.Fatal(result)
	}
	result = call(Request{Operation: "task", Task: &proto.Task{TaskID: "timeout", Kind: "shell", Code: "sleep 30", TimeoutS: 1}})
	if result["error"] != "timeout" {
		t.Fatal(result)
	}
	data := base64.StdEncoding.EncodeToString([]byte{0, 1, 255, 10})
	result = call(Request{Operation: "write", Path: "hello.bin", Data: data})
	if result["size"] != float64(4) {
		t.Fatal(result)
	}
	result = call(Request{Operation: "read", Path: "hello.bin", Size: 2})
	if result["eof"] != false || result["next_offset"] != float64(2) {
		t.Fatal(result)
	}
	result = call(Request{Operation: "write", Path: "hello.bin", Data: data})
	if result["error"] == nil {
		t.Fatal("overwrote without consent")
	}
	for _, path := range []string{"../../outside", filepath.Join(home, "outside")} {
		result = call(Request{Operation: "read", Path: path})
		if result["error"] == nil {
			t.Fatal("escaped workspace")
		}
	}
	root := filepath.Join(home, ".pantheon-fleet", "workspaces", job.AllocationID)
	os.Symlink(home, filepath.Join(root, "escape"))
	result = call(Request{Operation: "read", Path: "escape/outside"})
	if result["error"] == nil {
		t.Fatal("followed escaping symlink")
	}
	result = call(Request{Operation: "list"})
	if len(result["entries"].([]any)) != 2 {
		t.Fatal(result)
	}
	job.State = "PENDING"
	if _, err := Execute(context.Background(), stream, "cluster", job, Request{Operation: "probe"}); err == nil {
		t.Fatal("executed queued allocation")
	}
}

func TestTaskResourcesAndRequestIdentityAreServerOwned(t *testing.T) {
	j := hpc.Job{JobID: "42", AllocationID: strings.Repeat("b", 32), State: "RUNNING", CPUs: 4, MemGB: 8}
	stream := func(_ context.Context, _ string, activity bool, in io.Reader, out, stderr io.Writer, argv ...string) error {
		var req Request
		json.NewDecoder(in).Decode(&req)
		if req.Allocation != j.AllocationID || req.JobID != "42" || !activity {
			t.Fatal(req)
		}
		if argv[6] != "--cpus-per-task=4" || argv[7] != "--mem=8G" || argv[8] != "--gres=none" {
			t.Fatal(argv[:10])
		}
		io.WriteString(out, `{"exit_code":0}`)
		return nil
	}
	_, err := Execute(context.Background(), stream, "x", j, Request{Operation: "task", Allocation: "foreign", JobID: "1", Task: &proto.Task{Kind: "shell", Code: "true"}})
	if err != nil {
		t.Fatal(err)
	}
}
