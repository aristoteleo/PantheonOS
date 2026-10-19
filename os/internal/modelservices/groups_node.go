package modelservices

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"strings"
)

// Node operations of group members (FleetLifecycle.group_* in
// pantheon/apps/lifecycle.py). Every operation is idempotent on its original
// binding; a lost reply retries the same request.

// groupAuthority runs a public certificate operation on the rank-zero node.
// Prepare takes the topology; the others the group id and fingerprint.
func (n nodes) groupAuthority(ctx context.Context, node, action string, peers *Topology, claim map[string]any) (map[string]any, error) {
	groupID := strOf(peers.doc["group_id"])
	args := map[string]any{"group_id": groupID, "topology_sha256": peers.Fingerprint}
	switch action {
	case "prepare":
		if peers.Member(0)["node_id"] != node {
			return nil, fmt.Errorf("The original rank-zero node owns the group authority")
		}
		args = map[string]any{"group_topology": peers.Document()}
	case "issue":
		args["group_claim"] = claim
	case "status", "close":
	default:
		return nil, fmt.Errorf("Unsupported group authority action")
	}
	result, err := n.call(ctx, node, "group_authority_"+action, args)
	if err != nil {
		return nil, err
	}
	if !intIn(result["protocol"], 1, 1) || result["group_id"] != groupID || result["topology_sha256"] != peers.Fingerprint {
		return nil, fmt.Errorf("Node returned a different group authority identity")
	}
	if action == "issue" {
		for _, k := range []string{"rank", "node_id", "instance_id", "revision", "generation"} {
			if !same(result[k], claim[k]) {
				return nil, fmt.Errorf("Node returned a certificate for another group member")
			}
		}
		for _, k := range []string{"certificate_pem", "ca_pem"} {
			if s, ok := result[k].(string); !ok || len(s) == 0 || len(s) > 16384 {
				return nil, fmt.Errorf("Node returned incomplete public certificates")
			}
		}
	} else if result["node_id"] != node || (result["state"] != "open" && result["state"] != "closed") {
		return nil, fmt.Errorf("Node returned an invalid group authority state")
	}
	if action == "prepare" && result["owner"] != peers.doc["owner"] {
		return nil, fmt.Errorf("Node returned another Fleet owner’s group authority")
	}
	return result, nil
}

// caDigest is the SHA-256 of a single PEM certificate's DER bytes.
func caDigest(value any) (string, error) {
	text, ok := value.(string)
	if !ok || len(text) == 0 || len(text) > 16384 || strings.Count(text, "-----BEGIN CERTIFICATE-----") != 1 {
		return "", fmt.Errorf("Missing original public authority certificate")
	}
	block, _ := pem.Decode([]byte(text))
	if block == nil || block.Type != "CERTIFICATE" {
		return "", fmt.Errorf("Missing original public authority certificate")
	}
	sum := sha256.Sum256(block.Bytes)
	return hex.EncodeToString(sum[:]), nil
}

// groupPeer enrolls (or, with certificates, installs) the prepared rank's
// key; private keys never leave the node.
func (n nodes) groupPeer(ctx context.Context, binding map[string]any, certificate, authority string) (map[string]any, error) {
	data := map[string]any{"instance_id": binding["instance_id"], "revision": binding["revision"], "generation": binding["generation"]}
	method := "group_peer_enroll"
	if certificate != "" || authority != "" {
		method = "group_peer_install"
		data["certificate_pem"], data["ca_pem"] = certificate, authority
	}
	result, err := n.call(ctx, strOf(binding["node_id"]), method, data)
	if err != nil {
		return nil, err
	}
	if !intIn(result["protocol"], 1, 1) || result["instance_id"] != binding["instance_id"] || result["revision"] != binding["revision"] ||
		intOf(result["generation"]) != intOf(binding["generation"])+1 {
		return nil, fmt.Errorf("Node returned credentials for a different group attempt")
	}
	return result, nil
}

// groupOverlay enrolls only public network material on one member node.
func (n nodes) groupOverlay(ctx context.Context, node, action string, peers *Topology, args map[string]any) (map[string]any, error) {
	if action != "prepare" {
		pinned := map[string]any{"group_id": peers.doc["group_id"], "topology_sha256": peers.Fingerprint}
		for k, v := range args {
			pinned[k] = v
		}
		args = pinned
	}
	result, err := n.call(ctx, node, "group_overlay_"+action, map[string]any{"group_overlay": args})
	if err != nil {
		return nil, err
	}
	if err := checkOverlayReply(result, node, peers, ""); err != nil {
		return nil, err
	}
	endpoint := mapOf(result["endpoint"])
	switch {
	case action == "prepare" && result["state"] != "closed" &&
		(!same(endpoint["rank"], mapOf(args["manifest"])["rank"]) || endpoint["address"] != args["address"]):
		return nil, fmt.Errorf("Node changed the original local network enrollment")
	case action == "pin" && (result["state"] != "pinned" || !same(result["endpoints"], args["endpoints"])):
		return nil, fmt.Errorf("Node did not acknowledge the exact pinned roster")
	case action == "close" && result["state"] != "closed":
		return nil, fmt.Errorf("Node did not close the original network enrollment")
	}
	return result, nil
}

