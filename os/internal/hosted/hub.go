package hosted

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/deployments"
)

// SecretStore is the controller's custody of owners' deployment secrets.
type SecretStore interface {
	Get(fleet, name string) (string, deployments.SecretInfo, error)
}

// StatusError is an HTTP status a hosted service reports to its caller
// (formerly pantheon.models.errors.ControlError).
type StatusError struct {
	Status int
	Detail string
}

func (e *StatusError) Error() string {
	if e.Detail != "" {
		return e.Detail
	}
	return fmt.Sprintf("Model Services control plane returned HTTP %d; refresh or update Hub/Fleet", e.Status)
}

func statusOf(err error) int {
	var s *StatusError
	if errors.As(err, &s) && s.Status >= 400 && s.Status <= 599 {
		return s.Status
	}
	return 503
}

// Hub calls the owner's Hub with a secret the owner placed in the
// controller's custody (an owner platform key bound to the Hub origin).
type Hub struct {
	Secrets SecretStore
	HTTP    *http.Client
}

const maxHubBody = 4 << 20

// Request sends one JSON request; a non-200 reply is a StatusError carrying
// the Hub's string detail when it is short.
func (h *Hub) Request(ctx context.Context, fleet, secret, method, path string, body any, out any) error {
	if h == nil || h.Secrets == nil {
		return &StatusError{Status: 503, Detail: "this controller keeps no owner credentials"}
	}
	key, info, err := h.Secrets.Get(fleet, secret)
	if err != nil {
		return &StatusError{Status: 503, Detail: "the owner's Hub credential is unavailable"}
	}
	origin, err := url.Parse(info.Endpoint)
	if err != nil || origin.Scheme != "https" || origin.Host == "" || origin.User != nil || origin.RawQuery != "" || origin.Fragment != "" {
		return &StatusError{Status: 503, Detail: "the owner's Hub credential has no HTTPS origin"}
	}
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(raw)
	}
	ctx, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(info.Endpoint, "/")+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+key)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	client := h.HTTP
	if client == nil {
		client = &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	resp, err := client.Do(req)
	if err != nil {
		return &StatusError{Status: 503, Detail: "the Hub is unreachable"}
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxHubBody+1))
	if err != nil || len(raw) > maxHubBody {
		return &StatusError{Status: 502}
	}
	if resp.StatusCode != http.StatusOK {
		var detail struct {
			Detail any `json:"detail"`
		}
		_ = json.Unmarshal(raw, &detail)
		e := &StatusError{Status: resp.StatusCode}
		if e.Status < 400 || e.Status > 599 {
			e.Status = 502
		}
		if s, ok := detail.Detail.(string); ok && len(s) <= 500 {
			e.Detail = s
		}
		return e
	}
	if out == nil {
		return nil
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return &StatusError{Status: 502}
	}
	return nil
}
