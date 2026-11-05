package hosted

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// Call serves the owner's browser (and any App of the owner's deployment):
// the caller is the space's person.
func (t *TeamChat) Call(ctx context.Context, c *Call) (any, error) {
	switch c.Method {
	case "space_get":
		if err := c.Decode(&struct{}{}); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) { return t.spaceGet(ctx, fl, tcOwner) })
	case "space_update":
		var a struct {
			Name     *string        `json:"name"`
			Settings map[string]int `json:"settings"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) { return t.spaceUpdate(ctx, fl, a.Name, a.Settings) })
	case "me_update":
		var a struct {
			Name   *string `json:"name"`
			Handle *string `json:"handle"`
			Avatar *string `json:"avatar"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) {
			return t.memberUpdate(ctx, fl, tcOwner, map[string]any{"name": a.Name, "handle": a.Handle, "avatar": a.Avatar})
		})
	case "conv_create":
		var a struct {
			Kind       string   `json:"kind"`
			Name       string   `json:"name"`
			Private    bool     `json:"private"`
			Members    []string `json:"members"`
			Topic      string   `json:"topic"`
			AgentReply string   `json:"agent_reply"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) {
			return t.convCreate(ctx, fl, tcOwner, a.Kind, a.Name, a.Private, a.Members, a.Topic, a.AgentReply)
		})
	case "conv_update":
		var a struct {
			CID        string    `json:"cid"`
			Name       *string   `json:"name"`
			Topic      *string   `json:"topic"`
			Private    *bool     `json:"private"`
			AgentReply *string   `json:"agent_reply"`
			Members    *[]string `json:"members"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) {
			return t.convUpdate(ctx, fl, a.CID, a.Name, a.Topic, a.Private, a.AgentReply, a.Members)
		})
	case "conv_join", "conv_leave":
		var a struct {
			CID string `json:"cid"`
			MID string `json:"mid"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		mid := a.MID
		if mid == "" {
			mid = tcOwner
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) {
			return t.convMembership(ctx, fl, tcOwner, a.CID, mid, c.Method == "conv_join")
		})
	case "conv_archive":
		var a struct {
			CID      string `json:"cid"`
			Archived *bool  `json:"archived"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		archived := a.Archived == nil || *a.Archived
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) { return t.convArchive(ctx, fl, a.CID, archived) })
	case "messages":
		var a struct {
			CID    string `json:"cid"`
			Before string `json:"before"`
			After  string `json:"after"`
			Root   string `json:"root"`
			Limit  int    `json:"limit"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) {
			return t.messages(ctx, fl, tcOwner, a.CID, a.Before, a.After, a.Root, a.Limit)
		})
	case "post":
		var a struct {
			CID    string   `json:"cid"`
			Text   string   `json:"text"`
			Root   string   `json:"root"`
			Files  []string `json:"files"`
			AsTask bool     `json:"as_task"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		if a.AsTask {
			return nil, &ArgumentError{"tasks come in Phase 2"}
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) {
			msg, err := t.post(ctx, fl, tcOwner, a.CID, a.Text, a.Root, a.Files)
			if err != nil {
				return nil, err
			}
			return map[string]any{"message": msg}, nil
		})
	case "edit", "delete":
		var a struct {
			ID   string `json:"id"`
			CID  string `json:"cid"`
			Text string `json:"text"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) {
			msg, err := t.change(ctx, fl, tcOwner, c.Method, a.ID, a.CID, a.Text)
			if err != nil {
				return nil, err
			}
			return map[string]any{"message": msg}, nil
		})
	case "read":
		var a struct {
			CID string `json:"cid"`
			ID  string `json:"id"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) { return t.markRead(ctx, fl, tcOwner, a.CID, a.ID) })
	case "follow":
		var a struct {
			Root string `json:"root"`
			CID  string `json:"cid"`
			On   *bool  `json:"on"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		on := a.On == nil || *a.On
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) { return t.follow(ctx, fl, tcOwner, a.Root, a.CID, on) })
	case "agent_create":
		var a struct {
			Spec agentSpec `json:"spec"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.agentCreate(ctx, c.Fleet, a.Spec)
	case "agent_update":
		var a map[string]any
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		mid, _ := a["mid"].(string)
		delete(a, "mid")
		return t.agentUpdate(ctx, c.Fleet, mid, a)
	case "agent_control":
		var a struct {
			MID     string `json:"mid"`
			Action  string `json:"action"`
			Confirm string `json:"confirm"`
			// delete: keep the agent's folder (the default) or remove it.
			KeepFolder *bool `json:"keep_folder"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		if a.Action == "delete" && a.KeepFolder != nil && !*a.KeepFolder {
			a.Action = "delete_folder"
		}
		return t.agentControl(ctx, c.Fleet, a.MID, a.Action, a.Confirm)
	case "agent_live":
		var a struct {
			MID string `json:"mid"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.withFleet(ctx, c.Fleet, func(fl *tcFleet) (any, error) { return t.agentLive(fl, a.MID) })
	case "file_put":
		var a filePutArgs
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.filePut(ctx, c.Fleet, tcOwner, a)
	case "file_get":
		var a struct {
			ID     string `json:"id"`
			Offset int64  `json:"offset"`
			Length int64  `json:"length"`
		}
		if err := c.Decode(&a); err != nil {
			return nil, err
		}
		return t.fileGet(ctx, c.Fleet, a.ID, a.Offset, a.Length)
	}
	return nil, &ArgumentError{fmt.Sprintf("unknown method %s", c.Method)}
}

