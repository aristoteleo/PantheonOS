package hpcconn

// keychain keeps a cluster password on this machine only (never a second factor).
type keychain interface {
	available() bool
	has(Cluster) bool
	load(Cluster) (string, bool)
	save(Cluster, string)
	forget(Cluster)
}

const keychainService = "PantheonOS Fleet HPC"

func account(c Cluster) string { return c.User + "@" + c.Host }

type noKeychain struct{}

func (noKeychain) available() bool             { return false }
func (noKeychain) has(Cluster) bool            { return false }
func (noKeychain) load(Cluster) (string, bool) { return "", false }
func (noKeychain) save(Cluster, string)        {}
func (noKeychain) forget(Cluster)              {}
