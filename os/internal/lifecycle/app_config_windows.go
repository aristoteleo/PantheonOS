//go:build windows

package lifecycle

import (
	"fmt"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

// Create with a protected inheritable user-only DACL before writing any data.
// Existing directories/files are checked; never repair a broad ACL silently.
func makeConfigDirectory(path string) error {
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return err
	}
	sd, err := windows.SecurityDescriptorFromString("O:" + user.User.Sid.String() + "D:P(A;OICI;FA;;;" + user.User.Sid.String() + ")")
	if err != nil {
		return err
	}
	p, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	sa := windows.SecurityAttributes{Length: uint32(unsafe.Sizeof(windows.SecurityAttributes{})), SecurityDescriptor: sd}
	err = windows.CreateDirectory(p, &sa)
	if err == windows.ERROR_ALREADY_EXISTS {
		return nil
	}
	return err
}

func checkConfigPrivate(path string, info os.FileInfo) error {
	invalid := fmt.Errorf("configuration ACL must be private to the Runner user")
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return invalid
	}
	sd, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.OWNER_SECURITY_INFORMATION|windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		return invalid
	}
	owner, _, err := sd.Owner()
	if err != nil || owner == nil || !owner.Equals(user.User.Sid) {
		return invalid
	}
	acl, _, err := sd.DACL()
	if err != nil || acl == nil || acl.AceCount != 1 {
		return invalid
	}
	var ace *windows.ACCESS_ALLOWED_ACE
	if windows.GetAce(acl, 0, &ace) != nil || ace == nil || ace.Header.AceType != windows.ACCESS_ALLOWED_ACE_TYPE || ace.Mask != (windows.STANDARD_RIGHTS_REQUIRED|windows.SYNCHRONIZE|0x1ff) {
		return invalid
	}
	sid := (*windows.SID)(unsafe.Pointer(&ace.SidStart))
	if !sid.Equals(user.User.Sid) {
		return invalid
	}
	if info.IsDir() {
		control, _, err := sd.Control()
		if err != nil || control&windows.SE_DACL_PROTECTED == 0 || ace.Header.AceFlags&(windows.OBJECT_INHERIT_ACE|windows.CONTAINER_INHERIT_ACE) != windows.OBJECT_INHERIT_ACE|windows.CONTAINER_INHERIT_ACE {
			return invalid
		}
	}
	return nil
}
