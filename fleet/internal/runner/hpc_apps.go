package runner

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/aristoteleo/pantheon-fleet/internal/hpc"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

//go:embed hpc_asset.py
var hpcAssetProgram string

func (r *Runner) stageHPCAsset(ctx context.Context, cluster, root, suffix string, data []byte) (string, string, error) {
	hash := sha256.Sum256(data)
	q := map[string]any{"root": root, "digest": hex.EncodeToString(hash[:]), "suffix": suffix, "write": false}
	for _, write := range []bool{false, true} {
		q["write"] = write
		metadata, _ := json.Marshal(q)
		var input []byte
		if write {
			input = data
		}
		out, err := r.clusters.Run(ctx, cluster, input, "python3", "-c", hpcAssetProgram, string(metadata))
		if err != nil {
			return "", "", err
		}
		var result struct {
			Exists     bool
			Path, Root string
		}
		if err = json.Unmarshal(out, &result); err != nil {
			return "", "", err
		}
		if result.Exists && strings.HasPrefix(result.Path, result.Root+"/") {
			return result.Path, result.Root, nil
		}
	}
	return "", "", fmt.Errorf("asset was not staged")
}

func (r *Runner) prepareHPCApp(ctx context.Context, cluster, allocation string, app hpc.App) (*hpc.HTTPService, error) {
	status, err := r.clusters.Status(cluster)
	if err != nil {
		return nil, err
	}
	profile := status.AppEnvironment
	if profile == nil {
		return nil, fmt.Errorf("configure the cluster's App environment before launching Apps")
	}
	if err = profile.Validate(); err != nil {
		return nil, err
	}
	if r.lifecycle == nil {
		return nil, fmt.Errorf("connector App staging unavailable")
	}
	artifact, err := r.lifecycle.ArtifactBytes(app.Digest)
	if err != nil {
		return nil, err
	}
	executable, err := os.Executable()
	if err != nil {
		return nil, err
	}
	// Workers are versioned with the installed connector. No shell installer,
	// unpinned download or cloud identity is sent to the cluster.
	workerRoot := os.Getenv("PANTHEON_HPC_WORKER_ROOT")
	if workerRoot == "" {
		workerRoot = filepath.Join(filepath.Dir(executable), "hpc")
		if runtime.GOOS == "darwin" && filepath.Base(filepath.Dir(executable)) == "MacOS" {
			workerRoot = filepath.Join(filepath.Dir(executable), "..", "Resources", "hpc")
		}
	}
	worker, err := os.ReadFile(filepath.Join(workerRoot, "fleet-job-linux-"+profile.Architecture))
	if err != nil {
		return nil, fmt.Errorf("this Fleet installation is missing its Linux App worker: %w", err)
	}
	workerPath, root, err := r.stageHPCAsset(ctx, cluster, profile.Root, ".bin", worker)
	if err != nil {
		return nil, err
	}
	artifactPath, _, err := r.stageHPCAsset(ctx, cluster, profile.Root, ".tar", artifact)
	if err != nil {
		return nil, err
	}
	secret := make([]byte, 32)
	if _, err = rand.Read(secret); err != nil {
		return nil, err
	}
	token := hex.EncodeToString(secret)
	tokenDir := filepath.Join(r.clusters.Root, "app-tokens", cluster)
	if err = os.MkdirAll(tokenDir, 0700); err != nil {
		return nil, err
	}
	if err = os.WriteFile(filepath.Join(tokenDir, allocation), []byte(token), 0600); err != nil {
		return nil, err
	}
	config, _ := json.Marshal(map[string]string{"Root": root + "/jobs/" + allocation, "Artifact": artifactPath,
		"Digest": app.Digest, "Scope": app.Scope, "Owner": r.fleet, "Node": proto.DelegatedNodeID(r.fleet, r.node, allocation), "Token": token})
	configPath, _, err := r.stageHPCAsset(ctx, cluster, profile.Root, ".json", config)
	if err != nil {
		return nil, err
	}
	argv := []string{workerPath, configPath}
	if len(profile.Modules) > 0 {
		var modules []string
		for _, module := range profile.Modules {
			modules = append(modules, "'"+module+"'")
		}
		argv = append([]string{"bash", "-lc", "set -e; module purge; module load " + strings.Join(modules, " ") + "; exec \"$@\"", "fleet-app"}, argv...)
	}
	return &hpc.HTTPService{Name: "app-worker", Argv: argv, Cwd: ".", StartupSeconds: 60}, nil
}
