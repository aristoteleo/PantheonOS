package hpcconn

import (
	"fmt"
	"regexp"
	"strings"
)

// AppEnvironment is configured once per cluster, independent of App identity.
type AppEnvironment struct {
	Root         string   `json:"root"`
	Architecture string   `json:"architecture"`
	Modules      []string `json:"modules,omitempty"`
}

func (p AppEnvironment) Validate() error {
	if len(p.Root) == 0 || len(p.Root) > 1024 || strings.ContainsAny(p.Root, "\x00\r\n") {
		return fmt.Errorf("choose a private shared App directory for this cluster")
	}
	if p.Architecture != "amd64" && p.Architecture != "arm64" {
		return fmt.Errorf("choose the compute architecture: amd64 or arm64")
	}
	if len(p.Modules) > 16 {
		return fmt.Errorf("at most 16 environment modules")
	}
	for _, m := range p.Modules {
		if !regexp.MustCompile(`^[A-Za-z0-9_./+-]{1,120}$`).MatchString(m) {
			return fmt.Errorf("invalid environment module")
		}
	}
	return nil
}
