package auth

import (
	"strings"
	"testing"
)

func TestServerConfigAddsWebsocketListenerOnlyWhenAsked(t *testing.T) {
	a, err := Bootstrap(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(a.ServerConfig("0.0.0.0:4222", "/js"), "websocket") {
		t.Fatal("websocket emitted by default")
	}
	a.WebsocketListen = "0.0.0.0:4223"
	cfg := a.ServerConfig("0.0.0.0:4222", "/js")
	if !strings.Contains(cfg, "websocket {\n  listen: \"0.0.0.0:4223\"\n  no_tls: true\n}") || !strings.Contains(cfg, "listen: 0.0.0.0:4222") {
		t.Fatalf("%s", cfg)
	}
}
