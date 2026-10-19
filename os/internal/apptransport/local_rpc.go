package apptransport

import (
	"net/url"
	"strconv"
)

// ValidLocalRPCOrigin is an explicit product-profile endpoint, not a general
// exception to generation-specific browser origins. RPC grants reject browser
// headers and carry both exact instance identities; HTTP/browser grants cannot
// use this shared origin. Only the local product launcher may configure it.
func ValidLocalRPCOrigin(origin string) bool {
	u, err := url.Parse(origin)
	if err != nil {
		return false
	}
	port, err := strconv.Atoi(u.Port())
	return err == nil && port > 0 && port < 65536 &&
		origin == "https://127.0.0.1:"+strconv.Itoa(port)
}
