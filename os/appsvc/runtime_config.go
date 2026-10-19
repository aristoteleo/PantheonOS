package appsvc

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strconv"
)

// RuntimeValues reads this component's prepared, generation-bound configuration.
// It never returns credentials or falls back after an invalid configured file.
// Native Apps still run under their node OS user's trust boundary.
func RuntimeValues() (map[string]json.RawMessage, error) {
	location := os.Getenv("PANTHEON_APP_CONFIG")
	if location == "" {
		return nil, nil
	}
	invalid := fmt.Errorf("App runtime configuration is unavailable, invalid or stale")
	f, err := os.Open(location)
	if err != nil {
		return nil, invalid
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, (256<<10)+1))
	if err != nil || len(raw) > 256<<10 {
		return nil, invalid
	}
	var value struct {
		Protocol    int                        `json:"protocol"`
		Owner       string                     `json:"owner"`
		Node        string                     `json:"node_id"`
		Instance    string                     `json:"instance_id"`
		Revision    string                     `json:"revision"`
		Generation  uint64                     `json:"generation"`
		Component   string                     `json:"component"`
		Values      map[string]json.RawMessage `json:"values"`
		Credentials map[string]struct {
			Endpoint string `json:"endpoint"`
			Key      string `json:"key"`
		} `json:"credentials"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&value) != nil || decoder.Decode(new(any)) != io.EOF || value.Protocol != 1 ||
		value.Values == nil || value.Credentials == nil || value.Generation == 0 ||
		strconv.FormatUint(value.Generation, 10) != os.Getenv("PANTHEON_INSTANCE_GENERATION") {
		return nil, invalid
	}
	for name, expected := range map[string]string{
		"PANTHEON_FLEET_ID": value.Owner, "PANTHEON_NODE_ID": value.Node,
		"PANTHEON_INSTANCE_ID": value.Instance, "PANTHEON_APP_REVISION": value.Revision,
		"PANTHEON_COMPONENT_NAME": value.Component,
	} {
		if expected == "" || os.Getenv(name) != expected {
			return nil, invalid
		}
	}
	for _, credential := range value.Credentials {
		if credential.Endpoint == "" || credential.Key == "" {
			return nil, invalid
		}
	}
	return value.Values, nil
}
