//go:build windows

package deployments

import (
	"fmt"
	"os"
)

// The deployment store needs protected storage. A Windows Controller needs
// ACL support first; Windows Fleet nodes do not host this store.
func private(info os.FileInfo) bool { return false }

func lockStore(path string) (*os.File, error) {
	return nil, fmt.Errorf("deployment store requires protected storage")
}