func checkOverlayReply(reply map[string]any, node string, peers *Topology, state string) error {
	bad := fmt.Errorf("Node returned another original overlay identity or state")
	if reply == nil || !intIn(reply["protocol"], 1, 1) || reply["owner"] != peers.doc["owner"] || reply["node_id"] != node ||
		reply["group_id"] != peers.doc["group_id"] || reply["topology_sha256"] != peers.Fingerprint {
		return bad
	}
	if s := reply["state"]; (s != "prepared" && s != "pinned" && s != "closed") || (state != "" && s != state) {
		return bad
	}
	if e := mapOf(reply["endpoint"]); e != nil {
		if err := overlayEndpoint(e); err != nil {
			return err
		}
		if int(intOf(e["rank"])) >= len(peers.Members()) || peers.Member(int(intOf(e["rank"])))["node_id"] != node {
			return fmt.Errorf("Node returned another rank endpoint")
		}
	}
	if reply["state"] != "closed" && reply["endpoint"] == nil {
		return fmt.Errorf("Live enrollment requires its original public endpoint")
	}
	roster := listOf(reply["endpoints"])
	if len(roster) > 0 {
		if reply["state"] == "prepared" || reply["endpoint"] == nil {
			return fmt.Errorf("Roster requires a pinned original enrollment")
		}
		if err := validateRoster(roster, len(peers.Members())); err != nil {
			return err
		}
		if !same(roster[intOf(mapOf(reply["endpoint"])["rank"])], reply["endpoint"]) {
			return fmt.Errorf("Roster replaced the local endpoint")
		}
	}
	if reply["state"] == "pinned" && len(roster) == 0 {
		return fmt.Errorf("Pinned enrollment requires the complete roster")
	}
	return nil
}

func overlayEndpoint(e map[string]any) error {
	if !keys(e, "rank", "address", "public_key") || !intIn(e["rank"], 0, 15) {
		return fmt.Errorf("Declare only rank, private UDP address and public key")
	}
	if err := privateEndpoint(e["address"]); err != nil {
		return err
	}
	key, _ := e["public_key"].(string)
	raw, err := decodeBase64(key)
	if len(key) != 44 || err != nil || len(raw) != 32 || allZero(raw) {
		return fmt.Errorf("Declare a canonical public X25519 key")
	}
	return nil
}

func allZero(b []byte) bool {
	for _, c := range b {
		if c != 0 {
			return false
		}
	}
	return true
}

// validateRoster checks a complete public endpoint roster.
func validateRoster(roster []any, count int) error {
	if len(roster) != count {
		return fmt.Errorf("Persist only the complete public endpoint roster")
	}
	addresses, keys := make([]any, 0, count), map[any]bool{}
	for rank, raw := range roster {
		e := mapOf(raw)
		if e == nil {
			return fmt.Errorf("Invalid public endpoint roster")
		}
		if err := overlayEndpoint(e); err != nil {
			return err
		}
		if intOf(e["rank"]) != int64(rank) {
			return fmt.Errorf("Public endpoint roster changed the pinned rank or host address")
		}
		addresses = append(addresses, e["address"])
		keys[e["public_key"]] = true
	}
	if len(keys) != count {
		return fmt.Errorf("Network readiness requires every distinct original public key")
	}
	return underlayAddresses(addresses, count)
}

// groupInference calls the pinned leader's resume/drain control.
func (n nodes) groupInference(ctx context.Context, binding map[string]any, method string, args map[string]any) (map[string]any, error) {
	value, err := n.rpc(ctx, binding, method, args)
	if err != nil {
		return nil, fmt.Errorf("Original group inference control is unavailable")
	}
	return value, nil
}

// fenceStart prevents a missing exact install/prepare/start; it never
// cancels accepted work.
func (n nodes) fenceStart(ctx context.Context, node string, request map[string]any) error {
	_, err := n.call(ctx, node, "fence_start", map[string]any{"request": request})
	return err
}

// decodeBase64 accepts only canonical padded standard base64.
func decodeBase64(s string) ([]byte, error) {
	raw, err := base64.StdEncoding.Strict().DecodeString(s)
	if err == nil && base64.StdEncoding.EncodeToString(raw) != s {
		err = fmt.Errorf("non-canonical base64")
	}
	return raw, err
}
