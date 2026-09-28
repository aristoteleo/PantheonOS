// Package hpc lets a Node on an HPC login node start Fleet Nodes on the
// cluster's compute nodes through Slurm. The login node never holds the
// user's Fleet key: the owner's Agent mints a one-use join token per job and
// the job script hands it to `fleet up` on the compute node.
package hpc

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// JobName marks the jobs this launcher submits in squeue/sacct.
const JobName = "pantheon-fleet"

// keepEnded is how long an ended job stays in the list.
const keepEnded = 7 * 24 * time.Hour

var (
	slurmName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,64}$`)
	gpuType   = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,32}$`)
	nodeName  = regexp.MustCompile(`^[A-Za-z0-9._-]{1,48}$`)
	jobID     = regexp.MustCompile(`^[0-9]{1,12}$`)
)

// Available reports whether this host can submit Slurm jobs and is not itself
// inside one (a compute node started by a job must not launch further jobs).
func Available() bool {
	if os.Getenv("SLURM_JOB_ID") != "" {
		return false
	}
	_, err := exec.LookPath("sbatch")
	return err == nil
}

// Launcher submits and tracks compute-node jobs for one Fleet.
type Launcher struct {
	Root       string // private directory for job scripts, logs and records
	Executable string // the fleet binary; must be on a filesystem compute nodes share
	Controller string // Controller URL the compute node joins through
	NodeID     string // this login node, recorded as the launcher label
	// Run executes a Slurm command (tests replace it).
	Run func(ctx context.Context, name string, args ...string) ([]byte, error)
	mu  sync.Mutex
}

func (l *Launcher) run(ctx context.Context, name string, args ...string) ([]byte, error) {
	if l.Run != nil {
		return l.Run(ctx, name, args...)
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, args...).CombinedOutput()
	if err != nil {
		return out, fmt.Errorf("%s: %v: %s", name, err, strings.TrimSpace(tail(out, 400)))
	}
	return out, nil
}

// Partition summarizes one Slurm partition for choosing where to run.
type Partition struct {
	Name      string   `json:"name"`
	Default   bool     `json:"default"`
	TimeLimit string   `json:"time_limit"`
	Nodes     int      `json:"nodes"`
	IdleNodes int      `json:"idle_nodes"`
	MaxCPUs   int      `json:"max_cpus"`
	MaxMemGB  int      `json:"max_mem_gb"`
	GPUs      []string `json:"gpus,omitempty"` // GRES such as gpu:a100:4
}

// Partitions lists the partitions this user can submit to.
func (l *Launcher) Partitions(ctx context.Context) ([]Partition, error) {
	out, err := l.run(ctx, "sinfo", "-h", "-o", "%P|%a|%l|%D|%T|%c|%m|%G")
	if err != nil {
		return nil, err
	}
	byName := map[string]*Partition{}
	var order []string
	for _, line := range strings.Split(string(out), "\n") {
		f := strings.Split(strings.TrimSpace(line), "|")
		if len(f) != 8 || f[1] != "up" {
			continue
		}
		name, def := strings.TrimSuffix(f[0], "*"), strings.HasSuffix(f[0], "*")
		p := byName[name]
		if p == nil {
			p = &Partition{Name: name, Default: def, TimeLimit: f[2]}
			byName[name] = p
			order = append(order, name)
		}
		nodes, _ := strconv.Atoi(f[3])
		p.Nodes += nodes
		if f[4] == "idle" || f[4] == "mixed" {
			p.IdleNodes += nodes
		}
		if cpus, _ := strconv.Atoi(strings.TrimSuffix(f[5], "+")); cpus > p.MaxCPUs {
			p.MaxCPUs = cpus
		}
		if mem, _ := strconv.Atoi(strings.TrimSuffix(f[6], "+")); mem/1024 > p.MaxMemGB {
			p.MaxMemGB = mem / 1024
		}
		for _, g := range strings.Split(f[7], ",") {
			if g = strings.TrimSpace(g); strings.HasPrefix(g, "gpu") && !contains(p.GPUs, g) {
				p.GPUs = append(p.GPUs, g)
			}
		}
	}
	parts := make([]Partition, 0, len(order))
	for _, name := range order {
		sort.Strings(byName[name].GPUs)
		parts = append(parts, *byName[name])
	}
	return parts, nil
}

// Request describes one compute node to start.
type Request struct {
	JoinToken string `json:"join_token"`
	Name      string `json:"name"`
	Partition string `json:"partition"`
	Account   string `json:"account,omitempty"`
	QOS       string `json:"qos,omitempty"`
	CPUs      int    `json:"cpus"`
	MemGB     int    `json:"mem_gb"`
	GPUs      int    `json:"gpus,omitempty"`
	GPUType   string `json:"gpu_type,omitempty"`
	Minutes   int    `json:"minutes"`
}

