package join

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/internal/proto"
)

func TestJoinReportsNodeOfAnotherFleet(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(proto.JoinConflict{Error: proto.JoinConflictNodeInOtherFleet, FleetID: "f_b", Message: "separate node"})
	}))
	defer srv.Close()
	_, err := Join(context.Background(), srv.URL, proto.JoinRequest{JoinToken: "t", NodeID: "n", NodePub: "p"})
	var other *OtherFleetError
	if !errors.As(err, &other) || other.FleetID != "f_b" {
		t.Fatalf("err = %v, want OtherFleetError for f_b", err)
	}
}

func TestJoinOtherConflictsStayPlainErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "node_id already bound to a different node key", http.StatusConflict)
	}))
	defer srv.Close()
	_, err := Join(context.Background(), srv.URL, proto.JoinRequest{JoinToken: "t"})
	var other *OtherFleetError
	if err == nil || errors.As(err, &other) || !strings.Contains(err.Error(), "different node key") {
		t.Fatalf("err = %v", err)
	}
}
