package reconciler

// Release sets are pinned by the SHA-256 of their archive. The controller
// downloads one once, verifies it, and keeps only what it delivers: the index
// and the prebuilt App artifacts. It never rebuilds a package, so the bytes a
// node verifies are exactly the bytes the release builder published.

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sync"

	"github.com/aristoteleo/pantheon-fleet/internal/lifecycle"
)

const (
	IndexName    = "release-set.json"
	MaxArchive   = 512 << 20
	maxIndex     = 64 << 10
	indexVersion = 2
)

var (
	platformRE = regexp.MustCompile(`^(linux|darwin|windows)-(amd64|arm64)$`)
	digestRE   = regexp.MustCompile(`^[a-f0-9]{64}$`)
	aliasRE    = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,79}$`)
	capRE      = regexp.MustCompile(`^[a-z][a-z0-9:_-]{0,31}$`)
)

// Variant is one App package compiled for one platform.
type Variant struct {
	Path     string   `json:"path"`
	AppID    string   `json:"app_id"`
	Version  string   `json:"version"`
	Revision string   `json:"revision"`
	Bytes    int64    `json:"bytes"`
	Artifact string   `json:"artifact"`
	Requires []string `json:"requires"`
	Prefer   []string `json:"prefer"`
}

// Index is release-set.json: package alias -> platform -> variant.
type Index struct {
	Protocol int                           `json:"protocol"`
	Apps     map[string]map[string]Variant `json:"apps"`
}

func (ix Index) validate() error {
	if ix.Protocol != indexVersion || len(ix.Apps) == 0 || len(ix.Apps) > 32 {
		return fmt.Errorf("release set index needs protocol %d and 1 to 32 Apps (rebuild the release set)", indexVersion)
	}
	for alias, variants := range ix.Apps {
		if !aliasRE.MatchString(alias) || len(variants) == 0 || len(variants) > 6 {
			return fmt.Errorf("invalid release set App %q", alias)
		}
		var identity [2]string
		for platform, v := range variants {
			if !platformRE.MatchString(platform) || !digestRE.MatchString(v.Revision) || v.Bytes < 1 || v.Bytes > lifecycle.MaxArtifact ||
				v.Artifact != "artifacts/"+v.Revision || !aliasRE.MatchString(v.AppID) || v.Version == "" {
				return fmt.Errorf("invalid release set variant %s/%s", alias, platform)
			}
			for _, c := range append(append([]string{}, v.Requires...), v.Prefer...) {
				if !capRE.MatchString(c) {
					return fmt.Errorf("invalid placement %q in %s/%s", c, alias, platform)
				}
			}
			if identity[0] != "" && identity != [2]string{v.AppID, v.Version} {
				return fmt.Errorf("platform variants of %s differ in App identity", alias)
			}
			identity = [2]string{v.AppID, v.Version}
		}
	}
	return nil
}

// Release is a verified, cached release set.
type Release struct {
	SHA256 string
	Index  Index
	dir    string
}

// Artifact returns the verified bytes of one variant.
func (r *Release) Artifact(v Variant) ([]byte, error) {
	raw, err := os.ReadFile(filepath.Join(r.dir, "artifacts", v.Revision))
	if err != nil {
		return nil, fmt.Errorf("release artifact %s unavailable", v.Revision[:12])
	}
	sum := sha256.Sum256(raw)
	if hex.EncodeToString(sum[:]) != v.Revision || int64(len(raw)) != v.Bytes {
		return nil, fmt.Errorf("release artifact %s changed on disk", v.Revision[:12])
	}
	return raw, nil
}

// Releases downloads and caches release sets under one private directory.
type Releases struct {
	root   string
	client *http.Client
	mu     sync.Mutex
	cache  map[string]*Release
	busy   map[string]*sync.Mutex
}

func NewReleases(root string, client *http.Client) (*Releases, error) {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, err
	}
	if client == nil {
		client = &http.Client{}
	}
	return &Releases{root: root, client: client, cache: map[string]*Release{}, busy: map[string]*sync.Mutex{}}, nil
}

// Get returns the release pinned by url and sha256, downloading it once.
func (rs *Releases) Get(ctx context.Context, url, sum string) (*Release, error) {
	if !digestRE.MatchString(sum) {
		return nil, fmt.Errorf("pin the release set by its SHA-256")
	}
	rs.mu.Lock()
	if r := rs.cache[sum]; r != nil {
		rs.mu.Unlock()
		return r, nil
	}
	lock := rs.busy[sum]
	if lock == nil {
		lock = &sync.Mutex{}
		rs.busy[sum] = lock
	}
	rs.mu.Unlock()
	lock.Lock()
	defer lock.Unlock()
	rs.mu.Lock()
	r := rs.cache[sum]
	rs.mu.Unlock()
	if r != nil {
		return r, nil
	}
	dir := filepath.Join(rs.root, sum)
	r, err := open(dir, sum)
	if err != nil {
		if err := rs.download(ctx, url, sum, dir); err != nil {
			return nil, err
		}
		if r, err = open(dir, sum); err != nil {
			return nil, err
		}
	}
	rs.mu.Lock()
	rs.cache[sum] = r
	rs.mu.Unlock()
	return r, nil
}

func open(dir, sum string) (*Release, error) {
	f, err := os.Open(filepath.Join(dir, IndexName))
	if err != nil {
		return nil, err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, maxIndex+1))
	if err != nil || len(raw) > maxIndex {
		return nil, fmt.Errorf("release set index too large")
	}
	var ix Index
	if err := json.Unmarshal(raw, &ix); err != nil {
		return nil, fmt.Errorf("invalid release set index")
	}
	if err := ix.validate(); err != nil {
		return nil, err
	}
	return &Release{SHA256: sum, Index: ix, dir: dir}, nil
}

func (rs *Releases) download(ctx context.Context, url, sum, dir string) error {
	tmp, err := os.MkdirTemp(rs.root, ".release-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmp)
	archive := filepath.Join(tmp, "release.tar.gz")
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return err
	}
	resp, err := rs.client.Do(req)
	if err != nil {
		return fmt.Errorf("release set download failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("release set download failed: HTTP %d", resp.StatusCode)
	}
	out, err := os.OpenFile(archive, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	hash := sha256.New()
	n, err := io.Copy(io.MultiWriter(out, hash), io.LimitReader(resp.Body, MaxArchive+1))
	if closeErr := out.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return fmt.Errorf("release set download failed: %w", err)
	}
	if n > MaxArchive {
		return fmt.Errorf("release set archive exceeds its limit")
	}
	if hex.EncodeToString(hash.Sum(nil)) != sum {
		return fmt.Errorf("release set archive does not match its pinned SHA-256")
	}
	staging := filepath.Join(tmp, "release")
	if err := extract(archive, staging); err != nil {
		return err
	}
	if _, err := open(staging, sum); err != nil {
		return err
	}
	_ = os.RemoveAll(dir)
	return os.Rename(staging, dir)
}

// extract keeps the index and artifacts/<sha256> files only; package source
// directories in the archive are for review and the Python tooling.
func extract(archive, dest string) error {
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("release set archive is not gzip")
	}
	if err := os.MkdirAll(filepath.Join(dest, "artifacts"), 0o700); err != nil {
		return err
	}
	tr := tar.NewReader(gz)
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("release set archive is corrupt")
		}
		name := path.Clean("/" + h.Name)[1:]
		var target string
		limit := int64(lifecycle.MaxArtifact)
		switch {
		case name == IndexName:
			target, limit = filepath.Join(dest, IndexName), maxIndex
		case path.Dir(name) == "artifacts" && digestRE.MatchString(path.Base(name)):
			target = filepath.Join(dest, "artifacts", path.Base(name))
		default:
			continue
		}
		if h.Typeflag != tar.TypeReg || h.Size > limit {
			return fmt.Errorf("release set entry %s is not a bounded regular file", name)
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if err != nil {
			return fmt.Errorf("release set archive repeats %s", name)
		}
		_, err = io.Copy(out, io.LimitReader(tr, limit))
		if closeErr := out.Close(); err == nil {
			err = closeErr
		}
		if err != nil {
			return err
		}
	}
}
