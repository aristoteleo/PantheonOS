package hpc

import (
	"context"
	"fmt"
	"regexp"
)

// App is an ordinary immutable Fleet artifact. Scheduler resources remain in
// Request; interpreter/module/storage choices belong to the cluster profile.
type App struct {
	Digest string `json:"digest"`
	Scope  string `json:"scope"`
}

func (a App) Validate() error {
	if !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(a.Digest) || !regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,79}$`).MatchString(a.Scope) {
		return fmt.Errorf("App requires an immutable artifact digest and scope")
	}
	return nil
}

type PrepareApp func(context.Context, string, App) (*HTTPService, error)
