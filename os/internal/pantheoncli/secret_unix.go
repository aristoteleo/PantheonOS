//go:build !windows

package pantheoncli

import (
	"bufio"
	"os"
	"os/exec"
	"strings"
)

// readSecret reads a line from the terminal without echoing it.
func readSecret() (string, error) {
	off := exec.Command("stty", "-echo")
	off.Stdin = os.Stdin
	if off.Run() == nil {
		defer func() {
			on := exec.Command("stty", "echo")
			on.Stdin = os.Stdin
			_ = on.Run()
		}()
	}
	line, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if err != nil && line == "" {
		return "", err
	}
	return strings.TrimRight(line, "\r\n"), nil
}
