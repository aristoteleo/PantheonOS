//go:build windows

package pantheoncli

import (
	"bufio"
	"os"
	"strings"

	"golang.org/x/sys/windows"
)

// readSecret reads a line from the console without echoing it.
func readSecret() (string, error) {
	handle := windows.Handle(os.Stdin.Fd())
	var mode uint32
	if windows.GetConsoleMode(handle, &mode) == nil {
		if windows.SetConsoleMode(handle, mode&^windows.ENABLE_ECHO_INPUT) == nil {
			defer windows.SetConsoleMode(handle, mode)
		}
	}
	line, err := bufio.NewReader(os.Stdin).ReadString('\n')
	if err != nil && line == "" {
		return "", err
	}
	return strings.TrimRight(line, "\r\n"), nil
}
