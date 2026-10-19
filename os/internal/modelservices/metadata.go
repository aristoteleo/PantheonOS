package modelservices

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// Suggested publish settings for models an external API reports without a
// context length, from OpenRouter's public model list (formerly
// pantheonos/models/model_metadata.py). Suggestions only pre-fill a form.

const openRouterModels = "https://openrouter.ai/api/v1/models"

var openRouter struct {
	sync.Mutex
	at     time.Time
	models []map[string]any
}

// OpenRouterURL is replaced by tests.
var OpenRouterURL = openRouterModels

func openRouterList(ctx context.Context) ([]map[string]any, error) {
	openRouter.Lock()
	defer openRouter.Unlock()
	if time.Since(openRouter.at) < time.Hour && len(openRouter.models) > 0 {
		return openRouter.models, nil
	}
	c, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(c, "GET", OpenRouterURL, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, io.ErrUnexpectedEOF
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		return nil, err
	}
	var doc struct {
		Data []map[string]any `json:"data"`
	}
	if err := decode(raw, &doc); err != nil {
		return nil, err
	}
	openRouter.at, openRouter.models = time.Now(), doc.Data
	return doc.Data, nil
}

var latestRE = regexp.MustCompile(`-latest$`)

func baseModel(id string) string {
	parts := strings.Split(strings.TrimLeft(id, "~"), "/")
	name := strings.ToLower(strings.Split(parts[len(parts)-1], ":")[0])
	return latestRE.ReplaceAllString(name, "")
}

func matchModel(models []map[string]any, id, provider string) map[string]any {
	wanted := baseModel(id)
	var candidates []map[string]any
	for _, m := range models {
		if baseModel(strOf(m["id"])) == wanted {
			candidates = append(candidates, m)
		}
	}
	if provider != "" {
		var own []map[string]any
		for _, m := range candidates {
			if strings.HasPrefix(strings.TrimLeft(strOf(m["id"]), "~"), provider+"/") {
				own = append(own, m)
			}
		}
		if len(own) > 0 {
			candidates = own
		}
	}
	sort.SliceStable(candidates, func(i, j int) bool {
		a, b := strOf(candidates[i]["id"]), strOf(candidates[j]["id"])
		if strings.HasPrefix(a, "~") != strings.HasPrefix(b, "~") {
			return strings.HasPrefix(a, "~")
		}
		return len(a) < len(b)
	})
	if len(candidates) == 0 {
		return nil
	}
	return candidates[0]
}

func suggestion(entry map[string]any) map[string]any {
	params := map[string]bool{}
	for _, p := range listOf(entry["supported_parameters"]) {
		params[strOf(p)] = true
	}
	vision := false
	for _, m := range listOf(mapOf(entry["architecture"])["input_modalities"]) {
		vision = vision || m == "image"
	}
	var context any
	if c := entry["context_length"]; c != nil && intOf(c) != 0 {
		context = number(intOf(c))
	}
	return map[string]any{"context": context, "tools": params["tools"], "reasoning": params["reasoning"] || params["include_reasoning"],
		"vision": vision, "source": "openrouter:" + strOf(entry["id"])}
}

// suggest returns {model id: suggestion} for ids OpenRouter knows; it never
// fails discovery.
func suggest(ctx context.Context, ids []string, provider string) map[string]any {
	models, err := openRouterList(ctx)
	if err != nil {
		return map[string]any{}
	}
	found := map[string]any{}
	for _, id := range ids {
		if entry := matchModel(models, id, provider); entry != nil {
			found[id] = suggestion(entry)
		}
	}
	return found
}

var _ = json.Valid
