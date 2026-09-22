package apptransport

import "regexp"

// Binding identifies one published port of one immutable App generation.
type Binding struct {
	Fleet      string `json:"fleet_id"`
	Node       string `json:"node_id"`
	Instance   string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation uint64 `json:"generation"`
	Component  string `json:"component"`
	Port       string `json:"port"`
}

var bindingIdent = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,100}$`)
var bindingDigest = regexp.MustCompile(`^[a-f0-9]{64}$`)

func (b Binding) Valid() bool {
	return bindingIdent.MatchString(b.Fleet) && bindingIdent.MatchString(b.Node) && bindingIdent.MatchString(b.Instance) && bindingDigest.MatchString(b.Revision) && b.Generation > 0 && bindingIdent.MatchString(b.Component) && bindingIdent.MatchString(b.Port)
}
