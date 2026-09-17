package nodefiles

import (
	"archive/zip"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"strings"
	"time"
)

var officeUploadPath = regexp.MustCompile(`^/api/office/resource-uploads/[a-f0-9]{32}/[a-f0-9]{32}$`)

// Destinations are configured locally, never learned from document contents or
// an RPC caller. No redirects/proxies: a ticket cannot forward shared files.
func officeUploadOrigins(value string) []string {
	origins := []string{}
	for _, item := range strings.Split(value, ",") {
		origin := strings.TrimRight(strings.TrimSpace(item), "/")
		u, err := url.Parse(origin)
		if err == nil && (u.Scheme == "https" || u.Scheme == "http") && u.Host != "" && u.User == nil && u.Path == "" && u.RawQuery == "" && u.Fragment == "" {
			origins = append(origins, origin)
		}
	}
	return origins
}

func (a *App) uploadZipEntry(p map[string]any) (any, error) {
	u, err := url.Parse(str(p, "url"))
	if err != nil || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" ||
		!officeUploadPath.MatchString(u.Path) || !slices.Contains(a.officeOrigins, u.Scheme+"://"+u.Host) {
		return nil, errors.New("Office upload destination is not configured on this node")
	}
	ticket := str(p, "ticket")
	if len(ticket) < 32 || len(ticket) > 4096 || strings.ContainsAny(ticket, "\r\n") {
		return nil, errors.New("invalid Office upload ticket")
	}
	a.mu.Lock()
	h := a.handles[str(p, "handle_id")]
	if h == nil || h.write || h.snapshot == nil || h.readers >= 4 {
		a.mu.Unlock()
		return nil, errors.New("Office upload requires an available verified snapshot")
	}
	h.readers++
	h.touched = time.Now()
	a.mu.Unlock()
	// Pin the descriptor without holding the Files lock over a network request.
	defer func() {
		a.mu.Lock()
		defer a.mu.Unlock()
		h.readers--
		if h.readers == 0 && h.closing {
			h.file.Close()
		}
	}()
	info, err := h.file.Stat()
	if err != nil || info.Size() != h.snapshot.Size() || !info.ModTime().Equal(h.snapshot.ModTime()) {
		return nil, errors.New("source file changed during snapshot read")
	}
	archive, err := zip.NewReader(h.file, h.snapshot.Size())
	if err != nil {
		return nil, err
	}
	if len(archive.File) > 20000 {
		return nil, errors.New("too many ZIP entries")
	}
	name := str(p, "name")
	var selected *zip.File
	for _, entry := range archive.File {
		if entry.Name == name {
			if selected != nil {
				return nil, errors.New("duplicate ZIP entry")
			}
			selected = entry
		}
	}
	if selected == nil || !strings.HasPrefix(name, "ppt/media/") || len(name) > 1024 || selected.Flags&1 != 0 ||
		selected.UncompressedSize64 > 128*1024*1024 || selected.UncompressedSize64 == 0 {
		return nil, errors.New("invalid Office media entry")
	}
	stream, err := selected.Open()
	if err != nil {
		return nil, err
	}
	defer stream.Close()
	request, err := http.NewRequest(http.MethodPost, u.String(), io.LimitReader(stream, int64(selected.UncompressedSize64)+1))
	if err != nil {
		return nil, errors.New("invalid Office upload request")
	}
	request.Header.Set("Authorization", "Bearer "+ticket)
	request.Header.Set("Content-Type", "application/octet-stream")
	request.ContentLength = int64(selected.UncompressedSize64)
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	defer transport.CloseIdleConnections()
	client := &http.Client{Timeout: 90 * time.Second, Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(request)
	if err != nil {
		return nil, errors.New("Office direct upload failed; retry using the file connection")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Office upload returned status %d", response.StatusCode)
	}
	var result struct {
		Ready bool `json:"ready"`
	}
	if json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&result) != nil || !result.Ready {
		return nil, errors.New("Office did not verify the uploaded media")
	}
	return map[string]any{"success": true, "bytes_uploaded": selected.UncompressedSize64}, nil
}
