package hosted

// team-chat: people and agents in channels, direct messages and threads, kept
// by the controller so no browser and no Workspace has to be up for an agent
// to be woken and to answer (docs/apps/team-chat.md). The service is the only
// client of its KV bucket, message stream and object store; browsers reach it
// as a hosted App, agents through their node's Coding Agents service.

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// Storage: one bucket, one stream and one object store for every Fleet.
const (
	TeamChatBucket = "TEAMCHAT"
	TeamChatStream = "TEAMCHAT_MSG"
	TeamChatFiles  = "TEAMCHAT_FILES"
)

// Limits of a space.
const (
	tcMaxText       = 32 << 10
	tcMaxConvs      = 200
	tcMaxAgents     = 50
	tcMaxFile       = 25 << 20
	tcMaxSpaceFiles = 2 << 30
	tcSinceMax      = 30
	tcRetention     = 365 * 24 * time.Hour
	tcChunk         = 256 << 10 // a file_get piece for browsers
	tcNodeChunk     = 48 << 10  // a file piece to or from a node
)

// The person of a Phase 1 space: a Fleet belongs to one Hub user, and every
// caller the service admits acts for that owner (see desktopOwner).
const tcOwner = "owner"

var (
	tcHandleRE  = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,31}$`)
	tcIDRE      = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
	tcMentionRE = regexp.MustCompile(`(^|[^A-Za-z0-9_@])@([A-Za-z0-9][A-Za-z0-9_-]{0,31})`)
)

// TeamNode is what the service needs to know of a node. Seen is its last
// heartbeat (in the node's own clock; zero when not known).
type TeamNode struct {
	ID, Name, Kind, OS string
	Online             bool
	Seen               time.Time
}

// TeamNodes reaches the owner's nodes: their state, and their Coding Agents
// service (the controller's Fleet service in production).
type TeamNodes interface {
	Node(ctx context.Context, fleet, nodeID string) (TeamNode, error)
	Call(ctx context.Context, fleet, nodeID, method string, args map[string]any, timeout time.Duration) (map[string]any, error)
}

// FleetNodes is TeamNodes over the controller's Fleet service.
func FleetNodes(f *Fleet) TeamNodes { return fleetNodes{f} }

type fleetNodes struct{ f *Fleet }

func (n fleetNodes) Node(ctx context.Context, fleet, nodeID string) (TeamNode, error) {
	inv, err := n.f.inventory(ctx, fleet)
	if err != nil {
		return TeamNode{}, err
	}
	rec := node(inv, nodeID)
	if rec == nil {
		// A node's record lives as long as its heartbeat (registry TTL) and is
		// removed when it stops: a missing record is an offline node.
		return TeamNode{ID: nodeID}, nil
	}
	seen, _ := time.Parse(time.RFC3339Nano, str(rec["last_seen"]))
	return TeamNode{ID: nodeID, Name: str(rec["name"]), Kind: str(rec["kind"]), OS: str(rec["os"]), Online: online(rec), Seen: seen}, nil
}

func (n fleetNodes) Call(ctx context.Context, fleet, nodeID, method string, args map[string]any, timeout time.Duration) (map[string]any, error) {
	return n.f.CallNodeService(ctx, fleet, nodeID, "coding_agent", method, args, timeout)
}

// TeamChat is the team-chat hosted service.
type TeamChat struct {
	// Connect opens the service's own bus connection (on first use).
	Connect func() (*nats.Conn, error)
	// Nodes reaches the owner's nodes.
	Nodes TeamNodes
	// Backups is where daily snapshots go ("" takes none); Dir holds file
	// uploads in progress and files read for browsers.
	Backups, Dir string
	// Key signs each agent's identity, which its node presents with the
	// agent's requests.
	Key []byte

	mu     sync.Mutex
	infoMu sync.Mutex // the stream handle caches its info
	nc     *nats.Conn
	js     jetstream.JetStream
	kv     jetstream.KeyValue
	stream jetstream.Stream
	obj    jetstream.ObjectStore
	sub    *nats.Subscription
	closed bool

	fleetsMu sync.Mutex
	fleets   map[string]*tcFleet

	// now is the clock (tests replace it); timing knobs too.
	now      func() time.Time
	poll     time.Duration // between reads of a running turn's events
	tick     time.Duration // between looks at waiting agents
	backoffs []time.Duration
	uploads  sync.Map // upload id -> *tcUpload
}

func (t *TeamChat) clock() time.Time {
	if t.now != nil {
		return t.now()
	}
	return time.Now()
}

func (t *TeamChat) pollEvery() time.Duration {
	if t.poll > 0 {
		return t.poll
	}
	return 2 * time.Second
}

// open connects and opens (creating once) the bucket, stream and object store.
func (t *TeamChat) open(ctx context.Context) (jetstream.KeyValue, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.kv != nil {
		return t.kv, nil
	}
	if t.nc == nil {
		if t.Connect == nil {
			return nil, fmt.Errorf("team chat is not connected")
		}
		nc, err := t.Connect()
		if err != nil {
			return nil, fmt.Errorf("team chat bus: %w", err)
		}
		t.nc = nc
	}
	js, err := jetstream.New(t.nc)
	if err != nil {
		return nil, err
	}
	kv, err := js.CreateOrUpdateKeyValue(ctx, jetstream.KeyValueConfig{Bucket: TeamChatBucket,
		Description: "Team Chat spaces, members, conversations, queues (docs/apps/team-chat.md)", History: 1})
	if err != nil {
		return nil, fmt.Errorf("team chat bucket: %w", err)
	}
	stream, err := js.CreateOrUpdateStream(ctx, jetstream.StreamConfig{Name: TeamChatStream,
		Description: "Team Chat messages (docs/apps/team-chat.md)", Subjects: []string{"tc.>"},
		MaxAge: tcRetention, MaxMsgSize: 64 << 10, Storage: jetstream.FileStorage, Discard: jetstream.DiscardOld})
	if err != nil {
		return nil, fmt.Errorf("team chat stream: %w", err)
	}
	obj, err := js.CreateOrUpdateObjectStore(ctx, jetstream.ObjectStoreConfig{Bucket: TeamChatFiles,
		Description: "Team Chat files (docs/apps/team-chat.md)", Storage: jetstream.FileStorage})
	if err != nil {
		return nil, fmt.Errorf("team chat files: %w", err)
	}
	t.js, t.kv, t.stream, t.obj = js, kv, stream, obj
	if t.sub == nil {
		sub, err := t.nc.Subscribe("fleet.*.apps.team-chat.rpc", t.serveAgent)
		if err != nil {
			return nil, fmt.Errorf("team chat requests: %w", err)
		}
		t.sub = sub
	}
	return kv, nil
}

// Close stops answering agents and following turns (the bus connection is
// the caller's).
func (t *TeamChat) Close() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.closed = true
	if t.sub != nil {
		_ = t.sub.Unsubscribe()
	}
}

func (t *TeamChat) isClosed() bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.closed
}

// ── identities ─────────────────────────────────────────────────────────────

// agentToken is the identity a node presents with an agent's requests: it
// is given to the node with each of the agent's turns.
func (t *TeamChat) agentToken(fid, mid string) string {
	mac := hmac.New(sha256.New, t.Key)
	mac.Write([]byte("team-chat\n" + fid + "\n" + mid))
	return hex.EncodeToString(mac.Sum(nil))[:40]
}

func (t *TeamChat) checkToken(fid, mid, token string) bool {
	return len(t.Key) > 0 && hmac.Equal([]byte(t.agentToken(fid, mid)), []byte(token))
}

// ── ids ────────────────────────────────────────────────────────────────────

const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

var (
	idMu   sync.Mutex
	idLast [16]byte
)

// tcNewID is a ULID: 48 bits of milliseconds and 80 random bits, in Crockford
// base32; ids made by this process are strictly increasing.
func tcNewID(now time.Time) string {
	idMu.Lock()
	defer idMu.Unlock()
	var b [16]byte
	ms := uint64(now.UnixMilli())
	binary.BigEndian.PutUint16(b[0:2], uint16(ms>>32))
	binary.BigEndian.PutUint32(b[2:6], uint32(ms))
	_, _ = rand.Read(b[6:])
	if string(b[:6]) == string(idLast[:6]) || string(b[:]) <= string(idLast[:]) {
		// Same millisecond (or a clock step back): one more than the last.
		b = idLast
		for i := 15; i >= 0; i-- {
			b[i]++
			if b[i] != 0 {
				break
			}
		}
	}
	idLast = b
	// 128 bits -> 26 characters.
	hi, lo := binary.BigEndian.Uint64(b[:8]), binary.BigEndian.Uint64(b[8:])
	out := make([]byte, 26)
	for i := 25; i >= 0; i-- {
		out[i] = crockford[lo&31]
		lo = lo>>5 | hi<<59
		hi >>= 5
	}
	return string(out)
}

func shortID() string {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// ── records ────────────────────────────────────────────────────────────────

type tcSettings struct {
	TurnsPerAgentHour int `json:"turns_per_agent_hour"`
	TurnsPerSpaceDay  int `json:"turns_per_space_day"`
	HopLimit          int `json:"hop_limit"`
	TurnMaxMinutes    int `json:"turn_max_minutes"`
}

func defaultSettings() tcSettings {
	return tcSettings{TurnsPerAgentHour: 60, TurnsPerSpaceDay: 400, HopLimit: 8, TurnMaxMinutes: 120}
}

type tcSpace struct {
	ID       string     `json:"id"`
	Name     string     `json:"name"`
	Created  string     `json:"created"`
	Settings tcSettings `json:"settings"`
}

type tcAttention struct {
	Reason string `json:"reason"`
	Fix    string `json:"fix,omitempty"`
	At     string `json:"at"`
}

// tcMember is a person or an agent.
type tcMember struct {
	MID         string `json:"mid"`
	Kind        string `json:"kind"` // person | agent
	Handle      string `json:"handle"`
	Name        string `json:"name"`
	Avatar      string `json:"avatar,omitempty"`
	Description string `json:"description,omitempty"`
	Created     string `json:"created"`
	// Agents.
	Runtime string `json:"runtime,omitempty"`
	Model   string `json:"model,omitempty"`
	Effort  string `json:"effort,omitempty"`
	NodeID  string `json:"node_id,omitempty"`
	// The node's name and kind as last seen, for when it is offline.
	NodeName   string       `json:"node_name,omitempty"`
	NodeKind   string       `json:"node_kind,omitempty"`
	Folder     string       `json:"folder,omitempty"`
	Session    string       `json:"session_conversation_id,omitempty"`
	Access     string       `json:"access,omitempty"` // only_me | everyone | listed
	AccessList []string     `json:"access_list,omitempty"`
	Listen     string       `json:"listen,omitempty"` // mention | all
	CreatedBy  string       `json:"created_by,omitempty"`
	State      string       `json:"state,omitempty"` // running | stopped
	Attention  *tcAttention `json:"attention,omitempty"`
	// Turns started in the last day (unix seconds), for the guards.
	Turns []int64 `json:"turns,omitempty"`
}

func (m *tcMember) agent() bool { return m.Kind == "agent" }

type tcConv struct {
	CID        string   `json:"cid"`
	Kind       string   `json:"kind"` // channel | dm
	Name       string   `json:"name,omitempty"`
	Private    bool     `json:"private"`
	Topic      string   `json:"topic,omitempty"`
	Members    []string `json:"members"`
	Archived   bool     `json:"archived"`
	AgentReply string   `json:"agent_reply"` // mention | all
	Created    string   `json:"created"`
	CreatedBy  string   `json:"created_by"`
}

func (c *tcConv) has(mid string) bool {
	for _, m := range c.Members {
		if m == mid {
			return true
		}
	}
	return false
}

// label names a conversation for an agent: #name, or the DM's other side.
func (c *tcConv) label(fl *tcFleet, viewer string) string {
	if c.Kind == "channel" {
		return "#" + c.Name
	}
	for _, m := range c.Members {
		if m != viewer {
			if mem := fl.members[m]; mem != nil {
				return "DM with @" + mem.Handle
			}
		}
	}
	return "DM"
}

type tcFileRef struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Mime string `json:"mime,omitempty"`
	Size int64  `json:"size"`
}

type tcFile struct {
	tcFileRef
	From string `json:"from"`
	At   string `json:"at"`
}

// tcEvent is one message event as the stream keeps it.
type tcEvent struct {
	ID       string      `json:"id"`
	CID      string      `json:"cid"`
	Root     string      `json:"root,omitempty"`
	From     string      `json:"from"`
	Kind     string      `json:"kind"` // post | edit | delete | system
	Text     string      `json:"text,omitempty"`
	Mentions []string    `json:"mentions,omitempty"`
	Files    []tcFileRef `json:"files,omitempty"`
	At       string      `json:"at"`
	Ref      string      `json:"ref,omitempty"` // edit, delete: the message
	Hop      int         `json:"hop,omitempty"`
	Runtime  string      `json:"runtime,omitempty"`
	Model    string      `json:"model,omitempty"`
	About    string      `json:"about,omitempty"` // system: the agent it is about
	Action   string      `json:"action,omitempty"`
	Woke     []string    `json:"woke,omitempty"` // agents this message woke
}

// tcMsg is a message as readers see it: its edits and delete folded in.
type tcMsg struct {
	ID          string      `json:"id"`
	CID         string      `json:"cid"`
	Root        string      `json:"root,omitempty"`
	From        string      `json:"from"`
	Kind        string      `json:"kind"` // post | system
	Text        string      `json:"text"`
	Mentions    []string    `json:"mentions,omitempty"`
	Files       []tcFileRef `json:"files,omitempty"`
	At          string      `json:"at"`
	EditedAt    string      `json:"edited_at,omitempty"`
	Deleted     bool        `json:"deleted,omitempty"`
	Hop         int         `json:"hop,omitempty"`
	Runtime     string      `json:"runtime,omitempty"`
	Model       string      `json:"model,omitempty"`
	About       string      `json:"about,omitempty"`
	Action      string      `json:"action,omitempty"`
	ReplyCount  int         `json:"reply_count,omitempty"`
	LastReplyAt string      `json:"last_reply_at,omitempty"`
	Repliers    []string    `json:"repliers,omitempty"`
	woke        []string
}

// tcWake is one event waiting for an agent.
type tcWake struct {
	ID     string `json:"id"`    // the message
	Event  string `json:"event"` // its event id (an edit's or delete's own)
	CID    string `json:"cid"`   //
	Root   string `json:"root,omitempty"`
	Kind   string `json:"kind"`   // post | edit | delete
	Reason string `json:"reason"` // dm | mention | thread | all | edit | retry
	From   string `json:"from"`
	Hop    int    `json:"hop,omitempty"`
	At     string `json:"at"`
}

func (w tcWake) key() string { return w.Event }

// tcTurn is an agent's running turn.
type tcTurn struct {
	State          string   `json:"state"` // starting | running
	ConversationID string   `json:"conversation_id,omitempty"`
	NodeID         string   `json:"node_id"`
	Started        string   `json:"started"`
	FromSeq        int64    `json:"from_seq,omitempty"`
	Hop            int      `json:"hop"`
	Cause          tcWake   `json:"cause"`
	Events         []tcWake `json:"events"`
	Posted         []string `json:"posted,omitempty"`
	Runtime        string   `json:"runtime,omitempty"`
	Model          string   `json:"model,omitempty"`
	Stop           string   `json:"stop,omitempty"` // user | timeout
}

// ── per-Fleet state ────────────────────────────────────────────────────────

// tcFleet is one space in memory. The service is the only writer of its keys,
// so it is read from the bucket once and then kept, each write checked
// against the revision it was read or written at. Its lock is never held
// across a call to a node.
type tcFleet struct {
	mu      sync.Mutex
	fid     string
	loaded  bool
	space   *tcSpace
	members map[string]*tcMember
	convs   map[string]*tcConv
	reads   map[string]string // <mid>.<cid> -> last read message id
	follows map[string]bool   // <mid>.<root> -> on/off (explicit)
	queues  map[string][]tcWake
	turns   map[string]*tcTurn
	files   map[string]*tcFile
	revs    map[string]uint64 // key -> revision
	msgs    map[string]*tcConvMsgs
	seq     uint64 // the change revision events carry
	live    map[string]*tcLive
	nodes   map[string]TeamNode
	// unreachable: nodes a call found unreachable, with their last heartbeat
	// then; such a node counts as offline until it sends a newer one.
	unreachable map[string]time.Time
}

// tcLive is what the service knows of an agent beyond its record.
type tcLive struct {
	line        string
	lineCID     string
	lineRoot    string
	dispatching bool
	watching    bool
	retries     int
	retryAt     time.Time
	paused      string // a guard holds its turns
	pausedNoted bool
	textBuf     []string // the running turn's text since its last step
	lastDelta   bool
	allText     []string
	lastSeq     int64
	stopWanted  bool
}

func (fl *tcFleet) liveOf(mid string) *tcLive {
	l := fl.live[mid]
	if l == nil {
		l = &tcLive{}
		fl.live[mid] = l
	}
	return l
}

type tcConvMsgs struct {
	byID    map[string]*tcMsg
	top     []string            // top-level messages, oldest first
	replies map[string][]string // thread root -> replies, oldest first
	all     []string            // every message, oldest first
}

func newConvMsgs() *tcConvMsgs {
	return &tcConvMsgs{byID: map[string]*tcMsg{}, replies: map[string][]string{}}
}

func (t *TeamChat) fleet(ctx context.Context, fid string) (*tcFleet, error) {
	if !desktopFleetRE.MatchString(fid) {
		return nil, fmt.Errorf("invalid Fleet")
	}
	if _, err := t.open(ctx); err != nil {
		return nil, err
	}
	t.fleetsMu.Lock()
	if t.fleets == nil {
		t.fleets = map[string]*tcFleet{}
	}
	fl := t.fleets[fid]
	if fl == nil {
		fl = &tcFleet{fid: fid}
		t.fleets[fid] = fl
	}
	t.fleetsMu.Unlock()
	return fl, nil
}

// lockFleet locks a space, reading it from the bucket on first use and
// creating it (with #general and its person) when it does not exist.
func (t *TeamChat) lockFleet(ctx context.Context, fid string) (*tcFleet, error) {
	fl, err := t.fleet(ctx, fid)
	if err != nil {
		return nil, err
	}
	fl.mu.Lock()
	if !fl.loaded {
		if err := t.load(ctx, fl); err != nil {
			fl.mu.Unlock()
			return nil, err
		}
	}
	if fl.space == nil {
		if err := t.create(ctx, fl); err != nil {
			fl.mu.Unlock()
			return nil, err
		}
	}
	return fl, nil
}

// load reads every key of a space; fl.mu is held.
func (t *TeamChat) load(ctx context.Context, fl *tcFleet) error {
	fl.members, fl.convs, fl.reads, fl.follows = map[string]*tcMember{}, map[string]*tcConv{}, map[string]string{}, map[string]bool{}
	fl.queues, fl.turns, fl.files, fl.revs = map[string][]tcWake{}, map[string]*tcTurn{}, map[string]*tcFile{}, map[string]uint64{}
	fl.msgs, fl.space = map[string]*tcConvMsgs{}, nil
	if fl.live == nil {
		fl.live = map[string]*tcLive{}
	}
	if fl.seq == 0 {
		// Revisions of this process start past any of an earlier one.
		fl.seq = uint64(time.Now().UnixMilli()) * 1000
	}
	watcher, err := t.kv.WatchFiltered(ctx, []string{fl.fid + ".>"}, jetstream.IgnoreDeletes())
	if err != nil {
		return err
	}
	defer watcher.Stop()
	prefix := fl.fid + "."
	for {
		var entry jetstream.KeyValueEntry
		select {
		case <-ctx.Done():
			return ctx.Err()
		case entry = <-watcher.Updates():
		}
		if entry == nil {
			break
		}
		key := entry.Key()
		part := strings.TrimPrefix(key, prefix)
		kind, rest, _ := strings.Cut(part, ".")
		raw := entry.Value()
		fl.revs[key] = entry.Revision()
		var bad error
		switch kind {
		case "space":
			var s tcSpace
			bad = json.Unmarshal(raw, &s)
			fl.space = &s
		case "member":
			var m tcMember
			bad = json.Unmarshal(raw, &m)
			fl.members[rest] = &m
		case "conv":
			var c tcConv
			bad = json.Unmarshal(raw, &c)
			fl.convs[rest] = &c
		case "read":
			var id string
			bad = json.Unmarshal(raw, &id)
			fl.reads[rest] = id
		case "follow":
			var on bool
			bad = json.Unmarshal(raw, &on)
			fl.follows[rest] = on
		case "queue":
			var q []tcWake
			bad = json.Unmarshal(raw, &q)
			fl.queues[rest] = q
		case "turn":
			var tr tcTurn
			bad = json.Unmarshal(raw, &tr)
			fl.turns[rest] = &tr
		case "file":
			var f tcFile
			bad = json.Unmarshal(raw, &f)
			fl.files[rest] = &f
		}
		if bad != nil {
			log.Printf("[team-chat] %s is unreadable: %v", key, bad)
		}
	}
	if fl.space != nil && fl.space.Settings == (tcSettings{}) {
		fl.space.Settings = defaultSettings()
	}
	fl.loaded = true
	return nil
}

// forget drops a space from memory after a failed write; the next call
// reads it again.
func (t *TeamChat) forget(fl *tcFleet) {
	fl.loaded = false
}

func (t *TeamChat) create(ctx context.Context, fl *tcFleet) error {
	now := t.stamp()
	space := &tcSpace{ID: fl.fid, Name: "Team", Created: now, Settings: defaultSettings()}
	owner := &tcMember{MID: tcOwner, Kind: "person", Handle: "owner", Name: "Owner", Created: now}
	general := &tcConv{CID: "general", Kind: "channel", Name: "general", Members: []string{tcOwner},
		AgentReply: "mention", Created: now, CreatedBy: tcOwner}
	if err := t.put(ctx, fl, "member."+tcOwner, owner); err != nil {
		return err
	}
	if err := t.put(ctx, fl, "conv.general", general); err != nil {
		return err
	}
	if err := t.put(ctx, fl, "space", space); err != nil {
		return err
	}
	fl.members[tcOwner], fl.convs["general"], fl.space = owner, general, space
	return nil
}

func (t *TeamChat) stamp() string { return t.clock().UTC().Format(time.RFC3339Nano) }

// put writes one key of a space at the revision it was last seen at.
func (t *TeamChat) put(ctx context.Context, fl *tcFleet, part string, v any) error {
	raw, err := json.Marshal(v)
	if err != nil {
		return err
	}
	key := fl.fid + "." + part
	var rev uint64
	if old := fl.revs[key]; old == 0 {
		rev, err = t.kv.Create(ctx, key, raw)
	} else {
		rev, err = t.kv.Update(ctx, key, raw, old)
	}
	if err != nil {
		if errors.Is(err, jetstream.ErrKeyExists) {
			t.forget(fl)
			return fmt.Errorf("the space changed meanwhile; try again")
		}
		return err
	}
	fl.revs[key] = rev
	return nil
}

func (t *TeamChat) del(ctx context.Context, fl *tcFleet, part string) error {
	key := fl.fid + "." + part
	rev := fl.revs[key]
	if rev == 0 {
		return nil
	}
	if err := t.kv.Delete(ctx, key, jetstream.LastRevision(rev)); err != nil {
		if errors.Is(err, jetstream.ErrKeyExists) {
			t.forget(fl)
		}
		return err
	}
	delete(fl.revs, key)
	return nil
}

// ── events ─────────────────────────────────────────────────────────────────

func tcSubject(fid string) string { return fmt.Sprintf("fleet.%s.apps.team-chat.events", fid) }

// emit announces a change; fl.mu is held, so revisions are in order. A
// browser that sees a gap reads the space again.
func (t *TeamChat) emit(fl *tcFleet, event map[string]any) {
	fl.seq++
	event["rev"] = fl.seq
	raw, err := json.Marshal(event)
	if err != nil || t.nc == nil {
		return
	}
	if err := t.nc.Publish(tcSubject(fl.fid), raw); err != nil {
		log.Printf("[team-chat] %s notify: %v", fl.fid, err)
	}
}

// ── messages ───────────────────────────────────────────────────────────────

func msgSubject(fid, cid string) string { return "tc." + fid + "." + cid }

// convMsgs is a conversation's messages, read from the stream on first use;
// fl.mu is held.
func (t *TeamChat) convMsgs(ctx context.Context, fl *tcFleet, cid string) (*tcConvMsgs, error) {
	if m := fl.msgs[cid]; m != nil {
		return m, nil
	}
	m := newConvMsgs()
	subject := msgSubject(fl.fid, cid)
	t.infoMu.Lock()
	info, err := t.stream.Info(ctx, jetstream.WithSubjectFilter(subject))
	t.infoMu.Unlock()
	if err != nil {
		return nil, err
	}
	remaining := info.State.Subjects[subject]
	if remaining == 0 {
		fl.msgs[cid] = m
		return m, nil
	}
	cons, err := t.stream.OrderedConsumer(ctx, jetstream.OrderedConsumerConfig{FilterSubjects: []string{subject}})
	if err != nil {
		return nil, err
	}
	for remaining > 0 {
		batch, err := cons.Fetch(int(min(remaining, 256)), jetstream.FetchMaxWait(5*time.Second))
		if err != nil {
			return nil, err
		}
		n := 0
		for msg := range batch.Messages() {
			n++
			var e tcEvent
			if json.Unmarshal(msg.Data(), &e) == nil {
				m.fold(&e)
			}
			if meta, err := msg.Metadata(); err == nil {
				remaining = meta.NumPending
			}
		}
		if err := batch.Error(); err != nil && !errors.Is(err, jetstream.ErrNoMessages) && !errors.Is(err, nats.ErrTimeout) {
			return nil, err
		}
		if n == 0 {
			break
		}
	}
	fl.msgs[cid] = m
	return m, nil
}

// fold applies one event.
func (m *tcConvMsgs) fold(e *tcEvent) *tcMsg {
	switch e.Kind {
	case "post", "system":
		if m.byID[e.ID] != nil {
			return m.byID[e.ID]
		}
		msg := &tcMsg{ID: e.ID, CID: e.CID, Root: e.Root, From: e.From, Kind: e.Kind, Text: e.Text, Mentions: e.Mentions,
			Files: e.Files, At: e.At, Hop: e.Hop, Runtime: e.Runtime, Model: e.Model, About: e.About, Action: e.Action, woke: e.Woke}
		m.byID[e.ID] = msg
		m.all = append(m.all, e.ID)
		if e.Root == "" {
			m.top = append(m.top, e.ID)
		} else {
			m.replies[e.Root] = append(m.replies[e.Root], e.ID)
			if root := m.byID[e.Root]; root != nil {
				root.ReplyCount++
				root.LastReplyAt = e.At
				if !contains(root.Repliers, e.From) {
					root.Repliers = append(root.Repliers, e.From)
				}
			}
		}
		return msg
	case "edit":
		if msg := m.byID[e.Ref]; msg != nil && !msg.Deleted {
			msg.Text, msg.EditedAt = e.Text, e.At
			msg.Mentions = e.Mentions
			return msg
		}
	case "delete":
		if msg := m.byID[e.Ref]; msg != nil {
			msg.Deleted, msg.Text, msg.Files, msg.EditedAt = true, "", nil, e.At
			return msg
		}
	}
	return nil
}

// append publishes an event to its conversation and folds it.
func (t *TeamChat) append(ctx context.Context, fl *tcFleet, e *tcEvent) (*tcMsg, error) {
	m, err := t.convMsgs(ctx, fl, e.CID)
	if err != nil {
		return nil, err
	}
	raw, err := json.Marshal(e)
	if err != nil {
		return nil, err
	}
	if _, err := t.js.Publish(ctx, msgSubject(fl.fid, e.CID), raw, jetstream.WithMsgID(fl.fid+"."+e.ID)); err != nil {
		return nil, err
	}
	return m.fold(e), nil
}

// findMsg finds a message by id in the space's conversations (cid first when
// given); fl.mu is held.
func (t *TeamChat) findMsg(ctx context.Context, fl *tcFleet, id, cid string) (*tcMsg, error) {
	if !tcIDRE.MatchString(id) {
		return nil, &ArgumentError{"no such message"}
	}
	cids := []string{}
	if cid != "" {
		cids = append(cids, cid)
	}
	for c := range fl.msgs {
		if c != cid {
			cids = append(cids, c)
		}
	}
	for c := range fl.convs {
		if fl.msgs[c] == nil && c != cid {
			cids = append(cids, c)
		}
	}
	for _, c := range cids {
		if fl.convs[c] == nil {
			continue
		}
		m, err := t.convMsgs(ctx, fl, c)
		if err != nil {
			return nil, err
		}
		if msg := m.byID[id]; msg != nil {
			return msg, nil
		}
	}
	return nil, &ArgumentError{"no message " + id}
}

// page returns folded messages: a conversation's top level, or one thread
// (its root first), between before and after, newest limit.
func (m *tcConvMsgs) page(root, before, after string, limit int) ([]*tcMsg, bool) {
	ids := m.top
	if root != "" {
		ids = append([]string{root}, m.replies[root]...)
	}
	var sel []string
	for _, id := range ids {
		if (before == "" || id < before) && (after == "" || id > after) {
			sel = append(sel, id)
		}
	}
	more := false
	if limit > 0 && len(sel) > limit {
		more = true
		if after != "" && before == "" {
			sel = sel[:limit]
		} else {
			sel = sel[len(sel)-limit:]
		}
	}
	out := make([]*tcMsg, 0, len(sel))
	for _, id := range sel {
		if msg := m.byID[id]; msg != nil {
			out = append(out, msg)
		}
	}
	return out, more
}

// mentions are the members a text names by @handle.
func mentions(fl *tcFleet, text string) []string {
	byHandle := map[string]string{}
	for _, m := range fl.members {
		byHandle[m.Handle] = m.MID
	}
	var out []string
	for _, match := range tcMentionRE.FindAllStringSubmatch(text, -1) {
		h := strings.ToLower(strings.TrimRight(match[2], "-_"))
		if mid, ok := byHandle[h]; ok && !contains(out, mid) {
			out = append(out, mid)
		}
	}
	return out
}

// follows reports whether mid follows a thread: explicitly, or because it
// wrote the root or a reply, or was mentioned there.
func (fl *tcFleet) followsThread(m *tcConvMsgs, mid, root string) bool {
	if on, ok := fl.follows[mid+"."+root]; ok {
		return on
	}
	ids := append([]string{root}, m.replies[root]...)
	for _, id := range ids {
		msg := m.byID[id]
		if msg != nil && (msg.From == mid || contains(msg.Mentions, mid)) {
			return true
		}
	}
	return false
}

// visible: whether mid may read a conversation (Phase 1: the person reads
// everything; an agent its conversations and public channels).
func (fl *tcFleet) visible(c *tcConv, mid string) bool {
	if mid == tcOwner {
		return true
	}
	return c.has(mid) || c.Kind == "channel" && !c.Private
}
