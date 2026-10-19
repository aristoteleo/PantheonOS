package hpc

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type fakeSlurm struct {
	calls [][]string
	out   map[string]string
}

func (f *fakeSlurm) run(_ context.Context, name string, args ...string) ([]byte, error) {
	f.calls = append(f.calls, append([]string{name}, args...))
	return []byte(f.out[name]), nil
}

func launcher(t *testing.T, f *fakeSlurm) *Launcher {
	return &Launcher{Root: t.TempDir(), Executable: "/home/u/.local/bin/fleet", Controller: "https://fleet.example",
		NodeID: "n_login", Run: f.run}
}

func TestSubmitWritesAPrivateJobThatJoinsWithTheToken(t *testing.T) {
	f := &fakeSlurm{out: map[string]string{"sbatch": "4242;sherlock\n"}}
	l := launcher(t, f)
	job, err := l.Submit(context.Background(), Request{JoinToken: "jt_0123456789abcdef", Name: "gpu", Partition: "gpu",
		CPUs: 8, MemGB: 64, GPUs: 2, GPUType: "a100", Minutes: 120, Account: "lab"})
	if err != nil || job.JobID != "4242" || job.NodeName != "gpu-4242" {
		t.Fatalf("%+v %v", job, err)
	}
	args := strings.Join(f.calls[0], " ")
	for _, want := range []string{"--parsable", "--job-name=pantheon-fleet", "--partition=gpu", "--cpus-per-task=8",
		"--mem=64G", "--time=120", "--gres=gpu:a100:2", "--account=lab"} {
		if !strings.Contains(args, want) {
			t.Fatalf("sbatch args %q lack %s", args, want)
		}
	}
	if strings.Contains(args, "jt_0123456789abcdef") {
		t.Fatal("token on the sbatch command line")
	}
	script := f.calls[0][len(f.calls[0])-1]
	body, _ := os.ReadFile(script)
	for _, want := range []string{"rm -f", "--join-token \"$token\"", "--controller 'https://fleet.example'",
		"slurm-job:\"$SLURM_JOB_ID\"", "launcher:n_login", "--no-auto-update"} {
		if !strings.Contains(string(body), want) {
			t.Fatalf("script lacks %s:\n%s", want, body)
		}
	}
	info, _ := os.Stat(filepath.Join(filepath.Dir(script), "join-token"))
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("token file mode %v", info.Mode())
	}
}

func TestSubmitRejectsInjectedValues(t *testing.T) {
	l := launcher(t, &fakeSlurm{})
	base := Request{JoinToken: "jt_0123456789abcdef", Name: "n", Partition: "p", CPUs: 1, MemGB: 1, Minutes: 1}
	for _, bad := range []func(*Request){
		func(q *Request) { q.Partition = "p --wrap=id" },
		func(q *Request) { q.Name = "a;rm" },
		func(q *Request) { q.JoinToken = "x'; id; '0123456789" },
		func(q *Request) { q.GPUType = "a100" },
		func(q *Request) { q.Minutes = 0 },
	} {
		q := base
		bad(&q)
		if _, err := l.Submit(context.Background(), q); err == nil {
			t.Fatalf("accepted %+v", q)
		}
	}
}

func TestJobsAndCancelOnlyCoverOwnJobs(t *testing.T) {
	f := &fakeSlurm{out: map[string]string{"sbatch": "7\n", "squeue": "7|RUNNING|1:02|sh03-12n07|xiaojie\n"}}
	l := launcher(t, f)
	if _, err := l.Submit(context.Background(), Request{JoinToken: "jt_0123456789abcdef", Name: "c", Partition: "normal",
		CPUs: 1, MemGB: 4, Minutes: 30}); err != nil {
		t.Fatal(err)
	}
	jobs, err := l.Jobs(context.Background())
	if err != nil || len(jobs) != 1 || jobs[0].State != "RUNNING" || jobs[0].Reason != "sh03-12n07" || jobs[0].Partition != "xiaojie" {
		t.Fatalf("%+v %v", jobs, err)
	}
	if err := l.Cancel(context.Background(), "99"); err == nil {
		t.Fatal("cancelled a foreign job")
	}
	if err := l.Cancel(context.Background(), "7"); err != nil {
		t.Fatal(err)
	}
	if last := f.calls[len(f.calls)-1]; strings.Join(last, " ") != "scancel 7" {
		t.Fatalf("%v", last)
	}
}

