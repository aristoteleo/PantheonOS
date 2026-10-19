package lifecycle

// Match the real SDK's bounded upload for shipped connectors as they grow.
// Stage itself retains its strict per-message limit and idempotent offsets.
func stageModelArtifact(m *Manager, digest string, payload []byte) (int64, error) {
	var received int64
	for offset := 0; offset < len(payload); {
		end := min(offset+MaxChunk, len(payload))
		var err error
		received, err = m.Stage(digest, int64(offset), payload[offset:end])
		if err != nil {
			return received, err
		}
		offset = end
	}
	return received, nil
}
