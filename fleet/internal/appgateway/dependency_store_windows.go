//go:build windows

package appgateway

import (
	"fmt"
	"os"
)

// Controller persistence requires protected storage. Windows Fleet consumers do
// not host this journal and remain supported; a Windows Controller needs ACLs.
func privateDependencyFile(info os.FileInfo) bool { return false }
func lockDependencyStore(path string) (*os.File, error) {
	return nil, fmt.Errorf("dependency controller persistence requires protected storage")
}
