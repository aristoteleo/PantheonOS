package dataplane

import (
	"context"
	"slices"
	"testing"
)

func TestWorkloadPeerHasNoInboundFileReceiver(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p, err := NewAppClient(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	if protocols := p.host.Mux().Protocols(); slices.Contains(protocols, TransferProto) || slices.Contains(protocols, AppProto) {
		t.Fatal("workload helper exposed node data-plane receivers")
	}
}
