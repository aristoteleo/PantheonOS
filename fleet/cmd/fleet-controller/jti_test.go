package main

import (
	"path/filepath"
	"testing"
	"time"
)

func TestUsedJoinTokensStayUsedAcrossRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "consumed.json")
	exp := time.Now().Add(time.Hour).Unix()
	if !loadJTISet(path).consume("a", exp) {
		t.Fatal("first use refused")
	}
	if loadJTISet(path).consume("a", exp) {
		t.Fatal("a used token was accepted again after a restart")
	}
	if !loadJTISet(path).consume("b", exp) {
		t.Fatal("an unused token was refused")
	}
}
