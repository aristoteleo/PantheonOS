//go:build !windows

package lifecycle

import "os"

func commitLedger(temporary, destination, directory string) error {
	if err := os.Rename(temporary, destination); err != nil {
		return err
	}
	dir, err := os.Open(directory)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}
