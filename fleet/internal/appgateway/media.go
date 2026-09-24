package appgateway

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appmedia"
)

type MediaDispatch func(context.Context, appmedia.Request) (appmedia.Answer, error)

// SetMediaDispatch runs before serving requests. The browser uses the existing
// App cookie; the immutable binding and credential never come from browser JSON.
func (g *Gateway) SetMediaDispatch(dispatch MediaDispatch) { g.media = dispatch }

func (g *Gateway) mediaOffer(w http.ResponseWriter, r *http.Request, access AttachRequest) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" || access.Workload || r.Header.Get("Origin") != access.UIOrigin || !g.origins[access.UIOrigin] {
		http.Error(w, "open direct model media through Atrium", 403)
		return
	}
	var offer appmedia.Offer
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 70000))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&offer) != nil || decoder.Decode(new(any)) != io.EOF || !offer.Valid() {
		http.Error(w, "invalid direct model media offer", 400)
		return
	}
	if g.media == nil {
		http.Error(w, "update Fleet to support direct model media", 503)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	if err := g.verify(ctx, access.Binding); err != nil {
		http.Error(w, "App generation unavailable", 409)
		return
	}
	expires := min(access.Expires, time.Now().Add(appmedia.MaxLifetime).Unix())
	answer, err := g.media(ctx, appmedia.Request{Binding: access.Binding, Offer: offer, Credential: access.Credential, Expires: expires})
	if err != nil || answer.Transport != "fleet_browser_direct" || answer.Expires != expires || len(answer.SDP) < 16 || len(answer.SDP) > 60000 {
		http.Error(w, "Direct media is unavailable on this node; no Relay fallback was used", 503)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(answer)
}
