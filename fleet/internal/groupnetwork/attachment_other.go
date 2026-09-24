//go:build !linux

package groupnetwork

import "fmt"

func OpenContainerNamespace(pid int) (NamespaceHandle, error) {
	return nil, fmt.Errorf("container collective networks require Linux")
}
