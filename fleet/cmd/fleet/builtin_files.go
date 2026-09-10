package main

import (
	"context"
	nodefiles "github.com/aristoteleo/pantheon-apps/node_files"
	"github.com/aristoteleo/pantheon-fleet/appsvc"
	"github.com/aristoteleo/pantheon-fleet/internal/apps"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/nats-io/nats.go"
)

func registerNodeFiles(r *runner.Runner, nc *nats.Conn, roots []string, nodeID string) {
	if len(roots) == 0 {
		return
	}
	// Snapshot configuration; neither spec.Dir nor spec.Env can widen it.
	roots = append([]string(nil), roots...)
	r.Apps().RegisterBuiltin("node-files", func(ctx context.Context, spec apps.Spec) (func(), error) {
		app, err := nodefiles.New(roots, nodeID)
		if err != nil {
			return nil, err
		}
		svcNc, owned, err := builtinConn(spec, nc)
		if err != nil {
			app.Close()
			return nil, err
		}
		svc := appsvc.New(svcNc, spec.ServiceID, "file_manager", "Files in locally shared folders.", "node-files/0.1.0", builtinPrefix(spec))
		tools, err := nodefiles.Tools(app)
		if err == nil {
			for _, t := range tools {
				svc.Register(t)
			}
			err = svc.Start(ctx)
		}
		close := func() {
			svc.Stop(context.Background())
			app.Close()
			if owned {
				svcNc.Close()
			}
		}
		if err != nil {
			close()
			return nil, err
		}
		return close, nil
	})
}
