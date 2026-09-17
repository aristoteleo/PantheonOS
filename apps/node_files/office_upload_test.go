package nodefiles

import (
	"archive/zip"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestOfficeDirectUpload(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "deck.pptx")
	payload := strings.Repeat("media", 10000)
	f, _ := os.Create(path)
	z := zip.NewWriter(f)
	w, _ := z.Create("ppt/media/image.png")
	w.Write([]byte(payload))
	z.Close()
	f.Close()
	received := make(chan bool, 1)
	release := make(chan bool, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, err := io.ReadAll(r.Body)
		if err != nil || string(data) != payload || r.Header.Get("Authorization") != "Bearer "+strings.Repeat("t", 64) {
			t.Error("incorrect upload")
		}
		received <- true
		<-release
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"ready":true}`)
	}))
	defer server.Close()
	t.Setenv("PANTHEON_OFFICE_UPLOAD_ORIGINS", server.URL)
	a, err := New([]string{root}, "test")
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	opened := call(t, a, "file_transfer", map[string]any{"method": "open_file_for_read", "args": map[string]any{"file_path": path, "snapshot": true}})
	route := "/api/office/resource-uploads/" + strings.Repeat("a", 32) + "/" + strings.Repeat("b", 32)
	params := map[string]any{"handle_id": opened["handle_id"], "name": "ppt/media/image.png", "url": server.URL + route, "ticket": strings.Repeat("t", 64)}
	for _, bad := range []string{"http://untrusted.test" + route, server.URL + "/other", server.URL + route + "?redirect=1"} {
		params["url"] = bad
		if _, err := a.uploadZipEntry(params); err == nil {
			t.Fatal("accepted destination", bad)
		}
	}
	params["url"] = server.URL + route
	done := make(chan error, 1)
	go func() { _, err := a.uploadZipEntry(params); done <- err }()
	select {
	case <-received:
	case <-time.After(3 * time.Second):
		t.Fatal("upload did not arrive")
	}
	// Close must return while the HTTP request is pending, and pin its descriptor.
	closed := make(chan bool, 1)
	go func() {
		_, err := a.transfer("close_file", map[string]any{"handle_id": opened["handle_id"]})
		closed <- err == nil
	}()
	select {
	case ok := <-closed:
		if !ok {
			t.Fatal("close failed")
		}
	case <-time.After(time.Second):
		t.Fatal("upload blocked Files")
	}
	release <- true
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if _, err := a.uploadZipEntry(params); err == nil {
		t.Fatal("accepted closed snapshot")
	}
}

func TestOfficeUploadNeverRedirects(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "deck.pptx")
	f, _ := os.Create(path)
	z := zip.NewWriter(f)
	w, _ := z.Create("ppt/media/image.png")
	w.Write([]byte("media"))
	z.Close()
	f.Close()
	forbidden := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("followed redirect") }))
	defer forbidden.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, forbidden.URL, http.StatusTemporaryRedirect)
	}))
	defer server.Close()
	t.Setenv("PANTHEON_OFFICE_UPLOAD_ORIGINS", server.URL)
	a, _ := New([]string{root}, "test")
	defer a.Close()
	opened := call(t, a, "file_transfer", map[string]any{"method": "open_file_for_read", "args": map[string]any{"file_path": path, "snapshot": true}})
	_, err := a.uploadZipEntry(map[string]any{"handle_id": opened["handle_id"], "name": "ppt/media/image.png", "url": server.URL + "/api/office/resource-uploads/" + strings.Repeat("a", 32) + "/" + strings.Repeat("b", 32), "ticket": strings.Repeat("t", 64)})
	if err == nil {
		t.Fatal("accepted redirect")
	}
}
