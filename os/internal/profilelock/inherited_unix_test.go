//go:build !windows

package profilelock

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

func TestInheritedLockHelper(t *testing.T) {
	mode := os.Getenv("PROFILE_LOCK_TEST_MODE")
	if mode == "" {
		return
	}
	path := os.Getenv("PROFILE_LOCK_TEST_PATH")
	if mode == "inspect" {
		var inherited, expected unix.Stat_t
		err := unix.Fstat(3, &inherited)
		if e := unix.Stat(path, &expected); e != nil {
			panic(e)
		}
		fmt.Printf("inherited=%t env=%t", err == nil && inherited.Ino == expected.Ino && inherited.Dev == expected.Dev,
			os.Getenv(Environment) != "")
		os.Exit(0)
	}
	if mode == "protected" {
		if err := Adopt(); err != nil {
			panic(err)
		}
	}
	// Adoption must keep this service's lock, not unlock or close it.
	other, err := os.OpenFile(path, os.O_RDWR, 0600)
	if err != nil {
		panic(err)
	}
	if err := unix.Flock(int(other.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != unix.EWOULDBLOCK {
		panic(fmt.Sprintf("infrastructure lost its lock: %v", err))
	}
	other.Close()
	child := exec.Command(os.Args[0], "-test.run=^TestInheritedLockHelper$")
	child.Env = append(os.Environ(), "PROFILE_LOCK_TEST_MODE=inspect")
	out, err := child.CombinedOutput()
	if err != nil {
		panic(string(out))
	}
	fmt.Print(string(out))
	os.Exit(0)
}

func TestOnlyInfrastructureInheritsOwnerLock(t *testing.T) {
	for _, mode := range []string{"unprotected", "protected"} {
		t.Run(mode, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "profile.lock")
			lock, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
			if err != nil {
				t.Fatal(err)
			}
			defer lock.Close()
			if err := unix.Flock(int(lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
				t.Fatal(err)
			}
			service := exec.Command(os.Args[0], "-test.run=^TestInheritedLockHelper$")
			service.ExtraFiles = []*os.File{lock}
			service.Env = append(os.Environ(), Environment+"=3", "PROFILE_LOCK_TEST_MODE="+mode,
				"PROFILE_LOCK_TEST_PATH="+path)
			out, err := service.CombinedOutput()
			if err != nil {
				t.Fatalf("%v: %s", err, out)
			}
			want := "inherited=false env=false"
			if mode == "unprotected" {
				want = "inherited=true env=true"
			}
			if string(out) != want {
				t.Fatalf("got %q, want %q", out, want)
			}
		})
	}
}

func TestAdoptRejectsInvalidDescriptors(t *testing.T) {
	for _, raw := range []string{"", "not-a-number", "-1", "0", "2", "99999999"} {
		t.Run(raw, func(t *testing.T) {
			t.Setenv(Environment, raw)
			if err := Adopt(); err == nil {
				t.Fatal("invalid descriptor admitted")
			}
			if _, ok := os.LookupEnv(Environment); ok {
				t.Fatal("descriptor environment retained")
			}
		})
	}
	read, write, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer read.Close()
	defer write.Close()
	t.Setenv(Environment, fmt.Sprint(read.Fd()))
	if err := Adopt(); err == nil || !strings.Contains(err.Error(), "regular file") {
		t.Fatal(err)
	}
}