// withFleet runs fn with the caller's space locked.
func (t *TeamChat) withFleet(ctx context.Context, fid string, fn func(fl *tcFleet) (any, error)) (any, error) {
	fl, err := t.lockFleet(ctx, fid)
	if err != nil {
		return nil, err
	}
	defer fl.mu.Unlock()
	out, err := fn(fl)
	if err != nil {
		return nil, err
	}
	// Encoded while the space is locked: replies share records with it.
	return frozen(out)
}

func frozen(v any) (json.RawMessage, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	return raw, nil
}

// ── space ──────────────────────────────────────────────────────────────────

func (t *TeamChat) spaceGet(ctx context.Context, fl *tcFleet, me string) (map[string]any, error) {
	members := []any{}
	status := map[string]any{}
	for _, mid := range sortedKeys(fl.members) {
		m := fl.members[mid]
		members = append(members, m)
		if m.agent() {
			status[mid] = t.statusOf(fl, m)
		}
	}
	convs := []any{}
	unread := map[string]any{}
	for _, cid := range sortedKeys(fl.convs) {
		c := fl.convs[cid]
		if !fl.visible(c, me) {
			continue
		}
		convs = append(convs, c)
		m, err := t.convMsgs(ctx, fl, cid)
		if err != nil {
			return nil, err
		}
		mark := fl.reads[me+"."+cid]
		count, mentioned := 0, 0
		var last string
		for _, id := range m.all {
			msg := m.byID[id]
			last = id
			if id <= mark || msg.From == me || msg.Deleted {
				continue
			}
			count++
			if contains(msg.Mentions, me) {
				mentioned++
			}
		}
		unread[cid] = map[string]any{"count": count, "mentions": mentioned, "last": last}
	}
	reads := map[string]string{}
	follows := []string{}
	for key, id := range fl.reads {
		if mid, cid, _ := strings.Cut(key, "."); mid == me {
			reads[cid] = id
		}
	}
	for key, on := range fl.follows {
		if mid, root, _ := strings.Cut(key, "."); mid == me && on {
			follows = append(follows, root)
		}
	}
	return map[string]any{"space": fl.space, "me": me, "members": members, "conversations": convs, "reads": reads,
		"follows": follows, "agent_status": status, "unread": unread, "usage": t.usage(fl), "revision": fl.seq,
		"subject": tcSubject(fl.fid)}, nil
}

// usage is each guard with its current use.
func (t *TeamChat) usage(fl *tcFleet) map[string]any {
	now := t.clock().Unix()
	agents, minutes := map[string]int{}, map[string]int{}
	day := 0
	for _, at := range fl.space.Retired {
		if now-at < 86400 {
			day++
		}
	}
	for mid, m := range fl.members {
		if !m.agent() {
			continue
		}
		hour := 0
		for _, at := range m.Turns {
			if now-at < 3600 {
				hour++
			}
			if now-at < 86400 {
				day++
			}
		}
		agents[mid] = hour
		minutes[mid] = turnMinutes(m, t.clock())
	}
	s := fl.space.Settings
	return map[string]any{"limits": s, "turns_last_hour": agents, "turn_minutes_last_hour": minutes, "turns_today": day}
}

