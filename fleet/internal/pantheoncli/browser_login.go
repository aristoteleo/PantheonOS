package pantheoncli

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

// The page the browser lands on: it takes the key from the URL fragment,
// which no request carries, and posts it to this command on the same origin.
const callbackPage = `<!doctype html><html><head><meta charset="utf-8"><title>pantheon</title>
<style>body{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:90vh;color:#444}</style>
</head><body><p id="m">Finishing sign-in…</p><script>
const body = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', '/callback');
fetch('/done', {method: 'POST', body}).then(r => r.text()).then(t => { document.getElementById('m').textContent = t; })
  .catch(() => { document.getElementById('m').textContent = 'Could not reach the pantheon command. Run pantheon login again.'; });
</script></body></html>`

type browserResult struct {
	key, user string
	err       error
}

// browserLogin opens the Hub's sign-in page for this command and waits for
// the key it hands back to a loopback address.
func (r *runner) browserLogin(s *Session) (string, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return "", err
	}
	defer listener.Close()
	port := listener.Addr().(*net.TCPAddr).Port
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	state := hex.EncodeToString(raw)
	results := make(chan browserResult, 1)
	finish := func(res browserResult) {
		select {
		case results <- res:
		default:
		}
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /callback", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Referrer-Policy", "no-referrer")
		fmt.Fprint(w, callbackPage)
	})
	mux.HandleFunc("POST /done", func(w http.ResponseWriter, req *http.Request) {
		// Only the callback page above, on this origin, may post.
		if req.Header.Get("Origin") != fmt.Sprintf("http://127.0.0.1:%d", port) {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		if err := req.ParseForm(); err != nil {
			http.Error(w, "invalid", http.StatusBadRequest)
			return
		}
		if subtle.ConstantTimeCompare([]byte(req.PostForm.Get("state")), []byte(state)) != 1 {
			http.Error(w, "This sign-in does not belong to the running pantheon command.", http.StatusForbidden)
			return
		}
		if req.PostForm.Get("error") != "" {
			fmt.Fprint(w, "Sign-in cancelled. You can close this tab.")
			finish(browserResult{err: errors.New("sign-in cancelled in the browser")})
			return
		}
		key := req.PostForm.Get("key")
		if !strings.HasPrefix(key, "pbk_") {
			http.Error(w, "The Hub did not return a key.", http.StatusBadRequest)
			return
		}
		fmt.Fprint(w, "Signed in. You can close this tab and return to the terminal.")
		finish(browserResult{key: key, user: req.PostForm.Get("user")})
	})
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() { _ = server.Serve(listener) }()
	defer server.Shutdown(context.Background())

	host, _ := os.Hostname()
	page := fmt.Sprintf("%s/#/cli-login?%s", s.Creds.Hub, url.Values{
		"port": {fmt.Sprint(port)}, "state": {state}, "host": {host}}.Encode())
	fmt.Fprintf(r.stderr, "Opening your browser to sign in to %s.\nIf it does not open, visit:\n  %s\n", s.Creds.Hub, page)
	fmt.Fprintln(r.stderr, "(Without a browser on this machine, use pantheon login --key or --username.)")
	openBrowser(page)
	select {
	case res := <-results:
		if res.err != nil {
			return "", res.err
		}
		s.Creds.User = res.user
		return res.key, nil
	case <-time.After(10 * time.Minute):
		return "", errors.New("sign-in timed out; run pantheon login again")
	}
}

// openBrowser shows a page in the user's browser (replaced in tests).
var openBrowser = func(page string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", page)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", page)
	default:
		cmd = exec.Command("xdg-open", page)
	}
	_ = cmd.Start()
}
