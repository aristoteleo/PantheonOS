// Command fleet-controller is the Fleet's gatekeeper (internal/controller).
package main

import (
	"os"

	"github.com/aristoteleo/pantheonos/os/internal/controller"
)

func main() { controller.Main(os.Args[1:]) }
