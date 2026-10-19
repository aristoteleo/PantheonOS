// The first-party Apps' Go implementations (runtime: builtin). One module
// so the fleet runner (a separate module) compiles them in via a replace;
// the bus protocol they speak lives in the runner's module (os/appsvc).
module github.com/aristoteleo/pantheon-apps

go 1.26.4

require (
	github.com/aristoteleo/pantheonos/os v0.0.0
	github.com/creack/pty v1.1.24
	github.com/nats-io/nats.go v1.52.0
)

require (
	github.com/klauspost/compress v1.20.0 // indirect
	github.com/nats-io/nkeys v0.4.16 // indirect
	github.com/nats-io/nuid v1.0.1 // indirect
	golang.org/x/crypto v0.57.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
)

replace github.com/aristoteleo/pantheonos/os => ../os
