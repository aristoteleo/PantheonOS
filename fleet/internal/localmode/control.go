package localmode

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/shirou/gopsutil/v4/process"
)

// deploymentName is the local General Team (pantheon/local/deployment.py).
const deploymentName = "general-team"

func profileOf(args []string, out io.Writer, name string) (string, bool, error) {
	_, launch, asJSON, err := flags(name, args, out)
	if err != nil {
		return "", false, err
	}
	var path string
	if *launch != "" {
		path, err = filepath.Abs(*launch)
	} else {
		path, err = launchPath(nil)
	}
	if err != nil {
		return "", false, err
	}
	l, err := readLaunch(path)
	if errors.Is(err, os.ErrNotExist) {
		return "", false, fmt.Errorf("no local setup yet (%s); see `pantheon up`", path)
	}
	return l.Profile, *asJSON, err
}

// receipt is the owner's process receipt (pantheon/local/recovery.py).
type receipt struct {
	Phase string `json:"phase"`
	Owner struct {
		PID     int     `json:"pid"`
		Created float64 `json:"created"`
	} `json:"owner"`
}

// liveOwner is the profile's running owner, matched by its pid and start
// time so a reused pid is never signalled.
func liveOwner(profile string) (*process.Process, error) {
	var r receipt
	if err := readJSON(filepath.Join(profile, "processes.json"), &r); err != nil || r.Phase == "closed" || r.Owner.PID <= 1 {
		return nil, nil
	}
	p, err := process.NewProcess(int32(r.Owner.PID))
	if err != nil {
		return nil, nil
	}
	created, err := p.CreateTime()
	if err != nil || math.Abs(float64(created)/1000-r.Owner.Created) > 1 {
		return nil, nil
	}
	if uids, err := p.Uids(); err != nil || len(uids) == 0 || int(uids[0]) != os.Getuid() {
		return nil, errors.New("the local owner belongs to another user")
	}
	return p, nil
}

// down asks the running `pantheon up` to stop its Apps, and waits for it.
func down(ctx context.Context, args []string, std IO) error {
	profile, _, err := profileOf(args, std.Stderr, "down")
	if err != nil {
		return err
	}
	owner, err := liveOwner(profile)
	if err != nil {
		return err
	}
	if owner == nil {
		fmt.Fprintln(std.Stdout, "Local mode is not running.")
		return nil
	}
	if err := owner.SendSignal(syscall.SIGTERM); err != nil {
		return err
	}
	fmt.Fprintln(std.Stdout, "Stopping the local Apps…")
	deadline := time.Now().Add(5 * time.Minute)
	for time.Now().Before(deadline) {
		if running, err := owner.IsRunningWithContext(ctx); err != nil || !running {
			fmt.Fprintln(std.Stdout, "Stopped.")
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
	return errors.New("the local Apps are still stopping; see `pantheon status`")
}

// Summary is the local deployment's state, as `pantheon local` reports it.
type Summary struct {
	State     string         `json:"state"` // starting | ready | stopping | stopped
	Profile   string         `json:"profile"`
	Apps      map[string]App `json:"apps"`
	Attention []string       `json:"attention,omitempty"`
}

// App is one App's state in a Summary.
type App struct {
	State  string `json:"state"`
	Reason string `json:"reason,omitempty"`
}

func status(ctx context.Context, args []string, std IO) error {
	profile, asJSON, err := profileOf(args, std.Stderr, "status")
	if err != nil {
		return err
	}
	s, err := readStatus(ctx, profile)
	if err != nil {
		return err
	}
	if asJSON {
		return json.NewEncoder(std.Stdout).Encode(s)
	}
	fmt.Fprintf(std.Stdout, "Local mode: %s (%s)\n", s.State, s.Profile)
	names := make([]string, 0, len(s.Apps))
	for name := range s.Apps {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		app := s.Apps[name]
		line := fmt.Sprintf("  %-18s %s", name, app.State)
		if app.Reason != "" {
			line += "  " + app.Reason
		}
		fmt.Fprintln(std.Stdout, line)
	}
	return nil
}

// readStatus asks the profile's controller for the deployment, as its owner.
func readStatus(ctx context.Context, profile string) (Summary, error) {
	stopped := Summary{State: "stopped", Profile: profile, Apps: map[string]App{}}
	if owner, err := liveOwner(profile); err != nil || owner == nil {
		return stopped, err
	}
	var ports struct {
		Controller int `json:"controller"`
	}
	if err := readJSON(filepath.Join(profile, "ports.json"), &ports); err != nil || ports.Controller <= 0 {
		return stopped, nil
	}
	key, err := os.ReadFile(filepath.Join(profile, "owner.key"))
	if err != nil {
		return stopped, err
	}
	ca, err := os.ReadFile(filepath.Join(profile, "tls-ca.pem"))
	if err != nil {
		return stopped, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return stopped, errors.New("the profile's certificate authority is unreadable")
	}
	client := &http.Client{Timeout: 5 * time.Second, Transport: &http.Transport{
		Proxy: nil, TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}}}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		fmt.Sprintf("https://127.0.0.1:%d/deployments/%s", ports.Controller, deploymentName), nil)
	if err != nil {
		return stopped, err
	}
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(key)))
	resp, err := client.Do(req)
	if err != nil {
		// The owner runs but its controller is not up yet (or already down).
		return Summary{State: "starting", Profile: profile, Apps: map[string]App{}}, nil
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return Summary{State: "starting", Profile: profile, Apps: map[string]App{}}, nil
	}
	if resp.StatusCode != http.StatusOK {
		return stopped, fmt.Errorf("the local controller answered %s", resp.Status)
	}
	var d deployment
	if err := json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&d); err != nil {
		return stopped, err
	}
	return summarize(d, profile), nil
}

type deployment struct {
	Spec struct {
		Apps map[string]struct {
			Intent string `json:"intent"`
		} `json:"apps"`
	} `json:"spec"`
	Status struct {
		Apps map[string]App `json:"apps"`
	} `json:"status"`
}

// summarize mirrors LocalDeployment.summary (pantheon/local/deployment.py).
func summarize(d deployment, profile string) Summary {
	s := Summary{Profile: profile, Apps: map[string]App{}}
	stopping := false
	for name, app := range d.Spec.Apps {
		if app.Intent == "stopped" {
			stopping = true
		}
		current := d.Status.Apps[name]
		if current.State == "" {
			current.State = "waiting"
		}
		s.Apps[name] = current
		switch current.State {
		case "attention", "failed", "backoff":
			s.Attention = append(s.Attention, name)
		}
	}
	sort.Strings(s.Attention)
	all := func(state string) bool {
		if len(s.Apps) == 0 {
			return false
		}
		for _, app := range s.Apps {
			if app.State != state {
				return false
			}
		}
		return true
	}
	switch {
	case stopping && all("stopped"):
		s.State = "stopped"
	case stopping:
		s.State = "stopping"
	case all("ready"):
		s.State = "ready"
	default:
		s.State = "starting"
	}
	return s
}