func TestPartitionsSummarizeSinfo(t *testing.T) {
	f := &fakeSlurm{out: map[string]string{"sinfo": "normal*|up|7-00:00:00|10|idle|32|256000|(null)\n" +
		"normal*|up|7-00:00:00|5|allocated|64|512000|(null)\ngpu|up|2-00:00:00|3|mixed|32|256000|gpu:a100:4\n" +
		"old|down|1:00:00|1|idle|4|1000|(null)\n"}}
	parts, err := launcher(t, f).Partitions(context.Background())
	if err != nil || len(parts) != 2 {
		t.Fatalf("%+v %v", parts, err)
	}
	if p := parts[0]; p.Name != "normal" || !p.Default || p.Nodes != 15 || p.IdleNodes != 10 || p.MaxCPUs != 64 || p.MaxMemGB != 500 {
		t.Fatalf("%+v", p)
	}
	if p := parts[1]; p.GPUs[0] != "gpu:a100:4" || p.IdleNodes != 3 {
		t.Fatalf("%+v", p)
	}
}

func TestSessionJobsRunActualAppWithoutFleetOnTheCluster(t *testing.T) {
	var calls []string
	var script, allocation string
	l := &Launcher{Root: t.TempDir(), Remote: func(_ context.Context, stdin []byte, argv ...string) ([]byte, error) {
		calls = append(calls, strings.Join(argv, " "))
		switch argv[0] {
		case "sbatch":
			script = string(stdin)
			for _, a := range argv {
				if strings.HasPrefix(a, "--comment=") {
					allocation = strings.TrimPrefix(a, "--comment=")
				}
			}
			return []byte("9001\n"), nil
		case "scontrol":
			return []byte("Comment=" + allocation), nil
		case "squeue":
			return []byte("9001|RUNNING|0:10|sh04-04n05|" + allocation + "\n"), nil
		}
		return nil, nil
	}}
	job, err := l.Submit(context.Background(), Request{Name: "gpu", Partition: "xiaojie", CPUs: 8, MemGB: 64, GPUs: 1, Minutes: 60, Service: &HTTPService{Name: "web", Argv: []string{"python3", "-m", "http.server", "${PORT}", "--bind", "${HOST}"}}})
	if err != nil || job.JobID != "9001" {
		t.Fatalf("%+v %v", job, err)
	}
	if !strings.Contains(script, "exec python3 -u -c") || strings.Contains(script, "sleep 3570") || strings.Contains(script, "fleet") && strings.Contains(script, " up ") {
		t.Fatalf("script:\n%s", script)
	}
	if !strings.Contains(calls[1], "--output=.pantheon-fleet/hpc/slurm-%j.out") || !strings.Contains(calls[1], "--gres=gpu:1") {
		t.Fatalf("%v", calls)
	}
	jobs, err := l.Jobs(context.Background())
	if err != nil || len(jobs) != 1 || jobs[0].State != "RUNNING" {
		t.Fatalf("%+v %v", jobs, err)
	}
	if err := l.Cancel(context.Background(), "9001"); err != nil || calls[len(calls)-1] != "scancel 9001" {
		t.Fatalf("%v %v", err, calls)
	}
}

func TestSessionPollingIsCachedAndFailureIsNotCompletion(t *testing.T) {
	calls := 0
	fail := false
	marker := ""
	l := &Launcher{Root: t.TempDir()}
	remote := func(_ context.Context, _ []byte, argv ...string) ([]byte, error) {
		switch argv[0] {
		case "sbatch":
			for _, a := range argv {
				if strings.HasPrefix(a, "--comment=") {
					marker = strings.TrimPrefix(a, "--comment=")
				}
			}
			return []byte("123"), nil
		case "squeue":
			calls++
			if fail {
				return nil, fmt.Errorf("network down")
			}
			return []byte("123|RUNNING|0:00|compute01|" + marker + "|xiaojie"), nil
		}
		return nil, nil
	}
	l.Remote, l.Query = remote, remote
	_, err := l.Submit(context.Background(), Request{Name: "test", Partition: "normal", CPUs: 1, MemGB: 1, Minutes: 5, Service: &HTTPService{Name: "web", Argv: []string{"python3", "-m", "http.server", "${PORT}"}}})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 5; i++ {
		jobs, err := l.Jobs(context.Background())
		if err != nil || jobs[0].State != "RUNNING" || jobs[0].Partition != "xiaojie" {
			t.Fatal(jobs, err)
		}
	}
	if calls != 1 {
		t.Fatalf("polled %d times", calls)
	}
	l.lastPoll = time.Time{}
	fail = true
	if _, err := l.Jobs(context.Background()); err == nil {
		t.Fatal("query failure became a completed job")
	}
}

