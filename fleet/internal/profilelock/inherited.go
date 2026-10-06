// Package profilelock scopes an owner's inherited profile lock to this service.
package profilelock

import (
	"fmt"
	"os"
	"strconv"
)

const Environment = "PANTHEON_LOCAL_PROFILE_LOCK_FD"

// Adopt keeps the lock open for this infrastructure process, including after
// its owner dies, but prevents tools, installers and Apps from inheriting it.
// Call before starting any subprocess. Do not wrap the descriptor in os.File:
// its finalizer could close the owner's lock while this service is still live.
func Adopt() error {
	raw, present := os.LookupEnv(Environment)
	if !present {
		return nil
	}
	os.Unsetenv(Environment)
	fd, err := strconv.Atoi(raw)
	if err != nil || fd < 3 {
		return fmt.Errorf("invalid inherited local profile lock descriptor")
	}
	return protect(fd)
}
