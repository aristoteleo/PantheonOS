package appdirect

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
)

// RunBridge gives a local workload QUIC without a Python libp2p dependency or a
// listening localhost HTTP proxy. One process owns one authenticated connection.
// Only protocol records and then raw HTTP bytes are written to output. Secrets
// arrive on stdin, never command arguments, the environment, or diagnostic logs.
func RunBridge(ctx context.Context, input io.ReadCloser, output io.WriteCloser) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	defer input.Close()
	defer output.Close()
	done := make(chan struct{})
	go func() {
		defer close(done)
		<-ctx.Done()
		_ = input.Close()
		_ = output.Close()
	}()
	defer func() { cancel(); <-done }()
	p, err := dataplane.NewAppClient(ctx)
	if err != nil {
		return fmt.Errorf("direct workload peer unavailable")
	}
	defer p.Close()
	bootstrap := time.AfterFunc(20*time.Second, cancel)
	defer bootstrap.Stop()
	if err := json.NewEncoder(output).Encode(map[string]any{"protocol": 1, "peer_id": p.ID()}); err != nil {
		return err
	}
	reader := bufio.NewReaderSize(input, 65536)
	line, err := reader.ReadSlice('\n')
	if err != nil {
		return fmt.Errorf("direct workload grant missing or too large")
	}
	var grant Grant
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&grant) != nil || decoder.Decode(new(any)) != io.EOF {
		_ = json.NewEncoder(output).Encode(map[string]any{"ready": false, "error": "grant_rejected"})
		return fmt.Errorf("invalid direct workload grant")
	}
	conn, err := Dial(ctx, p, grant)
	if err != nil {
		code := "unavailable"
		if errors.Is(err, ErrInvalidGrant) || errors.Is(err, ErrGrantRejected) {
			code = "grant_rejected"
		}
		_ = json.NewEncoder(output).Encode(map[string]any{"ready": false, "error": code})
		return fmt.Errorf("direct workload connection unavailable")
	}
	defer conn.Close()
	bootstrap.Stop()
	if err := json.NewEncoder(output).Encode(map[string]any{"ready": true, "transport": "fleet_direct"}); err != nil {
		return err
	}
	ended := make(chan struct{}, 2)
	go func() { _, _ = io.Copy(conn, reader); ended <- struct{}{} }()
	go func() { _, _ = io.Copy(output, conn); ended <- struct{}{} }()
	<-ended
	_ = conn.Close()
	cancel()
	<-ended
	return nil
}
