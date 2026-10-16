// Package broker is the NATS server linked into the pantheon binary, so local
// mode needs no separate broker executable (`pantheon node broker -c FILE`).
// It takes the stock nats-server's arguments and behaves as it does: the
// configuration the controller writes, SIGHUP to reload it, SIGTERM to stop.
package broker

import (
	"flag"
	"fmt"
	"os"

	"github.com/nats-io/nats-server/v2/server"
)

// Main runs the server with nats-server's arguments (without the program
// name) until it shuts down.
func Main(args []string) {
	fs := flag.NewFlagSet("broker", flag.ExitOnError)
	opts, err := server.ConfigureOptions(fs, args, server.PrintServerAndExit, fs.Usage, server.PrintTLSHelpAndDie)
	if err != nil {
		server.PrintAndDie(fmt.Sprintf("broker: %s", err))
	} else if opts.CheckConfig {
		fmt.Fprintf(os.Stderr, "broker: configuration file %s is valid\n", opts.ConfigFile)
		os.Exit(0)
	}
	s, err := server.NewServer(opts)
	if err != nil {
		server.PrintAndDie(fmt.Sprintf("broker: %s", err))
	}
	s.ConfigureLogger()
	if err := server.Run(s); err != nil {
		server.PrintAndDie(err.Error())
	}
	s.WaitForShutdown()
}