func TestRecycledJobCannotExecuteOrBeCancelled(t *testing.T) {
	l := &Launcher{Root: t.TempDir()}
	cancelled := false
	l.Remote = func(_ context.Context, _ []byte, argv ...string) ([]byte, error) {
		switch argv[0] {
		case "sbatch":
			return []byte("123"), nil
		case "squeue":
			return []byte("123|RUNNING|0:00|compute01|someone-elses-allocation"), nil
		case "sacct":
			return []byte("123|RUNNING"), nil
		case "scontrol":
			return []byte("JobId=123 Comment=someone-elses-allocation"), nil
		case "scancel":
			cancelled = true
		}
		return nil, nil
	}
	_, err := l.Submit(context.Background(), Request{Name: "test", Partition: "normal", CPUs: 1, MemGB: 1, Minutes: 5, Service: &HTTPService{Name: "web", Argv: []string{"python3", "-m", "http.server", "${PORT}"}}})
	if err != nil {
		t.Fatal(err)
	}
	jobs, err := l.Jobs(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if jobs[0].State != "ENDED" {
		t.Fatalf("reused ID is still eligible: %+v", jobs)
	}
	if err := l.Cancel(context.Background(), "123"); err == nil || cancelled {
		t.Fatal("cancelled someone else's allocation")
	}
}

func TestSessionRejectsEmptyWorkloadBeforeRemoteAccess(t *testing.T) {
	l := &Launcher{Root: t.TempDir(), Remote: func(context.Context, []byte, ...string) ([]byte, error) {
		t.Fatal("accessed cluster for empty workload")
		return nil, nil
	}}
	if _, err := l.Submit(context.Background(), Request{Name: "empty", Partition: "normal", CPUs: 1, MemGB: 1, Minutes: 20}); err == nil {
		t.Fatal("accepted placeholder allocation")
	}
}

func TestSessionOrdinaryArtifactIsThePrimaryJobWorkload(t *testing.T) {
	artifact := App{Digest: strings.Repeat("a", 64), Scope: "app"}
	prepared := false
	var script string
	l := &Launcher{Root: t.TempDir(), PrepareApp: func(_ context.Context, allocation string, got App) (*HTTPService, error) {
		if got != artifact || allocation == "" {
			t.Fatalf("invalid preparation: %+v %s", got, allocation)
		}
		prepared = true
		return &HTTPService{Name: "app-worker", Argv: []string{"/private/assets/worker", "/private/assets/config"}, Cwd: ".", StartupSeconds: 60}, nil
	}, Remote: func(_ context.Context, stdin []byte, argv ...string) ([]byte, error) {
		if argv[0] == "sbatch" {
			if !prepared {
				t.Fatal("submitted before staging")
			}
			script = string(stdin)
			return []byte("9002\n"), nil
		}
		return nil, nil
	}}
	job, err := l.Submit(context.Background(), Request{Name: "ordinary", Partition: "normal", CPUs: 1, MemGB: 2, Minutes: 20, App: &artifact})
	if err != nil {
		t.Fatal(err)
	}
	if job.App == nil || *job.App != artifact || job.Service == nil || job.Service.Name != "app-worker" {
		t.Fatalf("%+v", job)
	}
	if script == "" || strings.Contains(script, "join_token") || strings.Contains(script, "fleet up") {
		t.Fatal("unexpected job script")
	}
	records, _ := l.records()
	loaded := records["9002"]
	if loaded.App == nil || *loaded.App != artifact {
		t.Fatalf("artifact identity not persisted: %+v", loaded)
	}
}
