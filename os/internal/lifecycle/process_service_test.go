package lifecycle

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestNativeHTTPHelper(t *testing.T) {
	if os.Getenv("FLEET_TEST_HTTP_CHILD") != "1" {
		return
	}
	http.HandleFunc("/", func(w http.ResponseWriter, _ *http.Request) { fmt.Fprint(w, "native-office-service") })
	if err := http.ListenAndServe("127.0.0.1:"+os.Getenv("PANTHEON_PORT_HTTP"), nil); err != nil {
		os.Exit(2)
	}
	os.Exit(0)
}

func TestProcessServicesGetIndependentLoopbackPorts(t *testing.T) {
	root := t.TempDir()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	p := Paths{Package: root, Install: root, Data: root}
	c := Component{Name: "office", Runtime: "process", Argv: []string{executable, "-test.run=^TestNativeHTTPHelper$"},
		Env: map[string]string{"FLEET_TEST_HTTP_CHILD": "1", "PANTHEON_PORT_HTTP": "1"}, Ports: map[string]int{"http": 0}, StopSeconds: 3}
	driver := NativeDriver{}
	a, err := driver.Start(context.Background(), c, p, "native-a")
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := driver.Stop(context.Background(), c, a); err != nil {
			t.Error(err)
		}
	}()
	b, err := driver.Start(context.Background(), c, p, "native-b")
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := driver.Stop(context.Background(), c, b); err != nil {
			t.Error(err)
		}
	}()
	if a.Endpoints["http"] == b.Endpoints["http"] {
		t.Fatal("process instances share a port")
	}
	client := http.Client{Timeout: time.Second}
	for _, r := range []Resource{a, b} {
		deadline := time.Now().Add(5 * time.Second)
		ok := false
		for time.Now().Before(deadline) {
			response, err := client.Get(r.Endpoints["http"])
			if err == nil {
				body, _ := io.ReadAll(response.Body)
				response.Body.Close()
				if string(body) != "native-office-service" {
					t.Fatal("wrong process reached")
				}
				ok = true
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
		if !ok {
			log, _ := os.ReadFile(filepath.Join(root, "office.log"))
			t.Fatalf("native HTTP failed: %s", log)
		}
	}
}

func TestProcessPortsMustBeAssignedByRunner(t *testing.T) {
	d := definition()
	d.Components[0].Ports = map[string]int{"http": 0}
	if err := d.Validate(); err != nil {
		t.Fatal(err)
	}
	d.Components[0].Ports["http"] = 80
	if d.Validate() == nil {
		t.Fatal("process may claim an arbitrary fixed host port")
	}
}
