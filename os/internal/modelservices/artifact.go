package modelservices

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/lifecycle"
)

// File is one file of a package tree.
type File struct {
	Data       []byte
	Executable bool
}

// Tree is a package as relative POSIX path -> file.
type Tree map[string]File

const maxUnpacked = 128 << 20

func skipped(path string) bool {
	for _, part := range strings.Split(path, "/") {
		if part == ".git" || part == "__pycache__" || part == "node_modules" || part == ".venv" || strings.HasPrefix(part, ".env") {
			return true
		}
	}
	return false
}

// pathLess orders paths component by component, as Python sorts Path objects.
func pathLess(a, b string) bool {
	pa, pb := strings.Split(a, "/"), strings.Split(b, "/")
	for i := 0; i < len(pa) && i < len(pb); i++ {
		if pa[i] != pb[i] {
			return pa[i] < pb[i]
		}
	}
	return len(pa) < len(pb)
}

// BuildArtifact packages a tree exactly as the Python build_artifact did
// (pantheonos/appkit/lifecycle.py): the platform's fleet.<platform>.json becomes
// fleet.json when the manifest names platform variants, files are sorted,
// owner-only modes, no timestamps; gzip only above the artifact bound.
func BuildArtifact(tree Tree, platform string) ([]byte, string, error) {
	if _, ok := tree["fleet.json"]; !ok {
		return nil, "", fmt.Errorf("this App has no fleet.json execution declaration")
	}
	manifestFile, ok := tree["app.json"]
	if !ok {
		return nil, "", fmt.Errorf("App manifest is missing")
	}
	var manifest struct {
		ID        string `json:"id"`
		Version   string `json:"version"`
		Execution struct {
			PlatformManifests map[string]string `json:"platform_manifests"`
		} `json:"execution"`
	}
	if err := json.Unmarshal(manifestFile.Data, &manifest); err != nil {
		return nil, "", err
	}
	definition := tree["fleet.json"].Data
	if variants := manifest.Execution.PlatformManifests; len(variants) > 0 {
		name := variants[platform]
		f, ok := tree[name]
		if name == "" || !ok {
			return nil, "", fmt.Errorf("this App has no native package for node platform %q", platform)
		}
		definition = f.Data
	}
	var identity struct {
		Protocol int    `json:"protocol"`
		AppID    string `json:"app_id"`
		Version  string `json:"version"`
	}
	if err := json.Unmarshal(definition, &identity); err != nil || identity.Protocol != 1 || identity.AppID != manifest.ID || identity.Version != manifest.Version {
		return nil, "", fmt.Errorf("execution declaration must match the App identity and version")
	}
	paths := make([]string, 0, len(tree))
	for path := range tree {
		if !skipped(path) {
			paths = append(paths, path)
		}
	}
	sort.Slice(paths, func(i, j int) bool { return pathLess(paths[i], paths[j]) })
	var out bytes.Buffer
	for _, path := range paths {
		f := tree[path]
		data := f.Data
		if path == "fleet.json" {
			data = definition
		}
		if out.Len()+len(data)+10240 > maxUnpacked {
			return nil, "", fmt.Errorf("App code package exceeds 128 MiB unpacked")
		}
		mode := int64(0o400)
		if f.Executable {
			mode = 0o500
		}
		header, err := ustarHeader(path, mode, int64(len(data)))
		if err != nil {
			return nil, "", err
		}
		out.Write(header)
		out.Write(data)
		if rest := len(data) % 512; rest != 0 {
			out.Write(make([]byte, 512-rest))
		}
	}
	out.Write(make([]byte, 1024)) // end-of-archive blocks
	// Python pads the archive to whole 20-block records.
	if rest := out.Len() % 10240; rest != 0 {
		out.Write(make([]byte, 10240-rest))
	}
	payload := out.Bytes()
	if len(payload) > lifecycle.MaxArtifact {
		var compressed bytes.Buffer
		gz, _ := gzip.NewWriterLevel(&compressed, gzip.BestCompression)
		gz.ModTime = time.Time{}
		if _, err := gz.Write(payload); err != nil {
			return nil, "", err
		}
		if err := gz.Close(); err != nil {
			return nil, "", err
		}
		if compressed.Len() > lifecycle.MaxArtifact {
			return nil, "", fmt.Errorf("compressed App code package exceeds 32 MiB")
		}
		payload = compressed.Bytes()
	}
	sum := sha256.Sum256(payload)
	return payload, hex.EncodeToString(sum[:]), nil
}

// ustarHeader is the header Python's tarfile writes for a regular file with
// default ownership and time (devmajor/devminor left empty).
func ustarHeader(name string, mode, size int64) ([]byte, error) {
	if len(name) > 100 || strings.ContainsFunc(name, func(r rune) bool { return r > 0x7e || r < 0x20 }) {
		return nil, fmt.Errorf("package path %q needs a PAX header", name)
	}
	h := make([]byte, 512)
	copy(h[0:100], name)
	octal := func(field []byte, v int64) { copy(field, fmt.Sprintf("%0*o\x00", len(field)-1, v)) }
	octal(h[100:108], mode)
	octal(h[108:116], 0)
	octal(h[116:124], 0)
	octal(h[124:136], size)
	octal(h[136:148], 0)
	copy(h[148:156], "        ")
	h[156] = '0'
	copy(h[257:265], "ustar\x0000")
	sum := 0
	for _, c := range h {
		sum += int(c)
	}
	copy(h[148:156], fmt.Sprintf("%06o\x00 ", sum))
	return h, nil
}

// ReadTree reads a package tree from a (possibly gzip) tar archive.
func ReadTree(archive []byte) (Tree, error) {
	var reader *tar.Reader
	if len(archive) > 2 && archive[0] == 0x1f && archive[1] == 0x8b {
		gz, err := gzip.NewReader(bytes.NewReader(archive))
		if err != nil {
			return nil, err
		}
		reader = tar.NewReader(gz)
	} else {
		reader = tar.NewReader(bytes.NewReader(archive))
	}
	tree := Tree{}
	total := 0
	for {
		h, err := reader.Next()
		if err != nil {
			if err.Error() == "EOF" {
				return tree, nil
			}
			return nil, err
		}
		if h.Typeflag == tar.TypeDir {
			continue
		}
		if h.Typeflag != tar.TypeReg || strings.HasPrefix(h.Name, "/") || strings.Contains(h.Name, "..") {
			return nil, fmt.Errorf("package archive contains an unsupported entry %q", h.Name)
		}
		total += int(h.Size)
		if total > maxUnpacked {
			return nil, fmt.Errorf("package archive is too large")
		}
		data := make([]byte, h.Size)
		if _, err := io.ReadFull(reader, data); err != nil {
			return nil, err
		}
		tree[strings.TrimPrefix(h.Name, "./")] = File{Data: data, Executable: h.Mode&0o111 != 0}
	}
}
