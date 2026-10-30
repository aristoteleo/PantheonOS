package codingagentsapp

import (
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"image"
	"image/color"
	_ "image/gif" // decoders for thumbnails
	"image/jpeg"
	_ "image/png"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// Reference images: pictures a canvas turn hands to the agent. They are
// uploaded in small chunks (the bus jams on large messages) into the
// workspace's .pantheon/references folder, where the agent can read them.

const (
	referenceMax = 20 << 20
	thumbMax     = 40 << 10
	thumbSide    = 256
	// An image decodes to width × height × its bytes per pixel; larger
	// ones are refused at upload and never decoded.
	maxPixels    = 40_000_000
	decodeBudget = 160 << 20
	staleUpload  = time.Hour
)

var (
	imageTypes = map[string]string{".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
		".webp": "image/webp", ".gif": "image/gif"}
	uploadID   = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
	unsafeName = regexp.MustCompile(`[^A-Za-z0-9._-]+`)
)

// referencesDir is cwd's references folder (made if missing and create),
// refusing one that a link leads out of the workspace. Each level is
// checked before anything is made inside it.
func referencesDir(cwd string, create bool) (string, error) {
	cwd = filepath.Clean(expandHome(cwd))
	if !filepath.IsAbs(cwd) {
		return "", fmt.Errorf("give the workspace as an absolute path")
	}
	realCwd, err := filepath.EvalSymlinks(cwd)
	if st, serr := os.Stat(realCwd); err != nil || serr != nil || !st.IsDir() {
		return "", fmt.Errorf("%s is not a folder on this node", cwd)
	}
	dir := cwd
	for _, level := range []string{".pantheon", "references"} {
		dir = filepath.Join(dir, level)
		if _, err := os.Lstat(dir); errors.Is(err, os.ErrNotExist) && create {
			if err := os.Mkdir(dir, 0o755); err != nil && !errors.Is(err, os.ErrExist) {
				return "", err
			}
		}
		real, err := filepath.EvalSymlinks(dir)
		if err != nil {
			return "", err
		}
		if !within(realCwd, real) {
			return "", fmt.Errorf("%s leads outside the workspace", dir)
		}
		if st, err := os.Stat(real); err != nil || !st.IsDir() {
			return "", fmt.Errorf("%s is not a folder", dir)
		}
	}
	return dir, nil
}

// referenceName makes a file name safe: no folders, no leading dots, only
// plain characters, and an image extension.
func referenceName(name string) (string, error) {
	name = filepath.Base(strings.ReplaceAll(name, `\`, "/"))
	ext := strings.ToLower(filepath.Ext(name))
	if imageTypes[ext] == "" {
		return "", fmt.Errorf("only PNG, JPEG, WebP and GIF images can be references")
	}
	stem := strings.Trim(unsafeName.ReplaceAllString(strings.TrimSuffix(name, filepath.Ext(name)), "-"), ".-")
	if len(stem) > 100 {
		stem = stem[:100]
	}
	if stem == "" {
		stem = "image"
	}
	return stem + ext, nil
}

// ReferenceChunk is one piece of an upload.
type ReferenceChunk struct {
	Cwd      string
	UploadID string
	Name     string
	Offset   int64
	Data     string // base64
	Final    bool
}

// An upload in progress: the client's upload id names it, but its part
// file has a name of our own, so nothing the client says leads the write
// anywhere else.
type pendingUpload struct {
	part string
	name string
	at   time.Time
}

var uploads = struct {
	sync.Mutex
	m map[string]*pendingUpload // references dir + "\x00" + upload id
}{m: map[string]*pendingUpload{}}

// referencePut appends a chunk to an upload; the final chunk checks the
// content is an image of the named type and size and gives it its name.
func referencePut(c ReferenceChunk) map[string]any {
	if !uploadID.MatchString(c.UploadID) {
		return failure("upload_id: letters, digits, - and _ only")
	}
	name, err := referenceName(c.Name)
	if err != nil {
		return failure("%v", err)
	}
	dir, err := referencesDir(c.Cwd, true)
	if err != nil {
		return failure("%v", err)
	}
	data, err := base64.StdEncoding.DecodeString(c.Data)
	if err != nil {
		return failure("data is not base64: %v", err)
	}
	uploads.Lock()
	defer uploads.Unlock()
	sweepUploads(dir)
	key := dir + "\x00" + c.UploadID
	up := uploads.m[key]
	drop := func() {
		_ = os.Remove(up.part)
		delete(uploads.m, key)
	}
	var f *os.File
	if c.Offset == 0 {
		if up != nil {
			drop()
		}
		up = &pendingUpload{part: filepath.Join(dir, ".upload-"+newID()+".part"), name: name}
		uploads.m[key] = up
		if f, err = os.OpenFile(up.part, os.O_CREATE|os.O_EXCL|os.O_WRONLY|noFollow, 0o644); err != nil {
			delete(uploads.m, key)
			return failure("%v", err)
		}
	} else {
		if up == nil {
			return failure("No upload %s under way; start again from offset 0", c.UploadID)
		}
		st, err := os.Lstat(up.part)
		if err != nil || !st.Mode().IsRegular() {
			drop()
			return failure("The upload's partial file is gone; start again from offset 0")
		}
		if c.Offset != st.Size() {
			return failure("expected the chunk at offset %d", st.Size())
		}
		if f, err = os.OpenFile(up.part, os.O_WRONLY|os.O_APPEND|noFollow, 0); err != nil {
			drop()
			return failure("%v", err)
		}
	}
	if c.Offset+int64(len(data)) > referenceMax {
		_ = f.Close()
		drop()
		return failure("%s is larger than 20 MB", up.name)
	}
	_, werr := f.Write(data)
	if cerr := f.Close(); werr == nil {
		werr = cerr
	}
	if werr != nil {
		drop()
		return failure("%v", werr)
	}
	up.at = time.Now()
	if !c.Final {
		return map[string]any{"success": true, "offset": c.Offset + int64(len(data))}
	}
	defer drop()
	if err := sniffImage(up.part, up.name); err != nil {
		return failure("%v", err)
	}
	if err := checkImageSize(up.part, up.name); err != nil {
		return failure("%s: %v", up.name, err)
	}
	dest := freeName(dir, up.name)
	if err := os.Rename(up.part, dest); err != nil {
		return failure("%v", err)
	}
	st, _ := os.Stat(dest)
	return map[string]any{"success": true, "path": dest, "name": filepath.Base(dest), "size": st.Size()}
}

// sweepUploads forgets uploads left for an hour, and removes their part
// files, also ones an earlier run of the service left behind; uploads is
// locked.
func sweepUploads(dir string) {
	cutoff := time.Now().Add(-staleUpload)
	for key, up := range uploads.m {
		if up.at.Before(cutoff) {
			_ = os.Remove(up.part)
			delete(uploads.m, key)
		}
	}
	parts, _ := filepath.Glob(filepath.Join(dir, ".upload-*.part"))
	for _, p := range parts {
		if st, err := os.Lstat(p); err == nil && st.ModTime().Before(cutoff) {
			_ = os.Remove(p)
		}
	}
}

// checkImageSize refuses an image too large to decode for a preview.
// WebP, which the standard library cannot read, is left alone.
func checkImageSize(path, name string) error {
	if strings.EqualFold(filepath.Ext(name), ".webp") {
		return nil
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	cfg, _, err := image.DecodeConfig(f)
	if err != nil {
		return fmt.Errorf("not a readable image: %v", err)
	}
	return fitsBudget(cfg)
}

// fitsBudget: the image would decode within maxPixels and decodeBudget.
func fitsBudget(cfg image.Config) error {
	px := int64(cfg.Width) * int64(cfg.Height)
	if cfg.Width <= 0 || cfg.Height <= 0 || px > maxPixels || px*bytesPerPixel(cfg.ColorModel) > decodeBudget {
		return fmt.Errorf("the image is %d×%d pixels, too large", cfg.Width, cfg.Height)
	}
	return nil
}

func bytesPerPixel(m color.Model) int64 {
	switch m {
	case color.GrayModel, color.AlphaModel:
		return 1
	case color.Gray16Model, color.Alpha16Model, color.YCbCrModel:
		return 2
	case color.RGBA64Model, color.NRGBA64Model:
		return 8
	}
	if _, ok := m.(color.Palette); ok {
		return 1
	}
	return 4
}

// sniffImage checks the file's content is the image type its name says.
func sniffImage(path, name string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	head := make([]byte, 512)
	n, _ := io.ReadFull(f, head)
	got := http.DetectContentType(head[:n])
	want := imageTypes[strings.ToLower(filepath.Ext(name))]
	if got != want {
		return fmt.Errorf("%s is not a %s image", name, strings.TrimPrefix(want, "image/"))
	}
	return nil
}

// freeName is name in dir, numbered if a file already has it.
func freeName(dir, name string) string {
	ext := filepath.Ext(name)
	stem := strings.TrimSuffix(name, ext)
	p := filepath.Join(dir, name)
	for i := 2; ; i++ {
		if _, err := os.Lstat(p); errors.Is(err, os.ErrNotExist) {
			return p
		}
		p = filepath.Join(dir, fmt.Sprintf("%s-%d%s", stem, i, ext))
	}
}

// Reference is one uploaded image.
type Reference struct {
	Name     string  `json:"name"`
	Path     string  `json:"path"`
	Size     int64   `json:"size"`
	Modified float64 `json:"modified"`
}

// referenceList is the workspace's reference images, newest first.
func referenceList(cwd string) map[string]any {
	images := []Reference{}
	dir, err := referencesDir(cwd, false)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return map[string]any{"success": true, "images": images}
		}
		return failure("%v", err)
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if !e.Type().IsRegular() || strings.HasPrefix(e.Name(), ".") || imageTypes[strings.ToLower(filepath.Ext(e.Name()))] == "" {
			continue
		}
		if info, err := e.Info(); err == nil {
			images = append(images, Reference{Name: e.Name(), Path: filepath.Join(dir, e.Name()), Size: info.Size(),
				Modified: float64(info.ModTime().UnixNano()) / 1e9})
		}
	}
	sort.Slice(images, func(i, j int) bool { return images[i].Modified > images[j].Modified })
	return map[string]any{"success": true, "images": images}
}

// referenceThumb is a small JPEG of a reference image: a file directly in
// the references folder of the workspace cwd.
func referenceThumb(cwd, path string) map[string]any {
	path = filepath.Clean(path)
	dir, err := referencesDir(cwd, false)
	if err != nil {
		return failure("%s is not a reference image", path)
	}
	realDir, err1 := filepath.EvalSymlinks(dir)
	real, err2 := filepath.EvalSymlinks(path)
	if !filepath.IsAbs(path) || err1 != nil || err2 != nil || filepath.Dir(real) != realDir ||
		imageTypes[strings.ToLower(filepath.Ext(real))] == "" {
		return failure("%s is not a reference image", path)
	}
	st, err := os.Stat(real)
	if err != nil || !st.Mode().IsRegular() || st.Size() > referenceMax {
		return failure("%s is not a reference image", path)
	}
	b, err := os.ReadFile(real)
	if err != nil {
		return failure("%v", err)
	}
	mime := imageTypes[strings.ToLower(filepath.Ext(real))]
	thumb, err := thumbnail(b)
	if err != nil {
		// What the standard library cannot decode (WebP) is sent as it is
		// when small enough.
		if mime == "image/webp" && len(b) <= thumbMax {
			return map[string]any{"success": true, "mime": mime, "data": base64.StdEncoding.EncodeToString(b)}
		}
		return failure("No preview for %s: %v", filepath.Base(path), err)
	}
	return map[string]any{"success": true, "mime": "image/jpeg", "data": base64.StdEncoding.EncodeToString(thumb)}
}

// decoding lets one image decode at a time, bounding the memory previews take.
var decoding sync.Mutex

// thumbnail decodes an image (a GIF's first frame), fits it in a
// thumbSide square over white, and encodes it as JPEG within thumbMax.
func thumbnail(b []byte) ([]byte, error) {
	cfg, _, err := image.DecodeConfig(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	if err := fitsBudget(cfg); err != nil {
		return nil, err
	}
	decoding.Lock()
	defer decoding.Unlock()
	src, _, err := image.Decode(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	dst := shrink(src, thumbSide)
	for _, q := range []int{80, 65, 50, 35} {
		var out bytes.Buffer
		if err := jpeg.Encode(&out, dst, &jpeg.Options{Quality: q}); err != nil {
			return nil, err
		}
		if out.Len() <= thumbMax {
			return out.Bytes(), nil
		}
	}
	return nil, fmt.Errorf("the preview stays over 40 KB")
}

// shrink scales src to fit side×side (never up), averaging a few samples
// of the source under each pixel.
func shrink(src image.Image, side int) *image.RGBA {
	sb := src.Bounds()
	w, h := sb.Dx(), sb.Dy()
	scale := max(float64(w)/float64(side), float64(h)/float64(side), 1)
	dw, dh := max(int(float64(w)/scale), 1), max(int(float64(h)/scale), 1)
	dst := image.NewRGBA(image.Rect(0, 0, dw, dh))
	k := max(min(int(scale+0.5), 4), 1)
	for y := 0; y < dh; y++ {
		for x := 0; x < dw; x++ {
			var r, g, bl, a uint32
			for j := 0; j < k; j++ {
				for i := 0; i < k; i++ {
					sx := sb.Min.X + int((float64(x)+(float64(i)+0.5)/float64(k))*scale)
					sy := sb.Min.Y + int((float64(y)+(float64(j)+0.5)/float64(k))*scale)
					cr, cg, cb, ca := src.At(min(sx, sb.Max.X-1), min(sy, sb.Max.Y-1)).RGBA()
					r, g, bl, a = r+cr, g+cg, bl+cb, a+ca
				}
			}
			n := uint32(k * k)
			r, g, bl, a = r/n, g/n, bl/n, a/n
			// Premultiplied colour over white.
			white := 0xffff - a
			dst.Set(x, y, color.RGBA64{R: uint16(r + white), G: uint16(g + white), B: uint16(bl + white), A: 0xffff})
		}
	}
	return dst
}
