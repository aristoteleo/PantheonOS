package appsvc

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strconv"
	"time"
)

// ManagedFactory is evaluated only on start, never for readiness or stop probes.
type ManagedFactory func() (ManagedOptions, error)

func managedEnvironment(appID string) (ManagedIdentity, string, string, error) {
	generation, err := strconv.ParseUint(os.Getenv("PANTHEON_INSTANCE_GENERATION"), 10, 64)
	identity := ManagedIdentity{AppID: appID, InstanceID: os.Getenv("PANTHEON_INSTANCE_ID"),
		Revision: os.Getenv("PANTHEON_APP_REVISION"), Generation: generation}
	port, portErr := strconv.Atoi(os.Getenv("PANTHEON_PORT_HTTP"))
	token := os.Getenv("PANTHEON_APP_RPC_TOKEN")
	if err != nil || generation == 0 || portErr != nil || port < 1 || port > 65535 ||
		identity.InstanceID == "" || !sessionLeaseID.MatchString(identity.Revision) || len(token) < 32 {
		return identity, "", "", fmt.Errorf("missing managed App environment")
	}
	return identity, token, net.JoinHostPort("127.0.0.1", strconv.Itoa(port)), nil
}

// ManagedCommand supplies start/ready/drain for ordinary fleet.json commands.
// Readiness and component hooks receive the same Runner-owned identity and
// port as start. No mutable endpoint file or application-supplied URL is used.
func ManagedCommand(ctx context.Context, appID, command string, output io.Writer, factory ManagedFactory) error {
	if command != "start" && command != "ready" && command != "drain" {
		return fmt.Errorf("expected start, ready or drain")
	}
	identity, token, address, err := managedEnvironment(appID)
	if err != nil {
		return err
	}
	if command != "start" {
		return managedProbe(ctx, command, identity, token, address, output)
	}
	// Claim the allocated port before initializing resources. A stale or
	// duplicate start cannot create resources while another host owns the port.
	listener, err := net.Listen("tcp4", address)
	if err != nil {
		return fmt.Errorf("managed App listener unavailable: %w", err)
	}
	defer listener.Close()
	opts, err := factory()
	if err != nil {
		return err
	}
	opts.Identity, opts.Token = identity, token
	host, err := NewManagedHost(opts)
	if err != nil {
		if opts.Close != nil {
			_ = opts.Close()
		}
		return err
	}
	server := &http.Server{Handler: host, ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 * 1024}
	finished := make(chan error, 1)
	go func() { finished <- server.Serve(listener) }()
	select {
	case <-ctx.Done():
	case err = <-finished:
		if err != http.ErrServerClosed {
			return err
		}
	}
	// Normal Fleet stop calls drain before signalling. An external signal still
	// gets a bounded attempt, but cannot turn failed cleanup into success.
	deadline := time.Now().Add(25 * time.Second)
	for {
		receipt := host.Drain()
		if receipt.Status == "succeeded" && receipt.SafeToStop {
			break
		}
		if receipt.Status == "failed" || time.Now().After(deadline) {
			_ = server.Close()
			return fmt.Errorf("managed App shutdown did not drain resources")
		}
		time.Sleep(25 * time.Millisecond)
	}
	shutdown, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	return server.Shutdown(shutdown)
}

func managedProbe(ctx context.Context, command string, identity ManagedIdentity, token, address string, output io.Writer) error {
	transport := &http.Transport{Proxy: nil}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 2 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	request := func(method, path string, result any) error {
		req, err := http.NewRequestWithContext(ctx, method, "http://"+address+path, nil)
		if err != nil {
			return err
		}
		req.Header.Set("X-Fleet-RPC-Token", token)
		res, err := client.Do(req)
		if err != nil {
			return fmt.Errorf("managed App probe unavailable")
		}
		defer res.Body.Close()
		body, err := io.ReadAll(io.LimitReader(res.Body, 8193))
		if err != nil || len(body) > 8192 || res.StatusCode != http.StatusOK || json.Unmarshal(body, result) != nil {
			return fmt.Errorf("managed App probe rejected")
		}
		return nil
	}
	var health struct {
		ManagedIdentity
		Ready bool `json:"ready"`
	}
	if err := request("GET", "/health", &health); err != nil {
		return err
	}
	if health.ManagedIdentity != identity {
		return fmt.Errorf("managed App probe identity mismatch")
	}
	if command == "ready" {
		if !health.Ready {
			return fmt.Errorf("managed App is not ready")
		}
		return nil
	}
	deadline := time.Now().Add(8 * time.Second)
	for {
		var receipt DrainReceipt
		if err := request("POST", "/_fleet/drain", &receipt); err != nil {
			return err
		}
		if receipt.Status != "waiting" || time.Now().After(deadline) {
			return json.NewEncoder(output).Encode(receipt)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(25 * time.Millisecond):
		}
	}
}
