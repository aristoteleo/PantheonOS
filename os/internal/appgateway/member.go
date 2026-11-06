package appgateway

import (
	"net/http"
	"regexp"
)

// Member grants connect a person's browser to another account's Team Chat
// space (docs/apps/team-chat.md, "Phase 3 as built"): the owner's hosted
// `team-chat` App, with the person's identity, which the service checks on
// every call. The Hub sends them; nothing of the owner's travels with them.

// MemberGrant is a person's space grant. Space is the owner's Fleet (the
// binding's); FleetID is the person's own; Role is admin or member; Joined is
// when they joined (unix seconds).
type MemberGrant struct {
	GrantID  string `json:"grant_id"`
	Space    string `json:"space"`
	UserID   string `json:"user_id"`
	Username string `json:"username"`
	Name     string `json:"name"`
	FleetID  string `json:"fleet_id"`
	Role     string `json:"role"`
	Joined   int64  `json:"joined"`
	// Membership: the Hub's id of this membership (new each time they join).
	Membership string `json:"membership,omitempty"`
}

// MemberCheck reports whether a binding is one a member grant may reach (an
// owner's hosted team-chat App).
type MemberCheck func(Binding) bool

// SetMemberCheck enables member grants. Called once, before serving.
func (g *Gateway) SetMemberCheck(check MemberCheck) { g.memberCheck = check }

var memberIdentRE = regexp.MustCompile(`^[A-Za-z0-9_.@:-]{1,128}$`)

// validMember checks a member attach request; the binding must be a hosted
// team-chat App of the Fleet the grant names.
func (g *Gateway) validMember(q *AttachRequest) bool {
	m := q.Member
	return g.memberCheck != nil && !q.Workload && !q.URLAuth && q.Visitor == nil &&
		shareIDRE.MatchString(m.GrantID) && m.Space == q.Fleet && memberIdentRE.MatchString(m.FleetID) &&
		memberIdentRE.MatchString(m.UserID) && len(m.Username) <= 200 && len(m.Name) <= 200 && len(m.Membership) <= 200 &&
		(m.Role == "admin" || m.Role == "member") && m.Joined > 0 &&
		g.hostedNode != "" && q.Node == g.hostedNode && g.memberCheck(q.Binding)
}

// allowMember: a member reaches the App's RPC and the connection check, nothing else.
func allowMember(w http.ResponseWriter, r *http.Request) bool {
	if r.Method == http.MethodPost && r.URL.Path == "/rpc" || r.Method == http.MethodGet && r.URL.Path == "/__fleet/status" {
		return true
	}
	http.Error(w, "not available in a shared space", 403)
	return false
}