func (q Request) validate() error {
	switch {
	case len(q.JoinToken) < 16 || len(q.JoinToken) > 8192 || strings.ContainsAny(q.JoinToken, "'\n\r\x00"):
		return errors.New("a one-use join token is required")
	case !nodeName.MatchString(q.Name):
		return errors.New("name: letters, digits, '.', '_' or '-' (at most 48)")
	case !slurmName.MatchString(q.Partition):
		return errors.New("partition is required")
	case q.Account != "" && !slurmName.MatchString(q.Account):
		return errors.New("invalid account")
	case q.QOS != "" && !slurmName.MatchString(q.QOS):
		return errors.New("invalid QOS")
	case q.CPUs < 1 || q.CPUs > 512:
		return errors.New("cpus must be 1-512")
	case q.MemGB < 1 || q.MemGB > 8192:
		return errors.New("memory must be 1-8192 GB")
	case q.GPUs < 0 || q.GPUs > 16:
		return errors.New("gpus must be 0-16")
	case q.GPUType != "" && (q.GPUs == 0 || !gpuType.MatchString(q.GPUType)):
		return errors.New("invalid GPU type")
	case q.Minutes < 1 || q.Minutes > 14*24*60:
		return errors.New("time must be 1 minute to 14 days")
	}
	return nil
}

// Job is one submitted compute node.
type Job struct {
	JobID       string    `json:"job_id"`
	Name        string    `json:"name"`
	Partition   string    `json:"partition"`
	CPUs        int       `json:"cpus"`
	MemGB       int       `json:"mem_gb"`
	GPUs        int       `json:"gpus,omitempty"`
	GPUType     string    `json:"gpu_type,omitempty"`
	Minutes     int       `json:"minutes"`
	SubmittedAt time.Time `json:"submitted_at"`
	State       string    `json:"state,omitempty"`   // Slurm state: PENDING, RUNNING, COMPLETED, …
	Reason      string    `json:"reason,omitempty"`  // why it waits, or the host it runs on
	Elapsed     string    `json:"elapsed,omitempty"` // time used so far
	NodeName    string    `json:"node_name"`         // the Fleet Node name the job joins as
	LogTail     string    `json:"log_tail,omitempty"`
}

func quote(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }

// Submit writes the job script and hands it to sbatch.
func (l *Launcher) Submit(ctx context.Context, q Request) (Job, error) {
	if err := q.validate(); err != nil {
		return Job{}, err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	id := make([]byte, 6)
	rand.Read(id) //nolint:errcheck
	dir := filepath.Join(l.Root, hex.EncodeToString(id))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return Job{}, err
	}
	token := filepath.Join(dir, "join-token")
	if err := os.WriteFile(token, []byte(q.JoinToken), 0o600); err != nil {
		return Job{}, err
	}
	labels := "hpc,slurm,partition:" + q.Partition + ",launcher:" + l.NodeID
	// The token is read and deleted before `fleet up` runs, so it never shows
	// in the process list or survives the job. Each job gets its own identity.
	script := strings.Join([]string{
		"#!/bin/bash",
		"set -eu",
		"token=$(cat " + quote(token) + ")",
		"rm -f " + quote(token),
		`state="${TMPDIR:-/tmp}/pantheon-fleet-$SLURM_JOB_ID"`,
		`mkdir -p "$state"`,
		"exec " + quote(l.Executable) + " up --join-token \"$token\" --controller " + quote(l.Controller) +
			" --name " + quote(q.Name) + "-\"$SLURM_JOB_ID\" --state-dir \"$state\"" +
			" --labels " + quote(labels) + ",slurm-job:\"$SLURM_JOB_ID\"" +
			" --kind machine --no-auto-update --no-capture-setup",
		"",
	}, "\n")
	path := filepath.Join(dir, "job.sh")
	if err := os.WriteFile(path, []byte(script), 0o700); err != nil {
		return Job{}, err
	}
	args := []string{"--parsable", "--job-name=" + JobName, "--output=" + filepath.Join(dir, "slurm-%j.out"),
		"--partition=" + q.Partition, "--nodes=1", "--ntasks=1", "--cpus-per-task=" + strconv.Itoa(q.CPUs),
		"--mem=" + strconv.Itoa(q.MemGB) + "G", "--time=" + strconv.Itoa(q.Minutes)}
	if q.GPUs > 0 {
		gres := "gpu:" + strconv.Itoa(q.GPUs)
		if q.GPUType != "" {
			gres = "gpu:" + q.GPUType + ":" + strconv.Itoa(q.GPUs)
		}
		args = append(args, "--gres="+gres)
	}
	if q.Account != "" {
		args = append(args, "--account="+q.Account)
	}
	if q.QOS != "" {
		args = append(args, "--qos="+q.QOS)
	}
	out, err := l.run(ctx, "sbatch", append(args, path)...)
	if err != nil {
		os.RemoveAll(dir)
		return Job{}, err
	}
	id2 := strings.TrimSpace(strings.Split(strings.TrimSpace(string(out)), ";")[0])
	if !jobID.MatchString(id2) {
		os.RemoveAll(dir)
		return Job{}, fmt.Errorf("sbatch returned %q", tail(out, 200))
	}
	job := Job{JobID: id2, Name: q.Name, Partition: q.Partition, CPUs: q.CPUs, MemGB: q.MemGB,
		GPUs: q.GPUs, GPUType: q.GPUType, Minutes: q.Minutes, SubmittedAt: time.Now().UTC(),
		State: "PENDING", NodeName: q.Name + "-" + id2}
	b, _ := json.Marshal(job)
	if err := os.WriteFile(filepath.Join(dir, "job.json"), b, 0o600); err != nil {
		return job, err
	}
	return job, nil
}

