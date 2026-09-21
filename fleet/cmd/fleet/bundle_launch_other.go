//go:build !darwin

package main

func appLaunchBootstrap() (bool, int, error) { return false, 0, nil }
func finishAppLaunch(int)                    {}
