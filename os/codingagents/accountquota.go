package codingagents

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Agents whose CLIs report no quota of their own: their accounts' usage is
// read from the provider with the sign-in the CLI keeps on this machine. The
// sign-in is only read, never written or sent anywhere but its provider.

var (
	cursorUsageURL   = "https://cursor.com/api/usage-summary"
	geminiAssistURL  = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
	geminiQuotaURL   = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
	quotaHTTP        = &http.Client{Timeout: 10 * time.Second}
	cursorKeychainFn = cursorKeychain
)

// CursorQuota is the Cursor account's usage this billing cycle.
func CursorQuota(ctx context.Context) (Quota, error) {
	token, err := cursorToken(ctx)
	if err != nil {
		return Quota{}, err
	}
	sub, err := jwtSubject(token)
	if err != nil {
		return Quota{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, cursorUsageURL, nil)
	if err != nil {
		return Quota{}, err
	}
	// The dashboard's own session cookie, and the origin it checks.
	req.Header.Set("Cookie", "WorkosCursorSessionToken="+url.QueryEscape(sub)+"%3A%3A"+token)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Origin", "https://cursor.com")
	req.Header.Set("Referer", "https://cursor.com/dashboard")
	var body struct {
		BillingCycleEnd string `json:"billingCycleEnd"`
		MembershipType  string `json:"membershipType"`
		IsUnlimited     bool   `json:"isUnlimited"`
		IndividualUsage struct {
			Plan struct {
				Enabled          bool    `json:"enabled"`
				Used             float64 `json:"used"`
				Limit            float64 `json:"limit"`
				TotalPercentUsed float64 `json:"totalPercentUsed"`
			} `json:"plan"`
		} `json:"individualUsage"`
	}
	if err := getJSON(req, &body); err != nil {
		return Quota{}, fmt.Errorf("cursor usage: %w", err)
	}
	q := Quota{Plan: body.MembershipType, At: float64(time.Now().Unix())}
	plan := body.IndividualUsage.Plan
	if body.IsUnlimited || !plan.Enabled {
		return q, nil
	}
	used := plan.TotalPercentUsed / 100
	if used == 0 && plan.Limit > 0 {
		used = plan.Used / plan.Limit
	}
	l := Limit{Used: clamp01(used)}
	if t, err := time.Parse(time.RFC3339, body.BillingCycleEnd); err == nil {
		l.ResetsAt = t.Unix()
	}
	q.Limits = map[string]Limit{"monthly": l}
	return q, nil
}

// cursorToken is the sign-in cursor-agent keeps: the macOS Keychain for
// current CLIs, an auth file for older ones and other systems.
func cursorToken(ctx context.Context) (string, error) {
	if runtime.GOOS == "darwin" {
		if t, err := cursorKeychainFn(ctx); err == nil && t != "" {
			return t, nil
		}
	}
	home, _ := os.UserHomeDir()
	paths := []string{filepath.Join(home, ".cursor", "auth.json")}
	if x := os.Getenv("XDG_CONFIG_HOME"); x != "" {
		paths = append(paths, filepath.Join(x, "cursor", "auth.json"))
	}
	paths = append(paths, filepath.Join(home, ".config", "cursor", "auth.json"))
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		var f struct {
			AccessToken string `json:"accessToken"`
		}
		if json.Unmarshal(b, &f) == nil && f.AccessToken != "" {
			return f.AccessToken, nil
		}
	}
	return "", errors.New("no Cursor sign-in on this computer")
}

func cursorKeychain(ctx context.Context) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "/usr/bin/security", "find-generic-password",
		"-s", "cursor-access-token", "-a", "cursor-user", "-w").Output()
	return strings.TrimSpace(string(out)), err
}

func jwtSubject(token string) (string, error) {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return "", errors.New("the Cursor sign-in is not a token")
	}
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return "", errors.New("the Cursor sign-in is not a token")
	}
	var p struct {
		Sub string `json:"sub"`
	}
	if json.Unmarshal(raw, &p) != nil || p.Sub == "" {
		return "", errors.New("the Cursor sign-in names no account")
	}
	return p.Sub, nil
}

// GeminiQuota is the Gemini Code Assist account's daily quota: the model
// closest to its limit. It needs the sign-in Gemini CLI keeps while still
// valid; the CLI renews it whenever it runs.
func GeminiQuota(ctx context.Context) (Quota, error) {
	home, _ := os.UserHomeDir()
	b, err := os.ReadFile(filepath.Join(home, ".gemini", "oauth_creds.json"))
	if err != nil {
		return Quota{}, errors.New("no Gemini sign-in on this computer")
	}
	var creds struct {
		AccessToken string  `json:"access_token"`
		ExpiryDate  float64 `json:"expiry_date"` // unix ms
	}
	if json.Unmarshal(b, &creds) != nil || creds.AccessToken == "" {
		return Quota{}, errors.New("no Gemini sign-in on this computer")
	}
	if time.UnixMilli(int64(creds.ExpiryDate)).Before(time.Now().Add(30 * time.Second)) {
		return Quota{}, errors.New("the Gemini sign-in has lapsed; Gemini CLI renews it when it runs")
	}
	var assist struct {
		Project     json.RawMessage `json:"cloudaicompanionProject"`
		CurrentTier struct {
			ID   string `json:"id"`
			Name string `json:"name"`
		} `json:"currentTier"`
	}
	if err := postJSON(ctx, geminiAssistURL, creds.AccessToken, map[string]any{"metadata": map[string]string{
		"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}}, &assist); err != nil {
		return Quota{}, fmt.Errorf("gemini code assist: %w", err)
	}
	project := ""
	var asObject struct {
		ID string `json:"id"`
	}
	if json.Unmarshal(assist.Project, &project) != nil && json.Unmarshal(assist.Project, &asObject) == nil {
		project = asObject.ID
	}
	q := Quota{Plan: firstNonEmpty(assist.CurrentTier.Name, assist.CurrentTier.ID), At: float64(time.Now().Unix())}
	if project == "" {
		return q, errors.New("this Google account has no Gemini Code Assist tier")
	}
	var quota struct {
		Buckets []struct {
			RemainingFraction float64 `json:"remainingFraction"`
			ResetTime         string  `json:"resetTime"`
			ModelID           string  `json:"modelId"`
		} `json:"buckets"`
	}
	if err := postJSON(ctx, geminiQuotaURL, creds.AccessToken, map[string]string{"project": project}, &quota); err != nil {
		return q, fmt.Errorf("gemini quota: %w", err)
	}
	best := Limit{Used: -1}
	for _, bk := range quota.Buckets {
		used := clamp01(1 - bk.RemainingFraction)
		if used > best.Used {
			best = Limit{Used: used}
			if t, err := time.Parse(time.RFC3339, bk.ResetTime); err == nil {
				best.ResetsAt = t.Unix()
			}
		}
	}
	if best.Used >= 0 {
		q.Limits = map[string]Limit{"daily": best}
	}
	return q, nil
}

func postJSON(ctx context.Context, u, token string, in, out any) error {
	b, err := json.Marshal(in)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	return getJSON(req, out)
}

func getJSON(req *http.Request, out any) error {
	res, err := quotaHTTP.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode/100 != 2 {
		return fmt.Errorf("HTTP %d", res.StatusCode)
	}
	return json.NewDecoder(res.Body).Decode(out)
}

func clamp01(f float64) float64 {
	if f < 0 {
		return 0
	}
	if f > 1 {
		return 1
	}
	return f
}