func (t *TeamChat) spaceUpdate(ctx context.Context, fl *tcFleet, name *string, settings map[string]int) (any, error) {
	next := *fl.space
	if name != nil {
		n := strings.TrimSpace(*name)
		if n == "" || utf8.RuneCountInString(n) > 80 {
			return nil, &ArgumentError{"a space name has 1 to 80 characters"}
		}
		next.Name = n
	}
	for key, v := range settings {
		bounds := map[string][2]int{"turns_per_agent_hour": {1, 600}, "turns_per_space_day": {1, 10000},
			"hop_limit": {1, 50}, "turn_max_minutes": {1, 720}, "agent_messages_in_a_row": {2, 100},
			"posts_per_turn": {1, 200}, "turn_minutes_per_agent_hour": {1, 60}}[key]
		label := map[string]string{"turns_per_agent_hour": "Turns an hour for each agent", "turns_per_space_day": "Turns a day for the space",
			"hop_limit": "Hops between agents", "turn_max_minutes": "Minutes for one turn",
			"agent_messages_in_a_row": "Agent messages in a row", "posts_per_turn": "Messages in one turn",
			"turn_minutes_per_agent_hour": "Minutes of turns an hour for each agent"}[key]
		if bounds == [2]int{} {
			return nil, &ArgumentError{"this setting is not known"}
		}
		if v < bounds[0] || v > bounds[1] {
			return nil, &ArgumentError{fmt.Sprintf("%s is between %d and %d", label, bounds[0], bounds[1])}
		}
		switch key {
		case "turns_per_agent_hour":
			next.Settings.TurnsPerAgentHour = v
		case "turns_per_space_day":
			next.Settings.TurnsPerSpaceDay = v
		case "hop_limit":
			next.Settings.HopLimit = v
		case "turn_max_minutes":
			next.Settings.TurnMaxMinutes = v
		case "agent_messages_in_a_row":
			next.Settings.AgentRun = v
		case "posts_per_turn":
			next.Settings.PostsPerTurn = v
		case "turn_minutes_per_agent_hour":
			next.Settings.MinutesPerAgentHour = v
		}
	}
	if err := t.put(ctx, fl, "space", &next); err != nil {
		return nil, err
	}
	fl.space = &next
	t.emit(fl, map[string]any{"type": "space", "space": fl.space, "usage": t.usage(fl)})
	return map[string]any{"space": fl.space, "usage": t.usage(fl)}, nil
}

// handleFrom makes a handle of a name: lower case letters, digits, - and _.
func handleFrom(name string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(name) {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9', r == '_', r == '-':
			b.WriteRune(r)
		case r == ' ' || r == '.':
			b.WriteRune('-')
		}
		if b.Len() >= 32 {
			break
		}
	}
	return strings.Trim(b.String(), "-_")
}

func (t *TeamChat) handleFree(fl *tcFleet, handle, except string) error {
	if !tcHandleRE.MatchString(handle) {
		return &ArgumentError{"a handle has 1 to 32 lower case letters, digits, - and _, starting with a letter or digit"}
	}
	for _, m := range fl.members {
		if m.Handle == handle && m.MID != except {
			return &ArgumentError{"@" + handle + " is taken"}
		}
	}
	return nil
}

// memberUpdate changes a member's name, handle or avatar (the person's own,
// or an agent's through agent_update).
func (t *TeamChat) memberUpdate(ctx context.Context, fl *tcFleet, mid string, fields map[string]any) (any, error) {
	m := fl.members[mid]
	if m == nil {
		return nil, &ArgumentError{"no member " + mid}
	}
	next := *m
	if v, ok := fields["name"].(*string); ok && v != nil {
		n := strings.TrimSpace(*v)
		if n == "" || utf8.RuneCountInString(n) > 64 {
			return nil, &ArgumentError{"a name has 1 to 64 characters"}
		}
		next.Name = n
	}
	if v, ok := fields["handle"].(*string); ok && v != nil {
		h := strings.ToLower(strings.TrimPrefix(strings.TrimSpace(*v), "@"))
		if err := t.handleFree(fl, h, mid); err != nil {
			return nil, err
		}
		next.Handle = h
	}
	if v, ok := fields["avatar"].(*string); ok && v != nil {
		if len(*v) > 64<<10 {
			return nil, &ArgumentError{"an avatar is at most 64 KB"}
		}
		next.Avatar = *v
	}
	if err := t.put(ctx, fl, "member."+mid, &next); err != nil {
		return nil, err
	}
	fl.members[mid] = &next
	t.emit(fl, map[string]any{"type": "member", "member": &next})
	return map[string]any{"member": &next}, nil
}

