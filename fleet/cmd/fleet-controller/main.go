// Command fleet-controller is the Fleet's gatekeeper (internal/controller).
package main

import (
	"os"

	"github.com/aristoteleo/pantheon-fleet/internal/controller"
)

func main() { controller.Main(os.Args[1:]) }