// records reads the jobs this launcher submitted, keyed by job id.
func (l *Launcher) records() (map[string]Job, map[string]string) {
	jobs, dirs := map[string]Job{}, map[string]string{}
	entries, _ := os.ReadDir(l.Root)
	for _, e := range entries {
		dir := filepath.Join(l.Root, e.Name())
		b, err := os.ReadFile(filepath.Join(dir, "job.json"))
		var j Job
		if err != nil || json.Unmarshal(b, &j) != nil || !jobID.MatchString(j.JobID) {
			continue
		}
		jobs[j.JobID], dirs[j.JobID] = j, dir
	}
	return jobs, dirs
}

// Jobs lists submitted jobs with their current Slurm state, newest first.
func (l *Launcher) Jobs(ctx context.Context) ([]Job, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	jobs, dirs := l.records()
	if len(jobs) == 0 {
		return []Job{}, nil
	}
	ids := make([]string, 0, len(jobs))
	for id := range jobs {
		ids = append(ids, id)
	}
	live := map[string][]string{}
	if out, err := l.run(ctx, "squeue", "-h", "-j", strings.Join(ids, ","), "-o", "%i|%T|%M|%R"); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			if f := strings.Split(strings.TrimSpace(line), "|"); len(f) == 4 {
				live[f[0]] = f
			}
		}
	}
	var ended []string
	for id := range jobs {
		if _, ok := live[id]; !ok {
			ended = append(ended, id)
		}
	}
	final := map[string]string{}
	if len(ended) > 0 {
		// sacct may be unavailable; those jobs then read as ENDED.
		if out, err := l.run(ctx, "sacct", "-n", "-X", "-P", "-j", strings.Join(ended, ","), "-o", "JobID,State"); err == nil {
			for _, line := range strings.Split(string(out), "\n") {
				if f := strings.Split(strings.TrimSpace(line), "|"); len(f) == 2 {
					final[f[0]] = strings.Fields(f[1] + " ")[0]
				}
			}
		}
	}
	list := make([]Job, 0, len(jobs))
	for id, j := range jobs {
		if f, ok := live[id]; ok {
			j.State, j.Elapsed, j.Reason = f[1], f[2], f[3]
		} else {
			os.Remove(filepath.Join(dirs[id], "join-token"))
			j.State = final[id]
			if j.State == "" {
				j.State = "ENDED"
			}
			if time.Since(j.SubmittedAt) > keepEnded {
				os.RemoveAll(dirs[id])
				continue
			}
			if log, err := os.ReadFile(filepath.Join(dirs[id], "slurm-"+id+".out")); err == nil {
				j.LogTail = tail(log, 2000)
			}
		}
		list = append(list, j)
	}
	sort.Slice(list, func(a, b int) bool { return list[a].SubmittedAt.After(list[b].SubmittedAt) })
	return list, nil
}

// Cancel stops a job this launcher submitted (never any other job).
func (l *Launcher) Cancel(ctx context.Context, id string) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	jobs, dirs := l.records()
	if !jobID.MatchString(id) || jobs[id].JobID == "" {
		return errors.New("not a job started from this Fleet node")
	}
	if _, err := l.run(ctx, "scancel", id); err != nil {
		return err
	}
	os.Remove(filepath.Join(dirs[id], "join-token")) // a job cancelled while queued never used it
	return nil
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

func tail(b []byte, n int) string {
	if len(b) > n {
		b = b[len(b)-n:]
	}
	return string(b)
}
