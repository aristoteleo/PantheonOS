package runner

import (
	"context"
	"encoding/json"
	"path/filepath"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
	"github.com/aristoteleo/pantheon-fleet/internal/hpcconn"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
)

// EnableHPCClusters lets this Node connect to HPC clusters over sessions the
// user signs in to from PantheonOS.
func (r *Runner) EnableHPCClusters(m *hpcconn.Manager) { r.clusters = m }

func (r *Runner) scheduler(id string) *hpc.Launcher {
	r.hpcMu.Lock()
	defer r.hpcMu.Unlock()
	if r.schedulers == nil {
		r.schedulers = map[string]*hpc.Launcher{}
	}
	if scheduler := r.schedulers[id]; scheduler != nil {
		return scheduler
	}
	scheduler := &hpc.Launcher{Root: filepath.Join(r.clusters.Root, "jobs", id),
		PrepareApp: func(ctx context.Context, allocation string, app hpc.App) (*hpc.HTTPService, error) {
			return r.prepareHPCApp(ctx, id, allocation, app)
		},
		Remote: func(ctx context.Context, stdin []byte, argv ...string) ([]byte, error) {
			return r.clusters.Run(ctx, id, stdin, argv...)
		},
		Query: func(ctx context.Context, stdin []byte, argv ...string) ([]byte, error) {
			return r.clusters.RunQuery(ctx, id, stdin, argv...)
		},
	}
	r.schedulers[id] = scheduler
	return scheduler
}

// handleHPCCluster serves {"type":"hpc_cluster","method":...,"cluster_id":...}.
func (r *Runner) handleHPCCluster(m *nats.Msg) {
	if r.clusters == nil {
		r.replyErr(m, "this node cannot connect to HPC clusters (needs macOS or Linux with ssh)")
		return
	}
	var req struct {
		Method    string            `json:"method"`
		ClusterID string            `json:"cluster_id"`
		Cluster   hpcconn.Cluster   `json:"cluster"`
		Answer    hpcconn.Encrypted `json:"answer"`
		Remember  bool              `json:"remember"`
		Request   hpc.Request       `json:"request"`
		JobID     string            `json:"job_id"`
	}
	if err := json.Unmarshal(m.Data, &req); err != nil {
		r.replyErr(m, "hpc_cluster: "+err.Error())
		return
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
		defer cancel()
		var (
			out any
			err error
		)
		id := req.ClusterID
		if req.Method != "list" && req.Method != "save" {
			if _, err := r.clusters.Status(id); err != nil {
				r.replyErr(m, err.Error())
				return
			}
		}
		switch req.Method {
		case "list":
			out = map[string]any{"clusters": r.clusters.List()}
		case "save":
			var c hpcconn.Cluster
			c, err = r.clusters.Save(req.Cluster)
			out = map[string]any{"cluster": c}
		case "remove":
			err = r.clusters.Remove(id)
			out = map[string]any{"ok": err == nil}
		case "sign_in":
			out, err = r.clusters.SignIn(id)
		case "status":
			out, err = r.clusters.Status(id)
		case "answer":
			out, err = r.clusters.Answer(id, req.Answer, req.Remember)
		case "sign_out":
			r.clusters.SignOut(id)
			out, err = r.clusters.Status(id)
		case "touch":
			r.clusters.Touch(id)
			out, err = r.clusters.Status(id)
		case "partitions":
			var parts []hpc.Partition
			parts, err = r.scheduler(id).Partitions(ctx)
			out = map[string]any{"partitions": parts}
		case "submit":
			var job hpc.Job
			job, err = r.scheduler(id).Submit(ctx, req.Request)
			out = map[string]any{"job": job}
		case "jobs":
			var jobs []hpc.Job
			jobs, err = r.scheduler(id).Jobs(ctx)
			r.hpcMu.Lock()
			for i := range jobs {
				if jobs[i].AllocationID != "" {
					jobs[i].FleetNodeID = proto.DelegatedNodeID(r.fleet, r.node, jobs[i].AllocationID)
					jobs[i].ProxyError = r.proxyErrors[jobs[i].AllocationID]
				}
			}
			r.hpcMu.Unlock()
			out = map[string]any{"jobs": jobs}
		case "cancel":
			err = r.scheduler(id).Cancel(ctx, req.JobID)
			out = map[string]any{"ok": err == nil}
		default:
			r.replyErr(m, "hpc_cluster: unknown method "+req.Method)
			return
		}
		if err != nil {
			r.replyErr(m, "hpc_cluster "+req.Method+": "+err.Error())
			return
		}
		r.reply(m, out)
	}()
}
