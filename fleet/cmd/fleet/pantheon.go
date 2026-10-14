package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/aristoteleo/pantheon-fleet/internal/pantheoncli"
)

// One binary, two names (docs/pantheon-cli.md). Called `pantheon`, it is the
// PantheonOS command: the client of the owner's Apps, desktop and nodes, with
// the node itself under `pantheon node`. Called `fleet`, it is the node, as
// installed nodes, the macOS app and auto-update expect; the client commands
// work there too.

// The Pantheon agent's own commands, still served by its Python package
// (installed as `pantheon-agent`) until they move into this binary.
var agentCommands = map[string]bool{
	"cli": true, "ui": true, "setup": true, "store": true, "update-templates": true,
	"sync-templates": true, "local": true, "local-setup": true, "local-update": true, "local-release": true,
}

func invokedAsPantheon() bool {
	name := strings.ToLower(strings.TrimSuffix(filepath.Base(os.Args[0]), ".exe"))
	return name == "pantheon"
}

// routeCommand handles the client and the agent's commands. Everything else
// is a node command for the existing switch (returns false), with or without
// `node` in front: the node starts itself, its helpers and its workers by
// their command names, whichever name the binary has.
func routeCommand() bool {
	args := os.Args[1:]
	if !invokedAsPantheon() {
		if len(args) > 0 && pantheoncli.IsCommand(args[0]) {
			os.Exit(pantheoncli.Main(args, os.Stdin, os.Stdout, os.Stderr))
		}
		return false
	}
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		pantheonUsage()
		if len(args) == 0 {
			os.Exit(2)
		}
		os.Exit(0)
	}
	switch first := args[0]; {
	case first == "node":
		os.Args = append([]string{os.Args[0]}, args[1:]...)
		return false
	case first == "version" || first == "--version" || first == "-v":
		fmt.Println("pantheon", version)
		os.Exit(0)
	case pantheoncli.IsCommand(first):
		os.Exit(pantheoncli.Main(args, os.Stdin, os.Stdout, os.Stderr))
	case agentCommands[first]:
		os.Exit(runAgentCommand(args))
	}
	return false
}

// runAgentCommand forwards an agent command to the Python package's own
// entry point.
func runAgentCommand(args []string) int {
	path, err := exec.LookPath("pantheon-agent")
	if err != nil {
		fmt.Fprintf(os.Stderr, "pantheon: `pantheon %s` needs the Pantheon agent package (pantheon-agent), which is not installed here\n", args[0])
		return 127
	}
	cmd := exec.Command(path, args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exit.ExitCode()
		}
		fmt.Fprintln(os.Stderr, "pantheon:", err)
		return 1
	}
	return 0
}

func pantheonUsage() {
	fmt.Fprintf(os.Stderr, `pantheon %s — the PantheonOS command

%s

The Pantheon agent
  pantheon cli | ui | setup | store ...   (the agent's own commands)

This machine as a node
  pantheon node up | credentials | capture | version   (pantheon node --help)
`, version, pantheoncli.Usage)
}
