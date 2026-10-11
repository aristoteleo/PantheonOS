package modelservices

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"

	"github.com/aristoteleo/pantheon-fleet/internal/hosted"
)

// Row is one model service in the owner's Hub directory.
type Row = map[string]any

// directory is the owner's model directory at the Hub (formerly the
// ModelServices client's management calls, pantheon/models/client.py).
type directory struct {
	hub    *hosted.Hub
	fleet  string
	secret string
}

// request sends a Hub request and decodes its JSON reply keeping numbers exact.
func (d directory) request(ctx context.Context, method, path string, body any, out any) error {
	var raw json.RawMessage
	if err := d.hub.Request(ctx, d.fleet, d.secret, method, path, body, &raw); err != nil {
		return err
	}
	if out == nil {
		return nil
	}
	return decode(raw, out)
}

func (d directory) deployments(ctx context.Context) ([]Row, error) {
	var out struct {
		Deployments []Row `json:"deployments"`
	}
	err := d.request(ctx, "GET", "/api/model-services", nil, &out)
	return out.Deployments, err
}

func (d directory) deployment(ctx context.Context, id string) (Row, error) {
	rows, err := d.deployments(ctx)
	if err != nil {
		return nil, err
	}
	for _, r := range rows {
		if r["deployment_id"] == id {
			return r, nil
		}
	}
	return nil, fmt.Errorf("This model service is not in your Fleet directory")
}

func (d directory) save(ctx context.Context, row Row) (Row, error) {
	var out Row
	err := d.request(ctx, "PUT", "/api/model-services/"+strOf(row["deployment_id"]), row, &out)
	return out, err
}

func (d directory) remove(ctx context.Context, id string, revision any) (map[string]any, error) {
	var out map[string]any
	err := d.request(ctx, "DELETE", "/api/model-services/"+id, map[string]any{"revision": revision}, &out)
	return out, err
}

func (d directory) routes(ctx context.Context) ([]any, error) {
	var out struct {
		Routes []any `json:"routes"`
	}
	err := d.request(ctx, "GET", "/api/model-services/routes", nil, &out)
	var status *hosted.StatusError
	if errors.As(err, &status) && status.Status == 404 {
		return []any{}, nil // an older Hub
	}
	return out.Routes, err
}

var routeIDRE = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)

func (d directory) routeOperation(ctx context.Context, action string, route map[string]any, routeID string, revision any, requires any) (map[string]any, error) {
	if action == "list" {
		routes, err := d.routes(ctx)
		return map[string]any{"routes": routes}, err
	}
	if action == "save" {
		routeID = strOf(route["route_id"])
	}
	if !routeIDRE.MatchString(routeID) {
		return nil, fmt.Errorf("Invalid Fleet route reference")
	}
	path := "/api/model-services/routes/" + routeID
	var out map[string]any
	var err error
	switch action {
	case "save":
		err = d.request(ctx, "PUT", path, route, &out)
	case "delete":
		err = d.request(ctx, "DELETE", path, map[string]any{"revision": revision}, &out)
	case "resolve":
		if requires == nil {
			requires = map[string]any{}
		}
		err = d.request(ctx, "POST", path+"/resolve", requires, &out)
	default:
		return nil, fmt.Errorf("Unsupported route operation")
	}
	return out, err
}
