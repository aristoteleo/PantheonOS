package childenv

import "testing"

func TestCredentialsStayWithTheNode(t *testing.T) {
	got := Filter([]string{"PATH=/bin", "FLEET_KEY=k", "MODAL_TOKEN_SECRET=s", "LLM_API_KEY=mine",
		"PANTHEON_APP_CONFIG=c", "nats_jwt=lower", "HOME=/root"})
	want := []string{"PATH=/bin", "LLM_API_KEY=mine", "HOME=/root"}
	if len(got) != len(want) {
		t.Fatalf("%v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%v", got)
		}
	}
}
