// Package token implements the Fleet refresh token: a stateless, Controller-
// signed blob that binds a node's public key to a fleet with an expiry. On
// refresh (/token) the node must present the token AND a fresh signature made
// with its private key (proof-of-possession), so a leaked refresh token is
// useless to anyone who doesn't hold the node key. See docs/fleet-security-model.md.
package token

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// Payload is the signed content of a refresh token. The node's public key IS its
// identity here — the private key never leaves the node.
type Payload struct {
	FleetID string `json:"fleet_id"`
	NodePub string `json:"node_pub"` // base64(std) Ed25519 public key of the node
	Exp     int64  `json:"exp"`      // unix seconds
}

// popSkew bounds how stale a proof-of-possession challenge may be.
const popSkew = 120 * time.Second

var b64 = base64.RawURLEncoding

// Sign returns a "payload.sig" refresh token signed by the Controller key.
func Sign(controllerPriv ed25519.PrivateKey, p Payload) (string, error) {
	pj, err := json.Marshal(p)
	if err != nil {
		return "", err
	}
	sig := ed25519.Sign(controllerPriv, pj)
	return b64.EncodeToString(pj) + "." + b64.EncodeToString(sig), nil
}

// Verify checks the Controller signature and expiry, returning the payload.
func Verify(controllerPub ed25519.PublicKey, tok string) (Payload, error) {
	var p Payload
	dot := -1
	for i := 0; i < len(tok); i++ {
		if tok[i] == '.' {
			dot = i
			break
		}
	}
	if dot < 0 {
		return p, errors.New("malformed refresh token")
	}
	pj, err := b64.DecodeString(tok[:dot])
	if err != nil {
		return p, errors.New("bad token payload")
	}
	sig, err := b64.DecodeString(tok[dot+1:])
	if err != nil {
		return p, errors.New("bad token signature")
	}
	if !ed25519.Verify(controllerPub, pj, sig) {
		return p, errors.New("refresh token signature invalid")
	}
	if err := json.Unmarshal(pj, &p); err != nil {
		return p, err
	}
	if time.Now().Unix() > p.Exp {
		return p, errors.New("refresh token expired")
	}
	return p, nil
}

// PoPChallenge is the message a node signs to prove it holds the node key. It
// binds the node public key + fleet + timestamp so a captured signature can't be
// reused for another node or after the skew window.
func PoPChallenge(nodePub, fleetID string, ts int64) string {
	return fmt.Sprintf("%s:%s:%d", nodePub, fleetID, ts)
}

// VerifyPoP checks that sigB64 is a valid node-key signature over the challenge
// for a recent ts, using the node public key from the refresh token payload.
func VerifyPoP(p Payload, ts int64, sigB64 string) error {
	if d := time.Now().Unix() - ts; d > int64(popSkew.Seconds()) || d < -int64(popSkew.Seconds()) {
		return errors.New("stale proof-of-possession challenge")
	}
	pub, err := base64.StdEncoding.DecodeString(p.NodePub)
	if err != nil || len(pub) != ed25519.PublicKeySize {
		return errors.New("bad node public key")
	}
	sig, err := base64.StdEncoding.DecodeString(sigB64)
	if err != nil {
		return errors.New("bad proof-of-possession signature")
	}
	if !ed25519.Verify(ed25519.PublicKey(pub), []byte(PoPChallenge(p.NodePub, p.FleetID, ts)), sig) {
		return errors.New("proof-of-possession verification failed")
	}
	return nil
}

// --- Join tokens: single-use, short-lived; authorize adding ONE machine -------

// JoinPayload authorizes one machine to join a fleet.
type JoinPayload struct {
	FleetID string `json:"fleet_id"`
	JTI     string `json:"jti"` // unique id; the Controller enforces single-use
	Exp     int64  `json:"exp"`
}

// SignJoin returns a Controller-signed join token.
func SignJoin(controllerPriv ed25519.PrivateKey, p JoinPayload) (string, error) {
	pj, err := json.Marshal(p)
	if err != nil {
		return "", err
	}
	sig := ed25519.Sign(controllerPriv, pj)
	return b64.EncodeToString(pj) + "." + b64.EncodeToString(sig), nil
}

// VerifyJoin checks the signature + expiry of a join token (single-use is
// enforced by the Controller tracking consumed JTIs).
func VerifyJoin(controllerPub ed25519.PublicKey, tok string) (JoinPayload, error) {
	var p JoinPayload
	dot := strings.IndexByte(tok, '.')
	if dot < 0 {
		return p, errors.New("malformed join token")
	}
	pj, err := b64.DecodeString(tok[:dot])
	if err != nil {
		return p, errors.New("bad join token payload")
	}
	sig, err := b64.DecodeString(tok[dot+1:])
	if err != nil {
		return p, errors.New("bad join token signature")
	}
	if !ed25519.Verify(controllerPub, pj, sig) {
		return p, errors.New("join token signature invalid")
	}
	if err := json.Unmarshal(pj, &p); err != nil {
		return p, err
	}
	if time.Now().Unix() > p.Exp {
		return p, errors.New("join token expired")
	}
	return p, nil
}
