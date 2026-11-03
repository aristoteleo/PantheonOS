package codingagents

import "sync"

// An agent that reports nothing of its own (Gemini, Cursor, a custom
// command, an older release whose hooks did not load) still shows what it
// does on its screen. The PTY service measures that screen for the terminal
// launches it runs and tells the Coding Agents service, in process, through
// this registry: the launch's command line, handed to pty_open as it is,
// names its terminal session.

// Activity is what a terminal launch's screen says.
type Activity struct {
	Terminal string
	// Working: the screen keeps changing (output, a spinner); false once it
	// has settled.
	Working bool
	// Notice is a desktop notification the program asked its terminal for
	// (OSC 9 or OSC 777), e.g. Codex's "Approval requested: …"; when set,
	// Working says nothing.
	Notice string
	// State is what the agent itself told its terminal (OSC 7501): working,
	// ready or waiting, Detail what it waits for. It outranks the screen.
	State  string
	Detail string
	// Closed: the terminal itself is gone (its window closed, its shell
	// exited).
	Closed bool
}

var activity struct {
	sync.Mutex
	commands map[string]string // command line -> terminal session
	sink     func(Activity)
}

// WatchCommand makes the screen of a PTY that runs command report as the
// terminal session's activity.
func WatchCommand(command, terminal string, sink func(Activity)) {
	activity.Lock()
	defer activity.Unlock()
	if activity.commands == nil {
		activity.commands = map[string]string{}
	}
	activity.commands[command] = terminal
	activity.sink = sink
}

// ForgetTerminal stops matching the command lines of a terminal session that
// ended.
func ForgetTerminal(terminal string) {
	activity.Lock()
	defer activity.Unlock()
	for cmd, t := range activity.commands {
		if t == terminal {
			delete(activity.commands, cmd)
		}
	}
}

// CommandTerminal is the terminal session a PTY's command line was made
// for, or "" when it is none (a plain shell, a task).
func CommandTerminal(command string) string {
	if command == "" {
		return ""
	}
	activity.Lock()
	defer activity.Unlock()
	return activity.commands[command]
}

// ReportActivity hands what a terminal launch's screen says to the Coding
// Agents service.
func ReportActivity(a Activity) {
	activity.Lock()
	sink := activity.sink
	activity.Unlock()
	if sink != nil && a.Terminal != "" {
		sink(a)
	}
}
