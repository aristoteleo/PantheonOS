//go:build !windows

package lifecycle

import (
	"fmt"
	"os"
	"syscall"
)

func makeConfigDirectory(path string) error {
	err := os.Mkdir(path, 0700)
	if os.IsExist(err) {
		return nil
	}
	return err
}

func checkConfigPrivate(_ string, info os.FileInfo) error {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) || info.Mode().Perm()&0077 != 0 {
		return fmt.Errorf("configuration must be owned by and private to the Runner user")
	}
	return nil
}
