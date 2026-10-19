package join

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"
)

// Client owns Controller trust. A product profile's CA is never installed in
// system roots or assigned to the process-wide HTTP transport. The private
// transport accepts only this exact Controller origin and never redirects keys.
type Client struct {
	http   *http.Client
	origin string
	tls    *tls.Config
}

func NewClient(controller, caFile string) (*Client, error) {
	if caFile == "" {
		return defaultClient, nil
	}
	u, err := url.Parse(controller)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || u.ForceQuery {
		return nil, fmt.Errorf("private Controller trust requires an exact HTTPS origin")
	}
	f, err := os.Open(caFile)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 16384 {
		return nil, fmt.Errorf("invalid Controller CA file")
	}
	pem, err := io.ReadAll(io.LimitReader(f, 16385))
	if err != nil || len(pem) > 16384 {
		return nil, fmt.Errorf("invalid Controller CA file")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(pem) {
		return nil, fmt.Errorf("invalid Controller CA certificate")
	}
	config := &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.TLSClientConfig = config
	return &Client{origin: controller, tls: config, http: &http.Client{Transport: transport,
		Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

func (c *Client) do(req *http.Request) (*http.Response, error) {
	if c.origin != "" && (req.URL.Scheme+"://"+req.URL.Host != c.origin || req.URL.User != nil) {
		return nil, fmt.Errorf("private Controller client cannot contact another origin")
	}
	return c.http.Do(req)
}

// TLSConfig also pins the Runner's outbound App tunnel to the same Controller.
// Callers supply that saved origin, never one from an App request.
func (c *Client) TLSConfig() *tls.Config {
	if c.tls == nil {
		return nil
	}
	return c.tls.Clone()
}

func (c *Client) Close() {
	if c.origin != "" {
		c.http.CloseIdleConnections()
	}
}