// ── conversations ──────────────────────────────────────────────────────────

func channelName(name string) (string, error) {
	n := strings.ToLower(strings.TrimPrefix(strings.TrimSpace(name), "#"))
	n = strings.ReplaceAll(n, " ", "-")
	if !tcHandleRE.MatchString(n) {
		return "", &ArgumentError{"a channel name has 1 to 32 lower case letters, digits, - and _"}
	}
	return n, nil
}

func dmID(a, b string) string {
	if a > b {
		a, b = b, a
	}
	return "dm-" + a + "-" + b
}

func (t *TeamChat) convCreate(ctx context.Context, fl *tcFleet, by, kind, name string, private bool, members []string, topic, agentReply string) (any, error) {
	if len(fl.convs) >= tcMaxConvs {
		return nil, &ArgumentError{fmt.Sprintf("a space holds at most %d conversations; archive one first", tcMaxConvs)}
	}
	for _, mid := range members {
		if fl.members[mid] == nil {
			return nil, &ArgumentError{"no member " + mid}
		}
	}
	now := t.stamp()
	var c *tcConv
	switch kind {
	case "dm":
		other := ""
		for _, mid := range members {
			if mid != by {
				other = mid
			}
		}
		if other == "" || len(members) > 2 {
			return nil, &ArgumentError{"a DM is between you and one other member"}
		}
		cid := dmID(by, other)
		if existing := fl.convs[cid]; existing != nil {
			return map[string]any{"conversation": existing, "existed": true}, nil
		}
		c = &tcConv{CID: cid, Kind: "dm", Private: true, Members: []string{by, other}, AgentReply: "all", Created: now, CreatedBy: by}
	case "channel":
		n, err := channelName(name)
		if err != nil {
			return nil, err
		}
		for _, other := range fl.convs {
			if other.Kind == "channel" && other.Name == n {
				return nil, &ArgumentError{"#" + n + " exists"}
			}
		}
		if agentReply == "" {
			agentReply = "mention"
		}
		if agentReply != "mention" && agentReply != "all" {
			return nil, &ArgumentError{"agent_reply is mention or all"}
		}
		list := []string{by}
		for _, mid := range members {
			if !contains(list, mid) {
				list = append(list, mid)
			}
		}
		c = &tcConv{CID: "c" + shortID(), Kind: "channel", Name: n, Private: private, Topic: topic, Members: list,
			AgentReply: agentReply, Created: now, CreatedBy: by}
	default:
		return nil, &ArgumentError{"kind is channel or dm"}
	}
	if err := t.put(ctx, fl, "conv."+c.CID, c); err != nil {
		return nil, err
	}
	fl.convs[c.CID] = c
	t.emit(fl, map[string]any{"type": "conv", "conversation": c})
	return map[string]any{"conversation": c}, nil
}

func (t *TeamChat) conv(fl *tcFleet, cid string) (*tcConv, error) {
	c := fl.convs[cid]
	if c == nil {
		return nil, &ArgumentError{"no conversation " + cid}
	}
	return c, nil
}

func (t *TeamChat) saveConv(ctx context.Context, fl *tcFleet, next *tcConv) error {
	if err := t.put(ctx, fl, "conv."+next.CID, next); err != nil {
		return err
	}
	fl.convs[next.CID] = next
	t.emit(fl, map[string]any{"type": "conv", "conversation": next})
	return nil
}

