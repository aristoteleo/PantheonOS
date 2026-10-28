// Package pantheoncli is the client half of the `pantheon` command
// (docs/os/pantheon-cli.md): it reaches the owner's Apps, desktop and nodes
// through the Hub and the Fleet controller.
//
// A session holds one credential: the owner's platform key (pbk_...). The Hub
// accepts it for App grants (workload-connect) and the controller for the
// deployment's status. The key never travels anywhere else, and no request
// follows a redirect.
package pantheoncli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// DefaultHub is the production Hub.
const DefaultHub = "https://app.pantheonos.stanford.edu"

// Credentials is what `pantheon login` saves.
type Credentials struct {
	Hub           string `json:"hub"`
	Key           string `json:"key"`
	FleetID       string `json:"fleet_id,omitempty"`
	ControllerURL string `json:"controller_url,omitempty"`
	User          string `json:"user,omitempty"`
}

// ConfigDir is ~/.pantheon/cli, or $PANTHEON_CLI_HOME.
func ConfigDir() (string, error) {
	if dir := os.Getenv("PANTHEON_CLI_HOME"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".pantheon", "cli"), nil
}

func credentialsPath() (string, error) {
	dir, err := ConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "credentials.json"), nil
}

// LoadCredentials reads the saved login; PANTHEON_KEY and PANTHEON_HUB_URL
// take precedence, for scripts and CI.
func LoadCredentials() (*Credentials, error) {
	creds := &Credentials{}
	if path, err := credentialsPath(); err == nil {
		if data, err := os.ReadFile(path); err == nil {
			if err := json.Unmarshal(data, creds); err != nil {
				return nil, fmt.Errorf("%s is not valid; run pantheon login again", path)
			}
		}
	}
	if key := os.Getenv("PANTHEON_KEY"); key != "" {
		creds = &Credentials{Hub: creds.Hub, Key: key}
	}
	if hub := os.Getenv("PANTHEON_HUB_URL"); hub != "" && hub != creds.Hub {
		creds.Hub, creds.FleetID, creds.ControllerURL = hub, "", ""
	}
	if creds.Hub == "" {
		creds.Hub = DefaultHub
	}
	if creds.Key == "" {
		return nil, errors.New("not signed in: run pantheon login")
	}
	return creds, nil
}

// SaveCredentials writes the login, readable by the user only.
func SaveCredentials(creds *Credentials) error {
	path, err := credentialsPath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(creds, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// RemoveCredentials forgets the login.
func RemoveCredentials() error {
	path, err := credentialsPath()
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// CheckOrigin accepts an exact https origin (or http on loopback, for local
// development).
func CheckOrigin(raw string) (string, error) {
	u, err := url.Parse(strings.TrimRight(raw, "/"))
	if err != nil || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		return "", fmt.Errorf("%q is not a Hub origin like https://host", raw)
	}
	loopback := u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost" || u.Hostname() == "::1"
	if u.Scheme != "https" && !(u.Scheme == "http" && loopback) {
		return "", fmt.Errorf("%q must use https", raw)
	}
	return u.Scheme + "://" + u.Host, nil
}

// Session is a signed-in client.
type Session struct {
	Creds *Credentials
	http  *http.Client
}

func NewSession(creds *Credentials) (*Session, error) {
	hub, err := CheckOrigin(creds.Hub)
	if err != nil {
		return nil, err
	}
	creds.Hub = hub
	return &Session{Creds: creds, http: newHTTPClient()}, nil
}

// testTransport replaces the TLS transport in tests.
var testTransport http.RoundTripper

func newHTTPClient() *http.Client {
	if testTransport != nil {
		return &http.Client{Transport: testTransport, CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		}}
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	return &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}}
}

// HTTPError is a non-2xx reply.
type HTTPError struct {
	Status int
	Detail string
}

func (e *HTTPError) Error() string {
	if e.Detail != "" {
		return fmt.Sprintf("%s (HTTP %d)", e.Detail, e.Status)
	}
	return fmt.Sprintf("HTTP %d", e.Status)
}

// request sends JSON (or form) with a bearer credential and decodes the reply.
func (s *Session) request(ctx context.Context, method, target, bearer string, body any, out any, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	var reader io.Reader
	contentType := ""
	switch b := body.(type) {
	case nil:
	case url.Values:
		reader, contentType = strings.NewReader(b.Encode()), "application/x-www-form-urlencoded"
	default:
		data, err := json.Marshal(b)
		if err != nil {
			return err
		}
		reader, contentType = bytes.NewReader(data), "application/json"
	}
	req, err := http.NewRequestWithContext(ctx, method, target, reader)
	if err != nil {
		return err
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	// A coding agent's requests say so (PANTHEON_CLIENT is set where a node
	// starts one), so they can be told from the user's own.
	if client := os.Getenv("PANTHEON_CLIENT"); client != "" {
		req.Header.Set("X-Pantheon-Client", client)
	}
	resp, err := s.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return err
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		detail := ""
		var reply struct {
			Detail any `json:"detail"`
			Error  any `json:"error"`
		}
		if json.Unmarshal(data, &reply) == nil {
			for _, v := range []any{reply.Detail, reply.Error} {
				if v != nil {
					detail = fmt.Sprint(v)
					break
				}
			}
		}
		if resp.StatusCode >= 300 && resp.StatusCode < 400 {
			detail = "the server redirected the request; check the Hub address"
		}
		return &HTTPError{Status: resp.StatusCode, Detail: detail}
	}
	if out == nil {
		return nil
	}
	if err := json.Unmarshal(data, out); err != nil {
		return fmt.Errorf("invalid reply from %s", target)
	}
	return nil
}

// Identity is the owner's Fleet as the Hub sees the key.
type Identity struct {
	FleetID       string `json:"fleet_id"`
	ControllerURL string `json:"controller_url"`
}

// Identify confirms the key and learns the owner's Fleet and controller.
func (s *Session) Identify(ctx context.Context) (*Identity, error) {
	var id Identity
	if err := s.request(ctx, http.MethodGet, s.Creds.Hub+"/api/fleet/apps/workload-identity", s.Creds.Key, nil, &id, 20*time.Second); err != nil {
		var httpErr *HTTPError
		if errors.As(err, &httpErr) && (httpErr.Status == 401 || httpErr.Status == 403) {
			return nil, errors.New("the Hub does not accept this key (revoked or for another Hub): run pantheon login")
		}
		return nil, err
	}
	controller, err := CheckOrigin(id.ControllerURL)
	if err != nil || id.FleetID == "" {
		return nil, errors.New("the Hub returned no Fleet controller for this account")
	}
	id.ControllerURL = controller
	return &id, nil
}

// controller returns the controller origin, asking the Hub once.
func (s *Session) controller(ctx context.Context) (string, error) {
	if s.Creds.ControllerURL != "" {
		return s.Creds.ControllerURL, nil
	}
	id, err := s.Identify(ctx)
	if err != nil {
		return "", err
	}
	s.Creds.FleetID, s.Creds.ControllerURL = id.FleetID, id.ControllerURL
	return id.ControllerURL, nil
}
