// Package hpcproxy implements Fleet operations inside existing Slurm allocations.
package hpcproxy

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

//go:embed remote.py
var remoteProgram string

type Stream func(context.Context, string, bool, io.Reader, io.Writer, io.Writer, ...string) error

type Request struct {
	Operation  string      `json:"operation"`
	Allocation string      `json:"allocation"`
	JobID      string      `json:"job_id"`
	Task       *proto.Task `json:"task,omitempty"`
	Path       string      `json:"path,omitempty"`
	Offset     int64       `json:"offset,omitempty"`
	Size       int         `json:"size,omitempty"`
	Data       string      `json:"data,omitempty"`
	Overwrite  bool        `json:"overwrite,omitempty"`
}

type output struct {
	bytes.Buffer
	overflow bool
}

func (o *output) Write(p []byte) (int, error) {
	n := len(p)
	remaining := 512*1024 - o.Len()
	if len(p) > remaining {
		p = p[:remaining]
		o.overflow = true
	}
	o.Buffer.Write(p)
	return n, nil
}

func Execute(ctx context.Context, stream Stream, cluster string, job hpc.Job, req Request) (json.RawMessage, error) {
	if job.State != "RUNNING" || job.AllocationID == "" {
		return nil, fmt.Errorf("allocation is not ready")
	}
	req.Allocation, req.JobID = job.AllocationID, job.JobID
	seconds := 60
	if req.Task != nil {
		seconds = req.Task.TimeoutS
		if seconds <= 0 {
			seconds = 60
		}
		seconds = min(seconds, 3600)
		req.Task.TimeoutS = seconds
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(seconds+3)*time.Second)
	defer cancel()
	input, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	if len(input) > 256*1024 {
		return nil, fmt.Errorf("request too large")
	}
	argv := []string{"srun", "--jobid=" + job.JobID, "--overlap", "--exact", "--nodes=1", "--ntasks=1", "--cpus-per-task=1", "--mem=256M", "--gres=none", "--time=" + strconv.Itoa((seconds+59)/60), "python3", "-c", remoteProgram}
	// Commands may use the allocation resources; metadata/file operations reserve
	// only a small CPU step and no GPU. No task ever runs on the login node.
	if req.Operation == "task" {
		argv[6] = "--cpus-per-task=" + strconv.Itoa(job.CPUs)
		argv[7] = "--mem=" + strconv.Itoa(job.MemGB) + "G"
		if job.GPUs > 0 {
			argv[8] = "--gres=gpu:" + strconv.Itoa(job.GPUs)
		}
	}
	var out, stderr output
	err = stream(ctx, cluster, req.Operation != "probe", bytes.NewReader(input), &out, &stderr, argv...)
	if err != nil {
		return nil, fmt.Errorf("allocation step: %w: %.400s", err, stderr.String())
	}
	if out.overflow {
		return nil, fmt.Errorf("allocation reply exceeded limit")
	}
	if !json.Valid(out.Bytes()) {
		return nil, fmt.Errorf("invalid allocation reply: %.400s", stderr.String())
	}
	return append(json.RawMessage(nil), out.Bytes()...), nil
}