func (t *TeamChat) convUpdate(ctx context.Context, fl *tcFleet, cid string, name, topic *string, private *bool, agentReply *string, members *[]string) (any, error) {
	c, err := t.conv(fl, cid)
	if err != nil {
		return nil, err
	}
	next := *c
	next.Members = append([]string(nil), c.Members...)
	if name != nil {
		if c.Kind != "channel" || c.CID == "general" {
			return nil, &ArgumentError{"this conversation cannot be renamed"}
		}
		n, err := channelName(*name)
		if err != nil {
			return nil, err
		}
		for _, other := range fl.convs {
			if other.CID != cid && other.Kind == "channel" && other.Name == n {
				return nil, &ArgumentError{"#" + n + " exists"}
			}
		}
		next.Name = n
	}
	if topic != nil {
		if len(*topic) > 1024 {
			return nil, &ArgumentError{"a topic is at most 1 KB"}
		}
		next.Topic = *topic
	}
	if private != nil {
		if c.Kind != "channel" || c.CID == "general" {
			return nil, &ArgumentError{"only channels other than #general can be made private or public"}
		}
		next.Private = *private
	}
	if agentReply != nil {
		if *agentReply != "mention" && *agentReply != "all" {
			return nil, &ArgumentError{"agent_reply is mention or all"}
		}
		next.AgentReply = *agentReply
	}
	if members != nil {
		if c.Kind == "dm" {
			return nil, &ArgumentError{"a DM's members do not change"}
		}
		list := []string{}
		for _, mid := range *members {
			if fl.members[mid] == nil {
				return nil, &ArgumentError{"no member " + mid}
			}
			if !contains(list, mid) {
				list = append(list, mid)
			}
		}
		if c.CID == "general" {
			for _, mid := range c.Members {
				if !contains(list, mid) {
					return nil, &ArgumentError{"#general holds every member"}
				}
			}
		}
		next.Members = list
	}
	if err := t.saveConv(ctx, fl, &next); err != nil {
		return nil, err
	}
	return map[string]any{"conversation": &next}, nil
}

func (t *TeamChat) convMembership(ctx context.Context, fl *tcFleet, by, cid, mid string, join bool) (any, error) {
	c, err := t.conv(fl, cid)
	if err != nil {
		return nil, err
	}
	if fl.members[mid] == nil {
		return nil, &ArgumentError{"no member " + mid}
	}
	if c.Kind == "dm" {
		return nil, &ArgumentError{"a DM's members do not change"}
	}
	if by != tcOwner && mid != by {
		return nil, &ArgumentError{"you can only join or leave yourself"}
	}
	if join && c.Private && by != tcOwner && !c.has(by) {
		return nil, &ArgumentError{"#" + c.Name + " is private"}
	}
	if !join && c.CID == "general" {
		return nil, &ArgumentError{"#general holds every member and cannot be left"}
	}
	if join == c.has(mid) {
		return map[string]any{"conversation": c}, nil
	}
	next := *c
	if join {
		next.Members = append(append([]string(nil), c.Members...), mid)
	} else {
		next.Members = nil
		for _, m := range c.Members {
			if m != mid {
				next.Members = append(next.Members, m)
			}
		}
	}
	if err := t.saveConv(ctx, fl, &next); err != nil {
		return nil, err
	}
	return map[string]any{"conversation": &next}, nil
}

func (t *TeamChat) convArchive(ctx context.Context, fl *tcFleet, cid string, archived bool) (any, error) {
	c, err := t.conv(fl, cid)
	if err != nil {
		return nil, err
	}
	if c.CID == "general" {
		return nil, &ArgumentError{"#general cannot be archived"}
	}
	next := *c
	next.Archived = archived
	if err := t.saveConv(ctx, fl, &next); err != nil {
		return nil, err
	}
	return map[string]any{"conversation": &next}, nil
}

// ── messages ───────────────────────────────────────────────────────────────

func (t *TeamChat) messages(ctx context.Context, fl *tcFleet, me, cid, before, after, root string, limit int) (any, error) {
	c, err := t.conv(fl, cid)
	if err != nil {
		return nil, err
	}
	if !fl.visible(c, me) {
		return nil, &ArgumentError{"you are not in this conversation"}
	}
	m, err := t.convMsgs(ctx, fl, cid)
	if err != nil {
		return nil, err
	}
	if root != "" && m.byID[root] == nil {
		return nil, &ArgumentError{"no message " + root + " in this conversation"}
	}
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	page, more := m.page(root, before, after, limit)
	return map[string]any{"messages": page, "more": more, "cid": cid, "root": root}, nil
}

// post adds a message from mid (the person, an agent, or "system"), wakes the
// agents it is for, and announces it; fl.mu is held.
func (t *TeamChat) post(ctx context.Context, fl *tcFleet, from, cid, text, root string, fileIDs []string) (*tcMsg, error) {
	return t.postEvent(ctx, fl, &tcEvent{From: from, CID: cid, Text: text, Root: root, Kind: "post"}, fileIDs)
}

func (t *TeamChat) postEvent(ctx context.Context, fl *tcFleet, e *tcEvent, fileIDs []string) (*tcMsg, error) {
	c, err := t.conv(fl, e.CID)
	if err != nil {
		return nil, err
	}
	if c.Archived {
		return nil, &ArgumentError{"this conversation is archived"}
	}
	sender := fl.members[e.From]
	if e.Kind == "post" {
		if sender == nil {
			return nil, &ArgumentError{"no member " + e.From}
		}
		if e.From != tcOwner && !c.has(e.From) {
			return nil, &ArgumentError{"you are not a member of " + c.label(fl, e.From) + " (join it first)"}
		}
	}
	m, err := t.convMsgs(ctx, fl, e.CID)
	if err != nil {
		return nil, err
	}
	if e.Root != "" {
		root := m.byID[e.Root]
		if root == nil {
			return nil, &ArgumentError{"no message " + e.Root + " in this conversation"}
		}
		if root.Root != "" {
			e.Root = root.Root
		}
	}
	e.Text = strings.TrimRight(e.Text, " \n\t")
	if !utf8.ValidString(e.Text) {
		return nil, &ArgumentError{"a message is UTF-8 text"}
	}
	if len(e.Text) > tcMaxText {
		return nil, &ArgumentError{fmt.Sprintf("a message is at most %d KB of text; attach longer text as a file", tcMaxText>>10)}
	}
	for _, id := range fileIDs {
		f := fl.files[id]
		if f == nil {
			return nil, &ArgumentError{"no file " + id}
		}
		e.Files = append(e.Files, f.tcFileRef)
	}
	if strings.TrimSpace(e.Text) == "" && len(e.Files) == 0 {
		return nil, &ArgumentError{"write a message"}
	}
	// Agents may post only so many messages in a row in a conversation;
	// then they wait for a person.
	runLimit, paused := fl.space.Settings.AgentRun, false
	if e.Kind == "post" && sender != nil && sender.agent() {
		run := fl.agentRun(m, runLimit)
		if run >= runLimit {
			if !fl.runNoted(m) {
				_, _ = t.system(ctx, fl, e.CID, "", "", "", runLine(runLimit))
			}
			return nil, &ArgumentError{fmt.Sprintf("Not posted: agents are paused in %s after %d messages in a row without a person. "+
				"End your turn; a person's message there lets agents continue.", c.label(fl, e.From), runLimit)}
		}
		paused = run+1 >= runLimit
	}
	e.ID, e.At = tcNewID(t.clock()), t.stamp()
	if e.Kind == "post" {
		e.Mentions = mentions(fl, e.Text)
	}
	wakes, joined, stopped := t.wakesFor(fl, c, m, e)
	if paused {
		wakes, stopped = nil, false
	}
	for _, w := range wakes {
		e.Woke = append(e.Woke, w.agent)
	}
	msg, err := t.append(ctx, fl, e)
	if err != nil {
		return nil, err
	}
	if len(joined) > 0 {
		next := *c
		next.Members = append(append([]string(nil), c.Members...), joined...)
		if err := t.saveConv(ctx, fl, &next); err != nil {
			return nil, err
		}
	}
	t.emit(fl, map[string]any{"type": "message", "message": msg})
	if sender != nil {
		// Writing is reading.
		_ = t.setRead(ctx, fl, e.From, e.CID, e.ID)
	}
	for _, w := range wakes {
		t.enqueue(ctx, fl, w.agent, w.wake)
	}
	if paused && !fl.runNoted(m) {
		_, _ = t.system(ctx, fl, e.CID, "", "", "", runLine(runLimit))
	}
	if stopped && e.From != "system" {
		limit := fl.space.Settings.HopLimit
		_, _ = t.system(ctx, fl, e.CID, e.Root, "", "", fmt.Sprintf("Agents stopped answering each other here after %d hops without a person's message. "+
			"Write a message to let them continue.", limit))
	}
	return msg, nil
}

