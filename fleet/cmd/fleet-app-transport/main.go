// A distributable workload client: no runner, controller, node credentials or
// model management. Reuse Fleet's authenticated QUIC and private pipe protocol.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/aristoteleo/pantheon-fleet/internal/appdirect"
)

func main() {
	if len(os.Args) != 2 || (os.Args[1] != "app-dial" && os.Args[1] != "app-session") {
		fmt.Fprintln(os.Stderr, "Supply app-dial or app-session with the private stdin protocol")
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	run := appdirect.RunBridge
	if os.Args[1] == "app-session" {
		run = appdirect.RunSession
	}
	if err := run(ctx, os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "Direct App transport unavailable")
		os.Exit(1)
	}
}
