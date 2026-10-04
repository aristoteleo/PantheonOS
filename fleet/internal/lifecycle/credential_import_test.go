package lifecycle

import (
	"context"
	"encoding/json"
	"github.com/aristoteleo/pantheon-fleet/internal/modelcredentials"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"strings"
	"testing"
)

func TestCredentialImportDispatch(t *testing.T) {
	m, err := Open(t.TempDir(), "owner", "node", proto.Capability{}, NativeDriver{})
	if err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	q := Command{Protocol: 1, Type: "app_lifecycle", Method: "credential_prepare", CredentialRef: "node-secret://budget", CredentialEndpoint: "https://api.test"}
	result, err := m.Dispatch(context.Background(), q)
	if err != nil {
		t.Fatal(err)
	}
	challenge := result.(modelcredentials.ImportChallenge)
	if challenge.Owner != "owner" || challenge.Node != "node" || challenge.Endpoint != "https://api.test/v1" {
		t.Fatal(challenge)
	}
	snapshot := m.Snapshot()
	if snapshot.CredentialImportProtocol != 1 {
		t.Fatal("missing live capability")
	}
	data, _ := json.Marshal(snapshot)
	if strings.Contains(string(data), challenge.ID) || strings.Contains(string(data), challenge.PublicKey) {
		t.Fatal("challenge leaked into ledger")
	}
	if m.ledger.CredentialImportProtocol != 0 {
		t.Fatal("live capability persisted")
	}
	q.CredentialEnvelope = &modelcredentials.ImportEnvelope{}
	if _, err = m.Dispatch(context.Background(), q); err != modelcredentials.ErrImport {
		t.Fatal("mixed request accepted")
	}
	if err = StrictDecode([]byte(`{"type":"app_lifecycle","protocol":1,"method":"credential_ensure","key":"plaintext"}`), &Command{}); err == nil {
		t.Fatal("plaintext field accepted")
	}
	importer := m.credentialImporter
	if err = m.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err = m.Dispatch(context.Background(), Command{Protocol: 1, Method: "credential_prepare"}); err != modelcredentials.ErrImport {
		t.Fatal("closed manager accepted delivery")
	}
	if _, err = importer.Prepare("node-secret://budget", "https://api.test"); err != modelcredentials.ErrImport {
		t.Fatal("importer not closed")
	}
}
