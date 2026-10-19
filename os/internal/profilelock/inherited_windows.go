package profilelock

import "fmt"

func protect(fd int) error {
	return fmt.Errorf("inherited local profile locks require POSIX")
}
