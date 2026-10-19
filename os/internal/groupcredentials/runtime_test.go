package groupcredentials

import (
	"crypto/tls"
	"os"
	"path/filepath"
	"testing"
)

func TestRuntimeBundleRequiresInstalledIdentityAndCleansOnlyItsGeneration(t *testing.T) {
	s, b, m, key, ca, caPEM := fixture(t)
	root := filepath.Join(t.TempDir(), "runtime")
	if _, err := s.Materialize(root, b, m); err == nil {
		t.Fatal("unenrolled launch")
	}
	e, err := s.Enroll(b, m)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Materialize(root, b, m); err == nil {
		t.Fatal("unsigned launch")
	}
	cert := sign(t, e, key, ca, nil)
	if _, err = s.Install(b, m, cert, caPEM); err != nil {
		t.Fatal(err)
	}
	path, err := s.Materialize(root, b, m)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = tls.LoadX509KeyPair(filepath.Join(path, "certificate.pem"), filepath.Join(path, "key.pem")); err != nil {
		t.Fatal("unusable TLS files", err)
	}
	if again, err := s.Materialize(root, b, m); err != nil || again != path {
		t.Fatal("retry changed runtime", err)
	}
	for _, name := range []string{"ca.pem", "certificate.pem"} {
		body, err := os.ReadFile(filepath.Join(path, name))
		if err != nil {
			t.Fatal(err)
		}
		expected := caPEM
		if name == "certificate.pem" {
			expected = cert
		}
		if string(body) != expected {
			t.Fatal("changed certificate")
		}
	}
	other := b
	other.Generation++
	otherPath, err := RuntimePath(root, other)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Mkdir(otherPath, 0700); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(otherPath, "keep"), []byte("other generation"), 0600); err != nil {
		t.Fatal(err)
	}
	// Preparation ID is gone from a running ledger; cleanup still uses exact generation.
	b.Preparation = ""
	if err = RemoveRuntime(root, b); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("runtime survived", err)
	}
	if _, err = os.Stat(filepath.Join(otherPath, "keep")); err != nil {
		t.Fatal("removed another generation", err)
	}
	if err = RemoveRuntime(root, b); err != nil {
		t.Fatal("non-idempotent cleanup", err)
	}
	b.Preparation = "prepare-original"
	if again, err := s.Enroll(b, m); err != nil || !again.Installed || again.CSR != e.CSR {
		t.Fatal("cleanup destroyed durable identity", err)
	}
}

func TestRuntimeBundleDoesNotRepairOrFollowChangedFiles(t *testing.T) {
	for _, kind := range []string{"partial", "changed", "symlink", "extra", "writable", "directory-link"} {
		t.Run(kind, func(t *testing.T) {
			s, b, m, key, ca, caPEM := fixture(t)
			root := filepath.Join(t.TempDir(), "runtime")
			e, err := s.Enroll(b, m)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = s.Install(b, m, sign(t, e, key, ca, nil), caPEM); err != nil {
				t.Fatal(err)
			}
			path, err := s.Materialize(root, b, m)
			if err != nil {
				t.Fatal(err)
			}
			if err = os.Chmod(path, 0700); err != nil {
				t.Fatal(err)
			}
			defer func() { _ = os.Chmod(path, 0700) }()
			keyPath := filepath.Join(path, "key.pem")
			switch kind {
			case "partial":
				err = os.Remove(keyPath)
			case "changed":
				if err = os.Chmod(keyPath, 0600); err == nil {
					err = os.WriteFile(keyPath, []byte("replaced key"), 0400)
				}
				if err == nil {
					err = os.Chmod(keyPath, 0400)
				}
			case "symlink":
				if err = os.Remove(keyPath); err == nil {
					err = os.Symlink("certificate.pem", keyPath)
				}
			case "extra":
				err = os.WriteFile(filepath.Join(path, "unexpected"), []byte("x"), 0400)
			case "writable":
				err = os.Chmod(keyPath, 0600)
			case "directory-link":
				if err = os.Rename(path, path+"-original"); err == nil {
					err = os.Symlink(path+"-original", path)
				}
			}
			if err != nil {
				t.Fatal(err)
			}
			if kind != "directory-link" {
				if err = os.Chmod(path, 0500); err != nil {
					t.Fatal(err)
				}
			}
			if _, err = s.Materialize(root, b, m); err == nil {
				t.Fatal("repaired/accepted corrupted bundle")
			}
			if kind == "directory-link" {
				if err = RemoveRuntime(root, b); err == nil {
					t.Fatal("followed cleanup symlink")
				}
				if _, err = os.Stat(filepath.Join(path+"-original", "key.pem")); err != nil {
					t.Fatal(err)
				}
			} else if err = RemoveRuntime(root, b); err != nil {
				t.Fatal("cancel could not clean partial bundle", err)
			}
		})
	}
}
