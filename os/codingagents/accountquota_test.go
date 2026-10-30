package codingagents

import (
	"context"
	"encoding/base64"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestCursorQuota(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", "")
	cursorKeychainFn = func(context.Context) (string, error) { return "", errors.New("none") }
	t.Cleanup(func() { cursorKeychainFn = cursorKeychain })
	payload := base64.RawURLEncoding.EncodeToString([]byte(`{"sub":"auth0|user_1"}`))
	token := "h." + payload + ".s"
	os.MkdirAll(filepath.Join(home, ".cursor"), 0o700)
	os.WriteFile(filepath.Join(home, ".cursor", "auth.json"), []byte(`{"accessToken":"`+token+`"}`), 0o600)
	period := true
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.Header.Get("Cookie"), "auth0%7Cuser_1%3A%3A"+token) || r.Header.Get("Origin") != "https://cursor.com" {
			w.WriteHeader(403)
			return
		}
		switch r.URL.Path {
		case "/summary":
			io.WriteString(w, `{"billingCycleEnd":"2026-11-01T00:00:00.000Z","membershipType":"premium","isUnlimited":false,
				"individualUsage":{"plan":{"enabled":true,"used":1200,"limit":4800,"totalPercentUsed":0}}}`)
		case "/period":
			if !period || r.Method != http.MethodPost {
				w.WriteHeader(404)
				return
			}
			io.WriteString(w, `{"billingCycleStart":"1793037531180","billingCycleEnd":"1793642331180","enabled":true,
				"planUsage":{"autoPercentUsed":28.0173,"apiPercentUsed":0,"totalPercentUsed":28.0173}}`)
		}
	}))
	defer srv.Close()
	cursorUsageURL, cursorPeriodURL = srv.URL+"/summary", srv.URL+"/period"
	q, err := CursorQuota(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	// The current period's figures win over the summary's, which lag.
	if m := q.Limits["monthly"]; q.Plan != "premium" || m.Used < 0.28 || m.Used > 0.281 || m.ResetsAt != 1793642331 {
		t.Errorf("%+v", q)
	}
	period = false
	q, _ = CursorQuota(context.Background())
	if q.Limits["monthly"].Used != 0.25 || q.Limits["monthly"].ResetsAt != time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC).Unix() {
		t.Errorf("without the current period, the summary: %+v", q)
	}
}

func TestGeminiQuota(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	os.MkdirAll(filepath.Join(home, ".gemini"), 0o700)
	assist := `{"cloudaicompanionProject":{"id":"proj-1"},"currentTier":{"id":"standard-tier","name":"Gemini Code Assist"}}`
	write := func(expiry time.Time) {
		os.WriteFile(filepath.Join(home, ".gemini", "oauth_creds.json"),
			[]byte(`{"access_token":"tok","refresh_token":"r","expiry_date":`+itoa(expiry.UnixMilli())+`}`), 0o600)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer tok" {
			w.WriteHeader(401)
			return
		}
		body, _ := io.ReadAll(r.Body)
		switch r.URL.Path {
		case "/assist":
			io.WriteString(w, assist)
		case "/quota":
			if !strings.Contains(string(body), `"proj-1"`) {
				w.WriteHeader(403)
				return
			}
			io.WriteString(w, `{"buckets":[{"remainingFraction":0.9,"resetTime":"2026-10-30T00:00:00Z","modelId":"flash"},
				{"remainingFraction":0.4,"resetTime":"2026-10-29T08:00:00Z","modelId":"pro"}]}`)
		}
	}))
	defer srv.Close()
	geminiAssistURL, geminiQuotaURL = srv.URL+"/assist", srv.URL+"/quota"

	write(time.Now().Add(time.Hour))
	q, err := GeminiQuota(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if d := q.Limits["daily"]; q.Plan != "Gemini Code Assist" || d.Used < 0.59 || d.Used > 0.61 ||
		d.ResetsAt != time.Date(2026, 10, 29, 8, 0, 0, 0, time.UTC).Unix() {
		t.Errorf("the model closest to its limit: %+v", q)
	}
	// An account Gemini CLI no longer serves: the answer it fails on.
	assist = `{"allowedTiers":[],"ineligibleTiers":[{"reasonCode":"UNSUPPORTED_CLIENT","reasonMessage":"This client is no longer ` +
		`supported. To continue, please migrate: https://antigravity.example","tierId":"free-tier"}]}`
	q, err = GeminiQuota(context.Background())
	if err == nil || q.Refused != "This client is no longer supported. To continue, please migrate: https://antigravity.example" {
		t.Errorf("refused: %+v %v", q, err)
	}
	write(time.Now().Add(-time.Minute))
	if _, err := GeminiQuota(context.Background()); err == nil || !strings.Contains(err.Error(), "lapsed") {
		t.Errorf("a lapsed sign-in is not used: %v", err)
	}
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }
