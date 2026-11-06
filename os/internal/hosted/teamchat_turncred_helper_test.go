package hosted

import "context"

// asTurn is an agent request made by the agent's turn, as the node's
// pantheon-team script makes it: with the running turn's id and credential.
// When no turn runs (a test of what a command does, not of when), the
// request runs in a short turn of its own that works for the agent's owner.
// A request that names its turn (req.Turn) is sent as it is.
func (t *TeamChat) asTurn(ctx context.Context, fid string, req agentRequest) agentReply {
	if req.Turn != "" {
		return t.AgentRequest(ctx, fid, req)
	}
	fl, err := t.lockFleet(ctx, fid)
	if err != nil {
		return t.AgentRequest(ctx, fid, req)
	}
	var made *tcTurn
	if tr := fl.turns[req.MID]; tr != nil {
		req.Turn, req.Cred = tr.ID, tr.Cred
	} else if m := fl.members[req.MID]; m != nil && m.agent() {
		made = &tcTurn{ID: "t-test", Cred: newTurnCred(), WorksFor: ownerOf(m), State: "running", NodeID: m.NodeID}
		fl.turns[req.MID] = made
		req.Turn, req.Cred = made.ID, made.Cred
	}
	fl.mu.Unlock()
	out := t.AgentRequest(ctx, fid, req)
	if made != nil {
		if fl, err := t.lockFleet(ctx, fid); err == nil {
			if fl.turns[req.MID] == made {
				delete(fl.turns, req.MID)
			}
			fl.mu.Unlock()
			t.kick(fid, req.MID)
		}
	}
	return out
}
