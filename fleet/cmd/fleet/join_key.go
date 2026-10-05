package main

import (
	"errors"
	"io"
	"os"
	"runtime"
	"strings"
)

func readJoinKey(path string) (string, error) {
	invalid := errors.New("Fleet key file must be a private regular file containing one key")
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 8194 {
		return "", invalid
	}
	f, err := os.Open(path)
	if err != nil {
		return "", invalid
	}
	defer f.Close()
	current, err := f.Stat()
	if err != nil || !os.SameFile(info, current) || !current.Mode().IsRegular() ||
		(runtime.GOOS != "windows" && current.Mode().Perm()&0077 != 0) {
		return "", invalid
	}
	data, err := io.ReadAll(io.LimitReader(f, 8195))
	if err != nil || len(data) > 8194 {
		return "", invalid
	}
	key := strings.TrimSpace(string(data))
	if key == "" || strings.ContainsAny(key, "\r\n\t ") {
		return "", invalid
	}
	return key, nil
}
