// Browser interoperability fixture. Only loopback HTTP is exposed; all inputs,
// credentials and media belong to this disposable test, never a Fleet user.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync/atomic"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appmedia"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

func main() {
	if len(os.Args) != 4 {
		log.Fatal("usage: fixture MEDIA CLIENT_JS HTML")
	}
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		log.Fatal(err)
	}
	script, err := os.ReadFile(os.Args[2])
	if err != nil {
		log.Fatal(err)
	}
	html, err := os.ReadFile(os.Args[3])
	if err != nil {
		log.Fatal(err)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		log.Fatal(err)
	}
	origin := "http://" + listener.Addr().String()
	binding := apptransport.Binding{Fleet: "fixture", Node: "mac", Instance: "video", Revision: strings.Repeat("a", 64), Generation: 1, Component: "backend", Port: "http"}
	const credential = "test-fixture-only-not-a-user-credential"
	artifact := strings.Repeat("b", 32)
	config := strings.Repeat("c", 64)
	digest := sha256.Sum256(data)
	checksum := hex.EncodeToString(digest[:])
	var uses atomic.Int64
	media := appmedia.New(ctx, func(b apptransport.Binding) (string, func(), error) {
		if b != binding {
			return "", nil, fmt.Errorf("fixture binding mismatch")
		}
		uses.Add(1)
		return origin, func() { uses.Add(-1) }, nil
	}, func() bool { return ctx.Err() == nil })
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html")
		w.Write(html)
	})
	mux.HandleFunc("/client.js", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/javascript")
		w.Write(script)
	})
	mux.HandleFunc("/fixture.json", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"revision": config, "asset": map[string]any{"ref": "fleet-artifact://video/" + artifact, "id": artifact, "kind": "video", "mime": "video/mp4", "purpose": "output", "state": "ready", "size": len(data), "received": len(data), "sha256": checksum}})
	})
	mux.HandleFunc("/status", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"active_http_leases": uses.Load()})
	})
	mux.HandleFunc("/__fleet/model-media", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.Header.Get("Origin") != origin {
			http.Error(w, "fixture origin required", 403)
			return
		}
		var offer appmedia.Offer
		dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 70000))
		dec.DisallowUnknownFields()
		if dec.Decode(&offer) != nil || dec.Decode(new(any)) != io.EOF || offer.Artifact != artifact || offer.Config != config || offer.SHA256 != checksum || offer.Size != int64(len(data)) {
			http.Error(w, "wrong fixture", 400)
			return
		}
		answer, e := media.Offer(r.Context(), appmedia.Request{Binding: binding, Offer: offer, Credential: credential, Expires: time.Now().Add(time.Minute).Unix()})
		if e != nil {
			http.Error(w, e.Error(), 503)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(answer)
	})
	mux.HandleFunc("/media/artifacts/"+artifact+"/content", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Pantheon-App-Token") != credential || r.Header.Get("X-Model-Config") != config {
			http.Error(w, "fixture credential required", 403)
			return
		}
		w.Header().Set("ETag", `"`+checksum+`"`)
		w.Header().Set("Content-Type", "video/mp4")
		http.ServeContent(w, r, "fixture.mp4", time.Time{}, bytes.NewReader(data))
	})
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() { <-ctx.Done(); server.Close() }()
	fmt.Println(origin)
	if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
	shutdown, done := context.WithTimeout(context.Background(), 20*time.Second)
	defer done()
	began := time.Now()
	if err := media.Shutdown(shutdown); err != nil {
		log.Fatal(err)
	}
	fmt.Printf("owned media sockets closed in %s\n", time.Since(began))

}
