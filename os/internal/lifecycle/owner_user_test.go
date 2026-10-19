package lifecycle

import (
	"reflect"
	"testing"
)

func TestContainerOwnerMappingIsExplicitAndNodeLocal(t *testing.T) {
	for _, id := range []int{0, 1000, 2001} {
		args, err := containerOwnerArgs(true, "linux", id, 42)
		if err != nil || len(args) != 2 || args[0] != "--user" {
			t.Fatal(args, err)
		}
	}
	args, err := containerOwnerArgs(true, "linux", 1000, 1001)
	if err != nil || !reflect.DeepEqual(args, []string{"--user", "1000:1001"}) {
		t.Fatal(args, err)
	}
	for _, platform := range []string{"darwin", "windows"} {
		if _, err := containerOwnerArgs(true, platform, 1000, 1000); err == nil {
			t.Fatal("unsupported owner mapping accepted", platform)
		}
		if args, err := containerOwnerArgs(false, platform, -1, -1); err != nil || len(args) != 0 {
			t.Fatal("changed existing image-user behavior", args, err)
		}
	}
	if _, err := containerOwnerArgs(true, "linux", -1, 1000); err == nil {
		t.Fatal("missing OS identity accepted")
	}
}
