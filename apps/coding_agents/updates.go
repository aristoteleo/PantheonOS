package codingagentsapp

import "github.com/aristoteleo/pantheonos/os/codingagents"

// setUpdate puts an agent's update on its report: the command, the newest
// version and whether it is newer than the installed one.
func (i *AgentInfo) setUpdate(p codingagents.UpdatePlan) {
	if len(p.Argv) > 0 {
		i.Update = shellJoin(p.Argv)
	}
	i.Latest, i.UpdateNote = p.Latest, p.Note
	if p.Newer {
		i.UpdateTo = codingagents.VersionNumber(p.Latest)
	}
}

// updateCommand is the command line that updates a detected agent where it
// was installed; empty for an agent without one.
func updateCommand(f codingagents.Found) string {
	p := codingagents.PlanUpdate(f, codingagents.InstalledVersion(f.Path))
	if len(p.Argv) == 0 {
		return ""
	}
	return shellJoin(p.Argv)
}
