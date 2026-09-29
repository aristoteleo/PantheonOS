package join

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"net/http"
	"strings"
)

func Delegate(ctx context.Context, controller string, in proto.DelegateRequest) (proto.DelegateResponse, error) {
	var out proto.DelegateResponse
	b, _ := json.Marshal(in)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(controller, "/")+"/delegate", bytes.NewReader(b))
	if err != nil {
		return out, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := httpClient.Do(req)
	if err != nil {
		return out, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return out, fmt.Errorf("delegated node authorization: HTTP %d (update the Controller if unsupported)", resp.StatusCode)
	}
	err = json.NewDecoder(resp.Body).Decode(&out)
	if err == nil && (out.NodeID == "" || out.Creds == "" || out.ExpiresAt == 0) {
		err = fmt.Errorf("incomplete delegated authorization")
	}
	return out, err
}
