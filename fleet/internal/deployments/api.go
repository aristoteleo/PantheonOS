package deployments

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
)

// FleetResolver maps an owner key to its fleet id (the controller's resolver).
type FleetResolver func(key string) (string, bool)

// Auth identifies the fleet a request acts for. The Hub calls with the
// controller service token and names the authenticated owner's fleet in
// X-Fleet-Id; a local (single-machine) controller is called with the owner key.
type Auth struct {
	ServiceToken string
	Resolve      FleetResolver
}

// Fleet returns the fleet a request acts for.
func (a Auth) Fleet(r *http.Request) (string, bool) {
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") {
		return "", false
	}
	token := strings.TrimPrefix(header, "Bearer ")
	if a.ServiceToken != "" && subtle.ConstantTimeCompare([]byte(token), []byte(a.ServiceToken)) == 1 {
		fleet := r.Header.Get("X-Fleet-Id")
		return fleet, fleetRE.MatchString(fleet)
	}
	if a.Resolve != nil {
		return a.Resolve(token)
	}
	return "", false
}

// Register adds the deployments API to mux:
//
//	GET    /deployments
//	GET    /deployments/{name}
//	PUT    /deployments/{name}               {revision, spec}
//	PATCH  /deployments/{name}/apps/{app}    {revision, intent?, placement?, config?}
//	DELETE /deployments/{name}               {revision}
//	GET    /secrets                          names, endpoints, versions (no values)
//	PUT    /secrets/{name}                   {value, endpoint}
//	DELETE /secrets/{name}
func Register(mux *http.ServeMux, store *Store, secrets *Secrets, auth Auth) {
	with := func(h func(http.ResponseWriter, *http.Request, string)) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			fleet, ok := auth.Fleet(r)
			if !ok {
				httpError(w, http.StatusUnauthorized, "unauthorized")
				return
			}
			w.Header().Set("Cache-Control", "no-store")
			h(w, r, fleet)
		}
	}
	mux.HandleFunc("GET /deployments", with(func(w http.ResponseWriter, r *http.Request, fleet string) {
		writeJSON(w, http.StatusOK, map[string]any{"deployments": nonNil(store.List(fleet))})
	}))
	mux.HandleFunc("GET /deployments/{name}", with(func(w http.ResponseWriter, r *http.Request, fleet string) {
		d, err := store.Get(fleet, r.PathValue("name"))
		if err != nil {
			storeError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, d)
	}))
	mux.HandleFunc("PUT /deployments/{name}", with(func(w http.ResponseWriter, r *http.Request, fleet string) {
		var body struct {
			Revision *int64 `json:"revision"`
			Spec     *Spec  `json:"spec"`
		}
		if !decode(w, r, &body) {
			return
		}
		if body.Revision == nil || body.Spec == nil {
			httpError(w, http.StatusUnprocessableEntity, "supply revision and spec")
			return
		}
		d, err := store.Put(fleet, r.PathValue("name"), *body.Revision, *body.Spec)
		if err != nil {
			storeError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, d)
	}))
	mux.HandleFunc("PATCH /deployments/{name}/apps/{app}", with(func(w http.ResponseWriter, r *http.Request, fleet string) {
		var body struct {
			Revision  *int64                     `json:"revision"`
			Intent    *Intent                    `json:"intent"`
			Placement *Placement                 `json:"placement"`
			Config    map[string]json.RawMessage `json:"config"`
		}
		if !decode(w, r, &body) {
			return
		}
		if body.Revision == nil || (body.Intent == nil && body.Placement == nil && body.Config == nil) {
			httpError(w, http.StatusUnprocessableEntity, "supply revision and an intent, placement or config change")
			return
		}
		d, err := store.Patch(fleet, r.PathValue("name"), *body.Revision, r.PathValue("app"), func(a *AppSpec) error {
			if body.Intent != nil {
				a.Intent = *body.Intent
			}
			if body.Placement != nil {
				a.Placement = *body.Placement
			}
			if body.Config != nil {
				a.Config = body.Config
			}
			return nil
		})
		if err != nil {
			storeError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, d)
	}))
	mux.HandleFunc("DELETE /deployments/{name}", with(func(w http.ResponseWriter, r *http.Request, fleet string) {
		var body struct {
			Revision *int64 `json:"revision"`
		}
		if !decode(w, r, &body) {
			return
		}
		if body.Revision == nil {
			httpError(w, http.StatusUnprocessableEntity, "supply the revision")
			return
		}
		d, err := store.Get(fleet, r.PathValue("name"))
		if err != nil {
			storeError(w, err)
			return
		}
		// Removing desired state must not orphan running Apps: stop them first
		// (intent: stopped), let the reconciler converge, then delete.
		for name, app := range d.Spec.Apps {
			if app.Intent != Stopped {
				httpError(w, http.StatusConflict, "stop App "+name+" before deleting the deployment")
				return
			}
			if s, ok := d.Status.Apps[name]; ok && s.State != "" && s.State != "stopped" {
				httpError(w, http.StatusConflict, "App "+name+" is still "+s.State)
				return
			}
		}
		if err := store.Delete(fleet, d.Name, *body.Revision); err != nil {
			storeError(w, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	if secrets == nil {
		return
	}
	mux.HandleFunc("GET /secrets", with(func(w http.ResponseWriter, r *http.Request, fleet string) {
		list := secrets.List(fleet)
		if list == nil {
			list = []SecretInfo{}
		}
		writeJSON(w, http.StatusOK, map[string]any{"secrets": list})
	}))
	mux.HandleFunc("PUT /secrets/{name}", with(func(w http.ResponseWriter, r *http.Request, fleet string) {
		var body struct {
			Value    string `json:"value"`
			Endpoint string `json:"endpoint"`
		}
		if !decode(w, r, &body) {
			return
		}
		info, err := secrets.Put(fleet, r.PathValue("name"), body.Value, body.Endpoint)
		if err != nil {
			httpError(w, http.StatusUnprocessableEntity, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, info)
	}))
	mux.HandleFunc("DELETE /secrets/{name}", with(func(w http.ResponseWriter, r *http.Request, fleet string) {
		name := r.PathValue("name")
		for _, d := range store.List(fleet) {
			for _, declared := range d.Spec.Secrets {
				if declared == name {
					httpError(w, http.StatusConflict, "deployment "+d.Name+" uses secret "+name)
					return
				}
			}
		}
		if err := secrets.Delete(fleet, name); err != nil {
			httpError(w, http.StatusNotFound, err.Error())
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
}

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	raw, err := io.ReadAll(io.LimitReader(r.Body, 2*MaxSpecBytes+1))
	if err != nil || len(raw) > 2*MaxSpecBytes {
		httpError(w, http.StatusRequestEntityTooLarge, "request too large")
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(v); err != nil {
		httpError(w, http.StatusBadRequest, "invalid JSON body")
		return false
	}
	return true
}

func storeError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrNotFound):
		httpError(w, http.StatusNotFound, err.Error())
	case errors.Is(err, ErrConflict):
		httpError(w, http.StatusConflict, err.Error())
	case errors.Is(err, ErrLimit):
		httpError(w, http.StatusConflict, err.Error())
	case strings.Contains(err.Error(), "recovery required"), strings.Contains(err.Error(), "restart the controller"):
		httpError(w, http.StatusServiceUnavailable, err.Error())
	default:
		httpError(w, http.StatusUnprocessableEntity, err.Error())
	}
}

func httpError(w http.ResponseWriter, status int, detail string) {
	writeJSON(w, status, map[string]string{"detail": detail})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func nonNil(v []Deployment) []Deployment {
	if v == nil {
		return []Deployment{}
	}
	return v
}
