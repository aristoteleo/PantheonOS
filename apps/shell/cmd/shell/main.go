package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	shellapp "github.com/aristoteleo/pantheon-apps/shell"
	"github.com/aristoteleo/pantheon-fleet/appsvc"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	if len(os.Args) < 2 {
		return fmt.Errorf("usage: shell start|ready|drain --data PATH")
	}
	flags := flag.NewFlagSet("shell", flag.ContinueOnError)
	data := flags.String("data", "", "instance data directory")
	if err := flags.Parse(os.Args[2:]); err != nil {
		return err
	}
	if *data == "" || !filepath.IsAbs(*data) || flags.NArg() != 0 {
		return fmt.Errorf("an absolute instance data directory is required")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	return appsvc.ManagedCommand(ctx, "shell", os.Args[1], os.Stdout, func() (appsvc.ManagedOptions, error) {
		workspace := filepath.Join(*data, "workspace")
		if err := os.MkdirAll(workspace, 0700); err != nil {
			return appsvc.ManagedOptions{}, err
		}
		app := shellapp.NewApp(workspace)
		tools, err := shellapp.Tools(app)
		if err != nil {
			app.Close()
			return appsvc.ManagedOptions{}, err
		}
		return appsvc.ManagedOptions{Tools: tools, BeforeStop: app.BeforeStop, Close: app.CloseManaged,
			DrainMethods: []string{"get_shell_output", "close_shell", "resource_session_get", "resource_session_release"}}, nil
	})
}
