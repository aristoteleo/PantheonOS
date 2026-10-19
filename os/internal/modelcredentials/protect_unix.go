//go:build !windows

package modelcredentials

import (
	"os"
	"syscall"
)

const protection = "posix-private"

func private(info os.FileInfo) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && stat.Uid == uint32(os.Geteuid()) && info.Mode().Perm()&0077 == 0
}
func protect(value []byte) ([]byte, error)   { return append([]byte(nil), value...), nil }
func unprotect(value []byte) ([]byte, error) { return append([]byte(nil), value...), nil }
