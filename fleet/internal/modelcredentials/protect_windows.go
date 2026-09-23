//go:build windows

package modelcredentials

import (
	"golang.org/x/sys/windows"
	"os"
	"unsafe"
)

const protection = "windows-dpapi-user"

// Windows file mode bits do not describe ACLs. DPAPI binds the entire record
// (including its endpoint) to this user; no machine-wide or plaintext fallback.
func private(info os.FileInfo) bool { return true }
func crypt(value []byte, encrypt bool) ([]byte, error) {
	if len(value) == 0 || len(value) > maxRecord {
		return nil, ErrCredential
	}
	in := windows.DataBlob{Size: uint32(len(value)), Data: &value[0]}
	var out windows.DataBlob
	var err error
	if encrypt {
		err = windows.CryptProtectData(&in, nil, nil, 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out)
	} else {
		err = windows.CryptUnprotectData(&in, nil, nil, 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out)
	}
	if err != nil {
		return nil, ErrCredential
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	if out.Data == nil || out.Size > maxRecord {
		return nil, ErrCredential
	}
	buffer := unsafe.Slice(out.Data, int(out.Size))
	defer clear(buffer)
	return append([]byte(nil), buffer...), nil
}
func protect(value []byte) ([]byte, error)   { return crypt(value, true) }
func unprotect(value []byte) ([]byte, error) { return crypt(value, false) }
