//go:build !darwin

package hpcconn

func systemKeychain() keychain { return noKeychain{} }
