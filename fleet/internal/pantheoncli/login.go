package pantheoncli

import (
	"bufio"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

// login saves a platform key for this machine. With --key (or --key-stdin)
// the key is one the owner created in PantheonOS; with a username and
// password the command signs in once and creates a key named for this
// machine. Only the key is kept.
func (r *runner) login() error {
	hub := r.p.flags["hub"]
	if hub == "" {
		hub = os.Getenv("PANTHEON_HUB_URL")
	}
	if hub == "" {
		if saved, err := LoadCredentials(); err == nil {
			hub = saved.Hub
		} else {
			hub = DefaultHub
		}
	}
	origin, err := CheckOrigin(hub)
	if err != nil {
		return err
	}
	s := &Session{Creds: &Credentials{Hub: origin}, http: newHTTPClient()}
	in := bufio.NewReader(r.stdin)
	readLine := func() (string, error) {
		line, err := in.ReadString('\n')
		if err != nil && line == "" {
			return "", err
		}
		return strings.TrimSpace(line), nil
	}
	key := r.p.flags["key"]
	switch {
	case key != "":
	case r.p.bools["key-stdin"]:
		if key, err = readLine(); err != nil {
			return errors.New("no key on stdin")
		}
	case r.p.flags["username"] != "":
		if key, err = r.passwordLogin(s, r.p.flags["username"], in); err != nil {
			return err
		}
	default:
		fmt.Fprintf(r.stderr, "Sign in to %s.\nPaste a platform key (PantheonOS → Settings → Keys), or press Enter to use a username and password: ", origin)
		if key, err = readLine(); err != nil {
			return errors.New("sign-in cancelled")
		}
		if key == "" {
			fmt.Fprint(r.stderr, "Username or email: ")
			user, err := readLine()
			if err != nil || user == "" {
				return errors.New("sign-in cancelled")
			}
			if key, err = r.passwordLogin(s, user, in); err != nil {
				return err
			}
		}
	}
	if !strings.HasPrefix(key, "pbk_") {
		return errors.New("a platform key starts with pbk_")
	}
	s.Creds.Key = key
	id, err := s.Identify(r.ctx)
	if err != nil {
		return err
	}
	s.Creds.FleetID, s.Creds.ControllerURL = id.FleetID, id.ControllerURL
	if err := SaveCredentials(s.Creds); err != nil {
		return err
	}
	if path, err := grantCachePath(); err == nil {
		_ = os.Remove(path)
	}
	if r.json {
		return r.emit(map[string]any{"success": true, "hub": origin, "fleet_id": id.FleetID, "user": s.Creds.User})
	}
	who := ""
	if s.Creds.User != "" {
		who = " as " + s.Creds.User
	}
	fmt.Fprintf(r.stdout, "Signed in to %s%s (Fleet %s).\n", origin, who, id.FleetID)
	return nil
}

func (r *runner) passwordLogin(s *Session, user string, in *bufio.Reader) (string, error) {
	var password string
	if r.p.bools["password-stdin"] {
		line, err := in.ReadString('\n')
		if err != nil && line == "" {
			return "", errors.New("no password on stdin")
		}
		password = strings.TrimRight(line, "\r\n")
	} else {
		fmt.Fprint(r.stderr, "Password: ")
		p, err := readSecret()
		fmt.Fprintln(r.stderr)
		if err != nil {
			return "", err
		}
		password = p
	}
	var session struct {
		AccessToken string `json:"access_token"`
		User        struct {
			Username string `json:"username"`
		} `json:"user"`
	}
	err := s.request(r.ctx, http.MethodPost, s.Creds.Hub+"/api/auth/login", "",
		url.Values{"username": {user}, "password": {password}}, &session, 30*time.Second)
	if err != nil {
		return "", fmt.Errorf("signing in: %w", err)
	}
	host, _ := os.Hostname()
	name := fmt.Sprintf("pantheon CLI on %s, %s", host, time.Now().Format("2006-01-02 15:04"))
	var created struct {
		Key string `json:"key"`
	}
	if err := s.request(r.ctx, http.MethodPost, s.Creds.Hub+"/api/platform-keys/create", session.AccessToken,
		map[string]string{"name": name}, &created, 30*time.Second); err != nil {
		return "", fmt.Errorf("creating a key for this machine: %w", err)
	}
	s.Creds.User = session.User.Username
	return created.Key, nil
}
