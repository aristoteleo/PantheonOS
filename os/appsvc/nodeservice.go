package appsvc

import (
	"crypto/sha256"
	"encoding/hex"
)

// A node service (the Files, PTY and Coding Agents builtins the controller
// starts on one node in the owner's App namespace) has a fixed scope and
// service id, so whoever knows the Fleet and the node can address it: the
// controller when it starts one, a node's service when it calls another's.

// NodeScope is the App scope of one node's services.
func NodeScope(nodeID string) string {
	sum := sha256.Sum256([]byte(nodeID))
	return "node" + hex.EncodeToString(sum[:])[:16] + "-app"
}

// NodeServiceID is the service id of App appID's node service on nodeID in
// fleet; its RPC subject is [prefix.]pantheon.service.<id>.
func NodeServiceID(fleet, appID, nodeID string) string {
	sum := sha256.Sum256([]byte(fleet + "-app-" + appID + "-" + NodeScope(nodeID)))
	return hex.EncodeToString(sum[:])
}
