package lifecycle

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

// Runs only on an explicitly selected ephemeral Linux Docker runner. Uses the
// shipped package generator, real NativeDriver, resource admission and mounts.
func TestManagedSpeechDockerLifecycle(t *testing.T) {
	if os.Getenv("FLEET_TEST_SPEECH_DOCKER") != "1" {
		t.Skip("opt-in Linux Docker acceptance")
	}
	if runtime.GOOS != "linux" || runtime.GOARCH != "amd64" || os.Geteuid() == 0 {
		t.Fatal("requires a non-root Linux amd64 Docker user")
	}
	fixture, err := filepath.Abs("../../../tests/fixtures/prepare_speech_container.py")
	if err != nil {
		t.Fatal(err)
	}
	var audio []byte
	for _, modelID := range []string{"kokoro-82m-v1", "whisper-tiny-en"} {
		t.Run(modelID, func(t *testing.T) {
			root := t.TempDir()
			driver := NativeDriver{Engine: &ContainerEngine{Root: filepath.Join(root, "dependencies/docker")}}
			inventory := node.DetectResources()
			m, err := Open(root, "speech-acceptance", "linux-docker", proto.Capability{OS: "linux", Arch: "amd64", Caps: []string{"proc"}, Resources: &inventory}, driver)
			if err != nil {
				t.Fatal(err)
			}
			defer m.Close()
			m.SetResourceSampler(node.DetectResources)
			directory := filepath.Join(root, "fixture")
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
			output, err := exec.CommandContext(ctx, "python3", fixture, modelID, filepath.Join(root, "cache/model-service"), directory).CombinedOutput()
			cancel()
			if err != nil {
				t.Fatalf("prepare: %v %s", err, output)
			}
			var pinned struct {
				Model       string `json:"model"`
				SHA256      string `json:"sha256"`
				MemoryBytes uint64 `json:"memory_bytes"`
			}
			if err = json.Unmarshal(bytes.TrimSpace(output), &pinned); err != nil {
				t.Fatalf("model pin: %v %s", err, output)
			}
			files := map[string]string{}
			entries, err := os.ReadDir(directory)
			if err != nil {
				t.Fatal(err)
			}
			for _, entry := range entries {
				b, e := os.ReadFile(filepath.Join(directory, entry.Name()))
				if e != nil {
					t.Fatal(e)
				}
				files[entry.Name()] = string(b)
			}
			var def Definition
			if err = StrictDecode([]byte(files["fleet.json"]), &def); err != nil {
				t.Fatal(err)
			}
			if err = def.Validate(); err != nil {
				t.Fatal(err)
			}
			payload, digest := bundle(t, def, files)
			if _, err = stageModelArtifact(m, digest, payload); err != nil {
				t.Fatal(err)
			}
			id := m.instanceID(digest, "speech-test")
			t.Cleanup(func() {
				if in := m.Snapshot().Instances[id]; in != nil {
					for _, r := range in.Resources {
						ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
						_ = driver.Stop(ctx, def.Components[0], r)
						_ = driver.Release(ctx, r)
						cancel()
					}
				}
			})
			action := func(kind string) {
				t.Helper()
				var gen uint64
				if in := m.Snapshot().Instances[id]; in != nil {
					gen = in.Generation
				}
				request := Request{Protocol: 1, OperationID: fmt.Sprintf("%s-%d", kind, time.Now().UnixNano()), Action: kind, Digest: digest, Scope: "speech-test", Generation: gen}
				if _, e := m.Submit(request); e != nil {
					t.Fatal(e)
				}
				end := time.Now().Add(5 * time.Minute)
				for time.Now().Before(end) {
					op := m.Snapshot().Operations[request.OperationID]
					if op.State == "succeeded" {
						return
					}
					if op.State != "queued" && op.State != "running" {
						t.Fatalf("%s: %s", kind, op.Error)
					}
					time.Sleep(200 * time.Millisecond)
				}
				t.Fatal("lifecycle observation deadline exceeded")
			}
			action("install")
			for iteration := 0; iteration < 2; iteration++ {
				started := time.Now()
				action("start")
				in := m.Snapshot().Instances[id]
				if len(in.Resources) != 1 || len(in.Reservations) != 1 {
					t.Fatal("owned resource/reservation missing")
				}
				resource := in.Resources[0]
				origin, e := m.Service(id, digest, in.Generation, "backend", "http")
				if e != nil {
					t.Fatal(e)
				}
				client := http.Client{Timeout: 90 * time.Second}
				response, e := client.Get(origin + "/fleet/model-state")
				if e != nil {
					t.Fatal(e)
				}
				var state map[string]any
				err = json.NewDecoder(response.Body).Decode(&state)
				response.Body.Close()
				if err != nil || state["loaded"] != true || state["model"] != pinned.Model || state["sha256"] != pinned.SHA256 {
					t.Fatal("model not actually loaded", state, err)
				}
				t.Logf("%s start %d ready in %s", modelID, iteration, time.Since(started))
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				inspection, e := driver.docker(ctx, "inspect", resource.ID)
				cancel()
				if e != nil {
					t.Fatalf("inspect: %v", e)
				}
				var inspected []struct {
					Config     struct{ User string }
					HostConfig struct {
						Memory         uint64
						DeviceRequests []any
					}
					Mounts []struct {
						Destination string
						RW          bool
					}
				}
				if e = json.Unmarshal(inspection, &inspected); e != nil || len(inspected) != 1 {
					t.Fatal("inspection", e)
				}
				got := inspected[0]
				if got.Config.User != fmt.Sprintf("%d:%d", os.Geteuid(), os.Getegid()) || got.HostConfig.Memory != pinned.MemoryBytes || len(got.HostConfig.DeviceRequests) != 0 {
					t.Fatal("container owner or resource limits differ", got)
				}
				readonly := 0
				for _, mount := range got.Mounts {
					if mount.Destination == "/fleet/package" || mount.Destination == "/fleet/weights" {
						if mount.RW {
							t.Fatal("writable immutable files")
						}
						readonly++
					}
				}
				if readonly != 2 {
					t.Fatal("missing read-only mounts")
				}
				if modelID == "kokoro-82m-v1" {
					body, _ := json.Marshal(map[string]any{"model": pinned.Model, "input": "The quick brown fox jumps over the lazy dog.", "voice": "af_heart", "response_format": "wav"})
					response, e = client.Post(origin+"/v1/audio/speech", "application/json", bytes.NewReader(body))
				} else {
					var body bytes.Buffer
					writer := multipart.NewWriter(&body)
					if e = writer.WriteField("model", pinned.Model); e != nil {
						t.Fatal(e)
					}
					file, e := writer.CreateFormFile("file", "sample.wav")
					if e != nil {
						t.Fatal(e)
					}
					if _, e = file.Write(audio); e != nil {
						t.Fatal(e)
					}
					writer.Close()
					response, e = client.Post(origin+"/v1/audio/transcriptions", writer.FormDataContentType(), &body)
				}
				if e != nil {
					t.Fatal(e)
				}
				result, e := io.ReadAll(io.LimitReader(response.Body, 4<<20))
				response.Body.Close()
				if e != nil || response.StatusCode != 200 {
					t.Fatalf("inference: status %d %v %s", response.StatusCode, e, result)
				}
				if modelID == "kokoro-82m-v1" {
					if len(result) < 1000 || string(result[:4]) != "RIFF" {
						t.Fatal("invalid generated WAV")
					}
					audio = result
				} else {
					if !strings.Contains(strings.ToLower(string(result)), "fox") {
						t.Fatal("transcription did not match generated audio", string(result))
					}
				}
				action("stop")
				in = m.Snapshot().Instances[id]
				if in.State != "stopped" || len(in.Reservations) != 0 || len(in.Resources) != 0 {
					t.Fatal("stop retained owned resources", in.State)
				}
				if response, e = client.Get(origin + "/health"); e == nil {
					response.Body.Close()
					t.Fatal("stopped endpoint still accessible")
				}
				// Preparation rechecks the same cache on the second start; it must remain
				// intact after the container and memory reservation have been released.
			}
		})
	}
}