// system posts a plain-language line from the service.
func (t *TeamChat) system(ctx context.Context, fl *tcFleet, cid, root, about, action, text string) (*tcMsg, error) {
	if fl.convs[cid] == nil {
		cid = "general"
	}
	return t.postEvent(ctx, fl, &tcEvent{From: "system", CID: cid, Root: root, Kind: "system", Text: text, About: about, Action: action}, nil)
}

// change edits or deletes one of mid's own messages.
func (t *TeamChat) change(ctx context.Context, fl *tcFleet, mid, kind, id, cid, text string) (*tcMsg, error) {
	msg, err := t.findMsg(ctx, fl, id, cid)
	if err != nil {
		return nil, err
	}
	if msg.From != mid || msg.Kind != "post" {
		return nil, &ArgumentError{"you can only " + kind + " your own messages"}
	}
	if msg.Deleted {
		return nil, &ArgumentError{"this message is deleted"}
	}
	e := &tcEvent{ID: tcNewID(t.clock()), CID: msg.CID, Root: msg.Root, From: mid, Kind: kind, Ref: msg.ID, At: t.stamp()}
	if kind == "edit" {
		text = strings.TrimRight(text, " \n\t")
		if strings.TrimSpace(text) == "" && len(msg.Files) == 0 {
			return nil, &ArgumentError{"write a message (or delete it)"}
		}
		if len(text) > tcMaxText || !utf8.ValidString(text) {
			return nil, &ArgumentError{fmt.Sprintf("a message is at most %d KB of UTF-8 text", tcMaxText>>10)}
		}
		e.Text, e.Mentions = text, mentions(fl, text)
	}
	woke := append([]string(nil), msg.woke...)
	folded, err := t.append(ctx, fl, e)
	if err != nil {
		return nil, err
	}
	t.emit(fl, map[string]any{"type": "message", "message": folded, "change": kind})
	// Agents the message woke get an edit in their next turn; a delete
	// wakes no one (they see it when they next look).
	if kind != "edit" {
		woke = nil
	}
	for _, agent := range woke {
		if agent == mid || fl.members[agent] == nil {
			continue
		}
		t.enqueue(ctx, fl, agent, tcWake{ID: msg.ID, Event: e.ID, CID: msg.CID, Root: msg.Root, Kind: kind,
			Reason: kind, From: mid, At: e.At})
	}
	return folded, nil
}

func (t *TeamChat) setRead(ctx context.Context, fl *tcFleet, mid, cid, id string) error {
	key := mid + "." + cid
	if fl.reads[key] >= id {
		return nil
	}
	if err := t.put(ctx, fl, "read."+key, id); err != nil {
		return err
	}
	fl.reads[key] = id
	if mid == tcOwner {
		t.emit(fl, map[string]any{"type": "read", "mid": mid, "cid": cid, "id": id})
	}
	return nil
}

func (t *TeamChat) markRead(ctx context.Context, fl *tcFleet, me, cid, id string) (any, error) {
	c, err := t.conv(fl, cid)
	if err != nil {
		return nil, err
	}
	if !fl.visible(c, me) {
		return nil, &ArgumentError{"you are not in this conversation"}
	}
	if !tcIDRE.MatchString(id) {
		return nil, &ArgumentError{"no message " + id}
	}
	if err := t.setRead(ctx, fl, me, cid, id); err != nil {
		return nil, err
	}
	return map[string]any{"cid": cid, "id": fl.reads[me+"."+cid]}, nil
}

func (t *TeamChat) follow(ctx context.Context, fl *tcFleet, me, root, cid string, on bool) (any, error) {
	msg, err := t.findMsg(ctx, fl, root, cid)
	if err != nil {
		return nil, err
	}
	if msg.Root != "" {
		root = msg.Root
	}
	if err := t.put(ctx, fl, "follow."+me+"."+root, on); err != nil {
		return nil, err
	}
	fl.follows[me+"."+root] = on
	return map[string]any{"root": root, "on": on}, nil
}

// fmtTime is how agents see a message's time.
func fmtTime(at string) string {
	ts, err := time.Parse(time.RFC3339Nano, at)
	if err != nil {
		return at
	}
	return ts.UTC().Format("Jan 2 15:04 UTC")
}
