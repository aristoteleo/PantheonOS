package groupnetwork

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/groupcredentials"
)

type boundedOutput struct{ bytes.Buffer }

func (b *boundedOutput) Write(p []byte) (int, error) {
	if b.Len()+len(p) > 1<<20 {
		return 0, io.ErrShortBuffer
	}
	return b.Buffer.Write(p)
}

func inspectCommand(ctx context.Context, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, args[0], args[1:]...)
	var output boundedOutput
	cmd.Stdout = &output
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("network inspection %s failed: %w", args[0], err)
	}
	return output.Bytes(), nil
}

// Observe reads public interface metadata only. Never call wg dump/showconf,
// which can expose private key material. Failed queries never mean absence.
func (Commands) Observe(ctx context.Context, n groupcredentials.OverlayNetwork) (Observation, error) {
	var out Observation
	if runtime.GOOS != "linux" {
		return out, fmt.Errorf("collective namespaces require Linux")
	}
	if err := n.Validate(); err != nil {
		return out, err
	}
	readLinks := func(args ...string) ([]Link, error) {
		data, err := inspectCommand(ctx, args...)
		if err != nil {
			return nil, err
		}
		var links []Link
		if json.Unmarshal(data, &links) != nil || len(links) == 0 {
			return nil, fmt.Errorf("invalid network interface observation")
		}
		return links, nil
	}
	links, err := readLinks("ip", "-d", "-j", "address", "show")
	if err != nil {
		return out, err
	}
	for _, link := range links {
		if link.Name == n.HostInterface {
			l := link
			out.Host = &l
		}
	}
	path := filepath.Join("/var/run/netns", n.Namespace)
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return out, nil
	}
	if err != nil {
		return out, err
	}
	if !info.Mode().IsRegular() {
		return out, fmt.Errorf("namespace anchor is not a regular mount point")
	}
	kind, err := inspectCommand(ctx, "stat", "-f", "-c", "%T", path)
	if err != nil {
		return out, err
	}
	if strings.TrimSpace(string(kind)) != "nsfs" {
		return out, fmt.Errorf("namespace anchor is not nsfs")
	}
	identity, err := inspectCommand(ctx, "stat", "-L", "-c", "%d:%i", path)
	if err != nil {
		return out, err
	}
	out.Identity = strings.TrimSpace(string(identity))
	parts := strings.Split(out.Identity, ":")
	if len(parts) != 2 {
		return out, fmt.Errorf("invalid namespace inode observation")
	}
	for _, part := range parts {
		v, e := strconv.ParseUint(part, 10, 64)
		if e != nil || v == 0 || strconv.FormatUint(v, 10) != part {
			return out, fmt.Errorf("invalid namespace inode observation")
		}
	}
	out.Links, err = readLinks("ip", "-n", n.Namespace, "-d", "-j", "address", "show")
	if err != nil {
		return out, err
	}
	pids, err := inspectCommand(ctx, "ip", "netns", "pids", n.Namespace)
	if err != nil {
		return out, err
	}
	for _, pid := range strings.Fields(string(pids)) {
		value, e := strconv.Atoi(pid)
		if e != nil || value <= 0 {
			return out, fmt.Errorf("invalid namespace process observation")
		}
		out.PIDs = append(out.PIDs, value)
	}
	return out, nil
}
