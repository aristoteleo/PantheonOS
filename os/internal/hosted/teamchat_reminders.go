package hosted

// Reminders: a wake-up an agent sets for itself, once or recurring, tied to
// a message (docs/apps/team-chat.md, "Phase 2"). They are kept in the bucket
// (rem.<agent>.<id>), so they outlive a controller restart; the scheduler
// looks every few seconds and puts a due reminder in its agent's queue, which
// wakes only that agent.

import (
	"context"
	"fmt"
	"log"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata" // time zones without the host's database
	"unicode"
)

const (
	tcMaxRems     = 50 // per agent
	tcMinEvery    = time.Minute
	tcMaxRemAhead = 366 * 24 * time.Hour
)

type tcRem struct {
	ID       string `json:"id"`
	Agent    string `json:"agent"`
	Note     string `json:"note"`
	At       string `json:"at"`              // next time, RFC 3339 UTC
	Every    string `json:"every,omitempty"` // the repeat as written ("weekday 10:00")
	TimeZone string `json:"time_zone,omitempty"`
	CID      string `json:"cid,omitempty"`
	Root     string `json:"root,omitempty"`
	About    string `json:"about,omitempty"` // the message it is tied to
	// For: the person it reminds ("" = the agent itself). A person's
	// reminder is always posted for them, by the agent or for it.
	For string `json:"for,omitempty"`
	// Lang and Asked: the language a person asked in ("English",
	// "Chinese", … or "") and their request, so the reminder is written
	// in the same language.
	Lang      string `json:"lang,omitempty"`
	Asked     string `json:"asked,omitempty"`
	Created   string `json:"created"`
	CreatedBy string `json:"created_by"`
	// Behalf: the people the turn that set it worked for; its wake works
	// for them too (the agents it may wake).
	Behalf    []string `json:"behalf,omitempty"`
	Fired     int      `json:"fired,omitempty"`
	LastFired string   `json:"last_fired,omitempty"`
}

// tcSchedule is a repeat: every interval, or on some weekdays at a time.
type tcSchedule struct {
	interval time.Duration
	days     [7]bool // by time.Weekday
	hour     int
	minute   int
}

// next is the first time after `after` (in loc for the weekly kind).
func (s tcSchedule) next(after time.Time, loc *time.Location) time.Time {
	if s.interval > 0 {
		return after.Add(s.interval)
	}
	local := after.In(loc)
	for d := 0; d <= 8; d++ {
		day := local.AddDate(0, 0, d)
		at := time.Date(day.Year(), day.Month(), day.Day(), s.hour, s.minute, 0, 0, loc)
		if at.After(after) && s.days[at.Weekday()] {
			return at
		}
	}
	return after.Add(24 * time.Hour)
}

// nextAfterMissed: the next time of a repeat that came due at `due`, past
// now (missed times are not made up).
func (s tcSchedule) nextAfterMissed(due, now time.Time, loc *time.Location) time.Time {
	if s.interval > 0 {
		next := due.Add(s.interval)
		if !next.After(now) {
			n := now.Sub(due)/s.interval + 1
			next = due.Add(n * s.interval)
		}
		return next
	}
	next := s.next(due, loc)
	if !next.After(now) {
		next = s.next(now, loc)
	}
	return next
}

var (
	remUnitRE  = regexp.MustCompile(`^(?:in\s+)?(an?|\d+(?:\.\d+)?)?\s*(s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?|w|wks?|weeks?)$`)
	remClockRE = regexp.MustCompile(`^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$`)
	remDateRE  = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})(?:[ t](.+))?$`)
	remDays    = map[string]time.Weekday{"sun": 0, "sunday": 0, "mon": 1, "monday": 1, "tue": 2, "tues": 2, "tuesday": 2,
		"wed": 3, "wednesday": 3, "thu": 4, "thur": 4, "thurs": 4, "thursday": 4, "fri": 5, "friday": 5, "sat": 6, "saturday": 6}
)

func remUnit(u string) time.Duration {
	switch {
	case strings.HasPrefix(u, "s"):
		return time.Second
	case strings.HasPrefix(u, "m"):
		return time.Minute
	case strings.HasPrefix(u, "h"):
		return time.Hour
	case strings.HasPrefix(u, "d"):
		return 24 * time.Hour
	}
	return 7 * 24 * time.Hour
}

// remDuration reads "in 2 minutes", "2m", "an hour"; bare: also "minute"
// (a repeat's "every minute").
func remDuration(s string, bare bool) (time.Duration, bool) {
	m := remUnitRE.FindStringSubmatch(s)
	if m == nil || m[1] == "" && !bare {
		return 0, false
	}
	n := 1.0
	switch m[1] {
	case "", "a", "an":
	default:
		v, err := strconv.ParseFloat(m[1], 64)
		if err != nil || v <= 0 {
			return 0, false
		}
		n = v
	}
	return time.Duration(n * float64(remUnit(m[2]))), true
}

// remClock reads "14:30", "9", "9am", "2:30 pm".
func remClock(s string) (int, int, bool) {
	m := remClockRE.FindStringSubmatch(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(s), "at ")))
	if m == nil {
		return 0, 0, false
	}
	h, _ := strconv.Atoi(m[1])
	mm := 0
	if m[2] != "" {
		mm, _ = strconv.Atoi(m[2])
	}
	switch m[3] {
	case "am":
		if h == 12 {
			h = 0
		}
	case "pm":
		if h < 12 {
			h += 12
		}
	}
	if h > 23 || mm > 59 || m[3] != "" && (h > 23 || m[1] == "0") {
		return 0, 0, false
	}
	return h, mm, true
}

// clockLike: a text that names a time of day by itself ("14:30", "9am"),
// not a bare number.
func clockLike(s string) bool {
	return strings.Contains(s, ":") || strings.HasSuffix(s, "am") || strings.HasSuffix(s, "pm")
}

// remDaySet reads "weekday", "weekdays", "weekend", "day", "mon,wed", "mon and fri".
func remDaySet(s string) ([7]bool, bool) {
	var days [7]bool
	switch s {
	case "day", "days", "daily", "every day":
		return [7]bool{true, true, true, true, true, true, true}, true
	case "weekday", "weekdays", "workday", "workdays":
		return [7]bool{false, true, true, true, true, true, false}, true
	case "weekend", "weekends":
		return [7]bool{true, false, false, false, false, false, true}, true
	}
	some := false
	for _, part := range strings.FieldsFunc(strings.ReplaceAll(s, " and ", ","), func(r rune) bool { return r == ',' || r == ' ' }) {
		d, ok := remDays[strings.TrimSuffix(part, "s")]
		if !ok {
			if d, ok = remDays[part]; !ok {
				return days, false
			}
		}
		days[d], some = true, true
	}
	return days, some
}

// parseEvery reads a repeat: "5 minutes", "hour", "day 9:00", "weekday 10:00",
// "mon,wed 10:00", "10:00" (each day).
func parseEvery(spec string, now time.Time) (tcSchedule, error) {
	s := strings.TrimSpace(strings.TrimPrefix(strings.ToLower(strings.Join(strings.Fields(spec), " ")), "every "))
	if d, ok := remDuration(s, true); ok {
		if d < tcMinEvery {
			return tcSchedule{}, fmt.Errorf("a reminder repeats at most every minute")
		}
		return tcSchedule{interval: d}, nil
	}
	if h, m, ok := remClock(s); ok && clockLike(s) {
		return tcSchedule{days: [7]bool{true, true, true, true, true, true, true}, hour: h, minute: m}, nil
	}
	// "<days> [at] <clock>"
	for i := len(s); i > 0; i-- {
		if i < len(s) && s[i] != ' ' {
			continue
		}
		days, ok := remDaySet(strings.TrimSpace(s[:i]))
		if !ok {
			continue
		}
		clock := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(s[i:]), "at"))
		if clock == "" {
			clock = "9:00"
		}
		h, m, ok := remClock(clock)
		if !ok {
			break
		}
		return tcSchedule{days: days, hour: h, minute: m}, nil
	}
	return tcSchedule{}, fmt.Errorf("unknown repeat %q (try: every 5 minutes, every day 9:00, every weekday 10:00, every mon,wed 10:00)", spec)
}

// parseWhen reads when a reminder is due: once ("in 2 minutes", "14:30",
// "tomorrow 9:00", "monday 10:00", "2026-11-06 09:00") or, starting with
// "every", recurring. It returns the first time and the repeat (nil: once).
func parseWhen(when string, now time.Time, loc *time.Location) (time.Time, *tcSchedule, error) {
	s := strings.ToLower(strings.Join(strings.Fields(when), " "))
	s = strings.TrimSuffix(strings.TrimSpace(strings.TrimPrefix(s, "at ")), ".")
	if s == "" {
		return time.Time{}, nil, fmt.Errorf("say when")
	}
	if strings.HasPrefix(s, "every ") || s == "hourly" || s == "daily" {
		spec := strings.TrimPrefix(s, "every ")
		if s == "hourly" {
			spec = "hour"
		}
		if s == "daily" {
			spec = "day 9:00"
		}
		sched, err := parseEvery(spec, now)
		if err != nil {
			return time.Time{}, nil, err
		}
		return sched.next(now, loc), &sched, nil
	}
	if d, ok := remDuration(s, false); ok {
		if d < 10*time.Second {
			d = 10 * time.Second
		}
		return now.Add(d), nil, nil
	}
	if ts, ok := parseISO(s); ok {
		return ts, nil, nil
	}
	local := now.In(loc)
	at := func(day time.Time, h, m int) time.Time {
		return time.Date(day.Year(), day.Month(), day.Day(), h, m, 0, 0, loc)
	}
	if m := remDateRE.FindStringSubmatch(s); m != nil {
		y, _ := strconv.Atoi(m[1])
		mo, _ := strconv.Atoi(m[2])
		d, _ := strconv.Atoi(m[3])
		h, mm := 9, 0
		if m[4] != "" {
			var ok bool
			if h, mm, ok = remClock(m[4]); !ok {
				if ts, err := time.Parse(time.RFC3339, strings.ToUpper(s)); err == nil {
					return ts, nil, nil
				}
				return time.Time{}, nil, fmt.Errorf("unknown time %q", m[4])
			}
		}
		return time.Date(y, time.Month(mo), d, h, mm, 0, 0, loc), nil, nil
	}
	day, rest := "", s
	if i := strings.IndexByte(s, ' '); i > 0 {
		day, rest = s[:i], strings.TrimSpace(s[i+1:])
	} else if _, isDay := remDays[s]; isDay || s == "today" || s == "tomorrow" {
		day, rest = s, "9:00"
	}
	switch {
	case day == "" || day == "today":
		h, m, ok := remClock(rest)
		if !ok || day == "" && !clockLike(rest) {
			break
		}
		t := at(local, h, m)
		if !t.After(now) {
			if day == "today" {
				return time.Time{}, nil, fmt.Errorf("%s today has passed", rest)
			}
			t = at(local.AddDate(0, 0, 1), h, m)
		}
		return t, nil, nil
	case day == "tomorrow":
		if h, m, ok := remClock(rest); ok {
			return at(local.AddDate(0, 0, 1), h, m), nil, nil
		}
	default:
		if wd, ok := remDays[day]; ok {
			if h, m, ok := remClock(rest); ok {
				for d := 0; d <= 7; d++ {
					t := at(local.AddDate(0, 0, d), h, m)
					if t.Weekday() == wd && t.After(now) {
						return t, nil, nil
					}
				}
			}
		}
	}
	return time.Time{}, nil, fmt.Errorf("unknown time %q (try: in 2 minutes, 14:30, tomorrow 9:00, monday 10:00, 2026-11-06 09:00, every weekday 10:00)", when)
}

// parseISO reads a time that names its own offset: "2026-11-06T09:00+09:00",
// "2026-11-06 09:00:00Z", "2026-11-06T09:00 -0500".
func parseISO(s string) (time.Time, bool) {
	u := strings.ToUpper(strings.TrimSpace(s))
	if len(u) < 16 {
		return time.Time{}, false
	}
	if u[10] == ' ' {
		u = u[:10] + "T" + u[11:]
	}
	u = strings.Replace(u, " +", "+", 1)
	u = strings.Replace(u, " -", "-", 1)
	u = strings.Replace(u, " Z", "Z", 1)
	for _, layout := range []string{time.RFC3339, time.RFC3339Nano, "2006-01-02T15:04Z07:00", "2006-01-02T15:04:05Z0700", "2006-01-02T15:04Z0700"} {
		if ts, err := time.Parse(layout, u); err == nil {
			return ts, true
		}
	}
	return time.Time{}, false
}

// spaceLoc is the space's time zone.
func spaceLoc(fl *tcFleet) *time.Location {
	if fl.space != nil && fl.space.TimeZone != "" {
		if loc, err := time.LoadLocation(fl.space.TimeZone); err == nil {
			return loc
		}
	}
	return time.UTC
}

// remWhen is a reminder's time as an agent reads it.
func remWhen(ts time.Time, loc *time.Location) string {
	return ts.In(loc).Format("Mon Jan 2 15:04") + " " + locName(loc)
}

func locName(loc *time.Location) string {
	if loc == time.UTC {
		return "UTC"
	}
	return loc.String()
}

// remKey is a reminder's key part.
func remKey(r *tcRem) string { return "rem." + r.Agent + "." + r.ID }

// setReminder makes a reminder of agent m, for the person forMID ("" = the
// agent itself), its times read in loc (nil: the space's); fl.mu is held.
func (t *TeamChat) setReminder(ctx context.Context, fl *tcFleet, m *tcMember, by, when, every, note, cid, root, about, forMID, asked string,
	loc *time.Location) (*tcRem, error) {
	note = strings.TrimSpace(note)
	if note == "" {
		return nil, &ArgumentError{"Not set: say what to remind you of (the note on stdin: - <<'EOF' … EOF, or after the time)"}
	}
	if len([]rune(note)) > 2000 {
		return nil, &ArgumentError{"Not set: a reminder's note is at most 2000 characters"}
	}
	n := 0
	for _, r := range fl.rems {
		if r.Agent == m.MID {
			n++
		}
	}
	if n >= tcMaxRems {
		return nil, &ArgumentError{fmt.Sprintf("Not set: an agent has at most %d reminders; cancel one first (remind list)", tcMaxRems)}
	}
	if loc == nil {
		loc = spaceLoc(fl)
	}
	now := t.clock()
	var first time.Time
	var sched *tcSchedule
	var err error
	if strings.TrimSpace(every) != "" {
		s, err := parseEvery(every, now)
		if err != nil {
			return nil, &ArgumentError{"Not set: " + err.Error()}
		}
		sched = &s
		first = s.next(now, loc)
		if strings.TrimSpace(when) != "" {
			if first, _, err = parseWhen(when, now, loc); err != nil {
				return nil, &ArgumentError{"Not set: " + err.Error()}
			}
		}
		every = strings.TrimPrefix(strings.ToLower(strings.Join(strings.Fields(every), " ")), "every ")
	} else {
		if first, sched, err = parseWhen(when, now, loc); err != nil {
			return nil, &ArgumentError{"Not set: " + err.Error()}
		}
		if sched != nil {
			every = strings.TrimPrefix(strings.ToLower(strings.Join(strings.Fields(when), " ")), "every ")
		}
	}
	if !first.After(now.Add(-time.Second)) {
		return nil, &ArgumentError{"Not set: that time has passed"}
	}
	if first.Sub(now) > tcMaxRemAhead {
		return nil, &ArgumentError{"Not set: a reminder is at most a year ahead"}
	}
	r := &tcRem{ID: "r" + shortID(), Agent: m.MID, Note: note, At: first.UTC().Format(time.RFC3339), TimeZone: locName(loc),
		CID: cid, Root: root, About: about, For: forMID, Created: t.stamp(), CreatedBy: by}
	if tr := fl.turns[m.MID]; tr != nil && tr.WorksFor != "" {
		// Set in a turn: it works for that turn's person, always.
		r.Behalf = []string{tr.WorksFor}
	}
	if forMID != "" {
		r.Asked = clipText(strings.TrimSpace(asked), 300)
		r.Lang = langOf(asked)
	}
	if sched != nil {
		r.Every = every
	}
	if err := t.put(ctx, fl, remKey(r), r); err != nil {
		return nil, err
	}
	fl.rems[r.ID] = r
	t.emit(fl, map[string]any{"type": "reminder", "reminder": r})
	return r, nil
}

// cancelReminder removes one; fl.mu is held.
func (t *TeamChat) cancelReminder(ctx context.Context, fl *tcFleet, r *tcRem) error {
	if err := t.del(ctx, fl, remKey(r)); err != nil {
		return err
	}
	delete(fl.rems, r.ID)
	t.emit(fl, map[string]any{"type": "reminder", "id": r.ID, "agent": r.Agent, "deleted": true, "for": r.For})
	return nil
}

// remSeen: whether agent m's turn tr may list or cancel reminder r: all of
// them in its owner's turns; in a member's turn, the ones set in that
// member's turns or for them, and the ones tied to a conversation they see.
func (fl *tcFleet) remSeen(m *tcMember, tr *tcTurn, r *tcRem) bool {
	p := ownerOf(m)
	if tr != nil && tr.WorksFor != "" {
		p = tr.WorksFor
	}
	if p == ownerOf(m) {
		return true
	}
	if r.For == p || len(r.Behalf) > 0 && r.Behalf[0] == p {
		return true
	}
	c := fl.convs[r.CID]
	return c != nil && fl.visible(c, p)
}

// remPerson is the person a reminder works for: the one it reminds, else
// the person the turn that set it worked for ("" for the agent's own).
func remPerson(fl *tcFleet, r *tcRem) string {
	if x := fl.members[r.For]; x != nil && !x.agent() {
		return x.MID
	}
	if len(r.Behalf) > 0 {
		if x := fl.members[r.Behalf[0]]; x != nil && !x.agent() {
			return x.MID
		}
	}
	return ""
}

// remindersOf lists reminders (one agent's when mid is set), soonest first.
func remindersOf(fl *tcFleet, mid string) []*tcRem {
	var out []*tcRem
	for _, r := range fl.rems {
		if mid == "" || r.Agent == mid {
			out = append(out, r)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].At != out[j].At {
			return out[i].At < out[j].At
		}
		return out[i].ID < out[j].ID
	})
	return out
}

// fireDue puts each due reminder of a space in its agent's queue and moves
// a recurring one on (a one-off is removed); fl.mu is held. A reminder is
// queued before it moves on, so a restart in between fires it again rather
// than never (its wake has the same id, which a queue takes once).
func (t *TeamChat) fireDue(ctx context.Context, fl *tcFleet) {
	now := t.clock()
	for _, r := range remindersOf(fl, "") {
		due, err := time.Parse(time.RFC3339, r.At)
		if err != nil || due.After(now) {
			continue
		}
		m := fl.members[r.Agent]
		if m == nil || !m.agent() {
			_ = t.cancelReminder(ctx, fl, r)
			continue
		}
		if fl.goneFor(r) {
			// Set for, or in a turn of, a person no longer in the space.
			_ = t.cancelReminder(ctx, fl, r)
			continue
		}
		cid, root := r.CID, r.Root
		if cid != "" && fl.convs[cid] == nil {
			cid, root = "", ""
		}
		w := tcWake{ID: r.About, Event: "rem-" + r.ID + "-" + strconv.FormatInt(due.Unix(), 10), CID: cid,
			Root: root, Kind: "reminder", Reason: "reminder", From: r.Agent, At: now.UTC().Format(time.RFC3339Nano), Rem: r.ID, Note: r.Note,
			For: r.For, Lang: r.Lang, Asked: r.Asked, Behalf: r.Behalf}
		busy := fl.turns[m.MID] != nil
		if w.forPerson(fl) && (t.cannotRun(fl, m) || busy) && t.remindPeople(ctx, fl, m, nil, []tcWake{w}, "")[w.key()] {
			// A person's reminder does not wait for an agent that cannot
			// run, nor for the end of its running turn: it is posted for
			// them now (and not queued, so no later turn posts it again).
			// The busy agent is told, so it does not post it too.
			if busy {
				fl.liveOf(m.MID).notePosted(now, "@"+fl.members[w.For].Handle, *r)
			}
		} else {
			// (If the turn it queues cannot start, tryStart posts it for
			// the person: remindNow.)
			t.enqueue(ctx, fl, r.Agent, w)
		}
		log.Printf("[team-chat] %s %s: reminder %s is due", fl.fid, m.Handle, r.ID)
		if r.Every == "" {
			_ = t.cancelReminder(ctx, fl, r)
			continue
		}
		sched, err := parseEvery(r.Every, now)
		if err != nil {
			_ = t.cancelReminder(ctx, fl, r)
			continue
		}
		loc := time.UTC
		if r.TimeZone != "" && r.TimeZone != "UTC" {
			if l, err := time.LoadLocation(r.TimeZone); err == nil {
				loc = l
			}
		}
		next := *r
		next.At = sched.nextAfterMissed(due, now, loc).UTC().Format(time.RFC3339)
		next.Fired++
		next.LastFired = now.UTC().Format(time.RFC3339)
		if err := t.put(ctx, fl, remKey(&next), &next); err != nil {
			log.Printf("[team-chat] %s reminder %s: %v", fl.fid, r.ID, err)
			continue
		}
		fl.rems[r.ID] = &next
		t.emit(fl, map[string]any{"type": "reminder", "reminder": &next})
	}
}

// notePosted keeps a person's reminder that Team Chat posted while the
// agent was in a turn (the last two hours of them).
func (l *tcLive) notePosted(at time.Time, who string, r tcRem) {
	var kept []tcRemPosted
	for _, x := range l.remPosted {
		if at.Sub(x.at) < 2*time.Hour {
			kept = append(kept, x)
		}
	}
	l.remPosted = append(kept, tcRemPosted{at: at, who: who, note: r.Note, rem: r})
}

// postedLine tells the agent a reminder of theirs was posted for them.
func postedLine(x tcRemPosted, loc *time.Location) string {
	return fmt.Sprintf("Team Chat posted your reminder for %s at %s while you were busy (%s). It is done: don't post it again.\n",
		x.who, x.at.In(loc).Format("15:04"), clipText(firstLineOf(x.note), 120))
}

// fireReminders looks at every space in memory (the start loads each space
// that has reminders).
func (t *TeamChat) fireReminders(ctx context.Context) {
	t.fleetsMu.Lock()
	var fleets []*tcFleet
	for _, fl := range t.fleets {
		fleets = append(fleets, fl)
	}
	t.fleetsMu.Unlock()
	for _, fl := range fleets {
		fl.mu.Lock()
		if !fl.loaded {
			if err := t.load(ctx, fl); err != nil {
				fl.mu.Unlock()
				continue
			}
		}
		if fl.space != nil && len(fl.rems) > 0 {
			t.fireDue(ctx, fl)
		}
		fl.mu.Unlock()
	}
}

// remindHelp is `remind help`, and what a wrong `remind` is answered with.
const remindHelp = `remind <when> [--for @person|me] [--tz ZONE] [--every SPEC] [--about ID] - <<'EOF'
<the note>
EOF
  <when>: in 2 minutes | 2m | 1h | 14:30 | tomorrow 9:00 | monday 10:00 | 2026-11-06 09:00
          | 2026-11-06T09:00+09:00 (a time with its own offset) | every weekday 10:00
  --every: 5 minutes | hour | day 9:00 | weekday 10:00 | mon,wed 10:00
  --tz: the time zone the time is in (an IANA name: Asia/Tokyo, America/New_York); without it,
        the space's time zone. A person who names a city or zone ("9am Tokyo time") means that one.
  --for: who the reminder is for. A person who asks "remind me" gets it: --for @their-handle
         (the default when a person's message started your turn). --for me when it is your own
         follow-up. When a person's reminder comes due you post it for them; if you post
         nothing, Team Chat posts your final text, or the note, for them. One that comes due
         while you are in a turn is posted by Team Chat at once (inbox says so): don't repeat it.
remind list | remind cancel <id>
Reminder ids are for these commands only: never show them to people. Tell a person they can
say "cancel the reminder" instead.`

// remFor reads --for: a person's handle, or me/self (the agent itself).
func (t *TeamChat) remFor(fl *tcFleet, m *tcMember, tr *tcTurn, about *tcMsg, opt string) (string, error) {
	switch v := strings.ToLower(strings.TrimSpace(opt)); v {
	case "me", "self", "myself", "agent", "@" + m.Handle, m.Handle:
		return "", nil
	case "":
		// A person asked in the message it is about, or in the message
		// that started this turn: it is theirs.
		if about != nil {
			if x := fl.members[about.From]; x != nil && !x.agent() {
				return x.MID, nil
			}
		}
		if tr != nil {
			if x := fl.members[tr.Cause.From]; x != nil && !x.agent() {
				return x.MID, nil
			}
		}
		return "", nil
	default:
		x := t.memberByHandle(fl, v)
		if x == nil {
			return "", fmt.Errorf("Not set: no member %s (see: members)", opt)
		}
		if x.agent() {
			return "", fmt.Errorf("Not set: --for names a person (or me); @%s is an agent: post to it when the reminder wakes you", x.Handle)
		}
		return x.MID, nil
	}
}

// agentRemind serves `pantheon-team remind …`; fl.mu is held.
func (t *TeamChat) agentRemind(ctx context.Context, fl *tcFleet, m *tcMember, tr *tcTurn, req agentRequest) (string, error) {
	space := spaceLoc(fl)
	loc := space
	if tz := strings.TrimSpace(req.Opts["tz"]); tz != "" {
		l, err := time.LoadLocation(tz)
		if err != nil || strings.EqualFold(tz, "local") {
			return "", fmt.Errorf("Not set: unknown time zone %q (use an IANA name such as Asia/Tokyo, Europe/Berlin, America/New_York, UTC)", tz)
		}
		loc = l
	}
	switch arg(req, 0) {
	case "help", "--help", "-h":
		return remindHelp, nil
	case "list", "":
		turnP := ownerOf(m)
		if tr != nil && tr.WorksFor != "" {
			turnP = tr.WorksFor
		}
		var lines []string
		for _, r := range remindersOf(fl, m.MID) {
			if !fl.remSeen(m, tr, r) {
				continue
			}
			at, _ := time.Parse(time.RFC3339, r.At)
			line := fmt.Sprintf("[%s] %s", r.ID, remWhen(at, space))
			if r.Every != "" {
				line += " (every " + r.Every
				if r.TimeZone != "" && r.TimeZone != locName(space) {
					line += ", " + r.TimeZone + " time"
				}
				line += ")"
			}
			if x := fl.members[r.For]; x != nil {
				line += " for @" + x.Handle
			}
			note := clipText(r.Note, 120)
			if whose := remPerson(fl, r); whose != "" && whose != turnP {
				// Another person's reminder: context for this turn.
				note = fmt.Sprintf("[%s's reminder: context only, not a request to you in this turn] %s", handleOf(fl, whose), note)
			}
			line += ": " + note
			lines = append(lines, line)
		}
		var done string
		for _, x := range fl.liveOf(m.MID).remPosted {
			if fl.remSeen(m, tr, &x.rem) {
				done += postedLine(x, space)
			}
		}
		if len(lines) == 0 {
			return done + "You have no reminders.", nil
		}
		return done + strings.Join(lines, "\n") + "\n(The ids are for remind cancel only; don't show them to people.)", nil
	case "cancel", "delete", "remove":
		id := arg(req, 1)
		r := fl.rems[id]
		if r == nil || r.Agent != m.MID || !fl.remSeen(m, tr, r) {
			return "", fmt.Errorf("Not cancelled: you have no reminder %s (see: remind list)", id)
		}
		if err := t.cancelReminder(ctx, fl, r); err != nil {
			return "", err
		}
		return "Cancelled the reminder (" + clipText(r.Note, 80) + "). Tell the person it is cancelled; don't show its id.", nil
	}
	// The time is the longest run of first words that reads as one; the
	// note is stdin, else the words after it.
	words := strings.Fields(strings.Join(req.Args, " "))
	when, note := "", strings.TrimSpace(req.Text)
	every := req.Opts["every"]
	n := 0
	for k := min(len(words), 6); k >= 1; k-- {
		cand := strings.Join(words[:k], " ")
		if _, _, err := parseWhen(cand, t.clock(), loc); err == nil {
			when, n = cand, k
			break
		}
	}
	if every == "" && when == "" {
		_, _, err := parseWhen(strings.Join(words, " "), t.clock(), loc)
		if len(words) == 0 {
			err = fmt.Errorf("say when")
		}
		return "", fmt.Errorf("Not set: %v.\nUse: %s", err, remindHelp)
	}
	if note == "" {
		note = strings.Join(words[n:], " ")
	}
	// Tied to a message: --about, else what woke this turn.
	cid, root, about := "", "", ""
	var aboutMsg *tcMsg
	if id := strings.TrimSpace(req.Opts["about"]); id != "" {
		msg, err := t.findMsg(ctx, fl, id, "")
		if err != nil {
			return "", fmt.Errorf("Not set: %v", err)
		}
		if !fl.visible(fl.convs[msg.CID], m.MID) {
			return "", fmt.Errorf("Not set: you are not in the conversation of message %s", id)
		}
		cid, root, about, aboutMsg = msg.CID, msg.Root, msg.ID, msg
	} else if tr != nil {
		c := tr.Cause
		cid, root, about = c.CID, c.Root, c.ID
		if c.ID != "" && c.Kind != "reminder" {
			if msg, err := t.findMsg(ctx, fl, c.ID, c.CID); err == nil {
				aboutMsg = msg
			}
		}
	}
	forMID, err := t.remFor(fl, m, tr, aboutMsg, req.Opts["for"])
	if err != nil {
		return "", err
	}
	if p := tr.WorksFor; p != "" && p != ownerOf(m) && forMID != "" && forMID != p {
		// A member's turn sets reminders for that member (or the agent):
		// never in someone else's name.
		return "", fmt.Errorf("Not set: this turn works for %s, so a reminder set in it is for %s (or for you: --for me)",
			handleOf(fl, p), handleOf(fl, p))
	}
	asked := t.remAsked(ctx, fl, tr, aboutMsg, forMID)
	r, err := t.setReminder(ctx, fl, m, m.MID, when, every, note, cid, root, about, forMID, asked, loc)
	if err != nil {
		return "", err
	}
	at, _ := time.Parse(time.RFC3339, r.At)
	whenSpace := remWhen(at, space)
	phrase := remPhrase(at, t.clock(), space)
	out := "Reminder set for " + whenSpace
	if loc.String() != space.String() {
		out = fmt.Sprintf("Reminder set for %s (%s in the space's time zone)", remWhen(at, loc), whenSpace)
	}
	if r.Every != "" {
		out += ", then every " + r.Every
		if loc.String() != space.String() {
			out += " " + locName(loc) + " time"
		}
	}
	if x := fl.members[r.For]; x != nil {
		out += ". It is for @" + x.Handle + ": when it comes due, it wakes you to post it for them (Remind @" + x.Handle + ": " + clipText(r.Note, 120) + ")" +
			"; if you are still in a turn then, Team Chat posts it for them on time and your inbox says so: don't post it yourself"
	} else {
		out += ". It wakes you with: " + clipText(r.Note, 120)
	}
	if about != "" {
		out += " (about message " + about + ")"
	}
	out += ".\nNow confirm it to the person, quoting this time exactly (it is in the space's time zone): \"" + phrase + "\"" +
		" (e.g. \"I'll remind you " + phrase + ".\"). Write the confirmation in the language the person wrote in (translate words such as " +
		"\"today\" and the weekday too); keep the clock time and the time zone name as they are. Don't work the time out yourself, and don't say only \"in 2 minutes\"."
	out += " Don't show the reminder id (" + r.ID + ", for remind cancel only); a person cancels by asking you (\"cancel the reminder\")."
	return out, nil
}

// remAsked is the person's request a reminder for them was set from: the
// message it is about, else the newest message of theirs that this turn
// was woken by ("" when neither is theirs).
func (t *TeamChat) remAsked(ctx context.Context, fl *tcFleet, tr *tcTurn, about *tcMsg, forMID string) string {
	if forMID == "" {
		return ""
	}
	if about != nil && about.From == forMID && !about.Deleted {
		return about.Text
	}
	if tr == nil {
		return ""
	}
	events := append([]tcWake{tr.Cause}, tr.Events...)
	for i := len(events) - 1; i >= 0; i-- {
		e := events[i]
		if e.From != forMID || e.ID == "" || e.Kind == "reminder" {
			continue
		}
		if msg, err := t.findMsg(ctx, fl, e.ID, e.CID); err == nil && msg.From == forMID && !msg.Deleted {
			return msg.Text
		}
	}
	return ""
}

// langOf names the language a request is written in, by its letters:
// Chinese, Japanese, Korean, Russian, … or English for plain Latin text
// ("" when it can't tell). Mentions, links and code are left out.
func langOf(text string) string {
	var han, kana, hangul, cyr, arabic, latin int
	for _, w := range strings.Fields(text) {
		if strings.HasPrefix(w, "@") || strings.HasPrefix(w, "#") || strings.Contains(w, "://") || strings.HasPrefix(w, "`") {
			continue
		}
		for _, c := range w {
			switch {
			case unicode.Is(unicode.Hiragana, c) || unicode.Is(unicode.Katakana, c):
				kana++
			case unicode.Is(unicode.Han, c):
				han++
			case unicode.Is(unicode.Hangul, c):
				hangul++
			case unicode.Is(unicode.Cyrillic, c):
				cyr++
			case unicode.Is(unicode.Arabic, c):
				arabic++
			case c < 128 && unicode.IsLetter(c):
				latin++
			}
		}
	}
	switch {
	case kana > 0:
		return "Japanese"
	case han > 0:
		return "Chinese"
	case hangul > 0:
		return "Korean"
	case cyr > 0:
		return "Russian"
	case arabic > 0:
		return "Arabic"
	case latin > 0:
		// Latin letters: English unless it reads as another language,
		// which the agent sees from the request itself.
		return "English"
	}
	return ""
}

// remLang tells the agent which language to write a person's reminder in:
// the one they asked in, not the one its session drifted to.
func remLang(w tcWake, who string) string {
	if strings.TrimSpace(w.Asked) == "" {
		return ""
	}
	lang := "the language " + who + " asked in"
	if w.Lang != "" {
		lang = w.Lang + ", the language " + who + " asked in"
	}
	return fmt.Sprintf("Write the reminder in %s (they wrote: %q), even if you used another language before.\n", lang, clipText(w.Asked, 200))
}

// remPhrase is a reminder's time as an agent quotes it to people, in loc:
// "at 10:02 today (Thu Nov 5, America/Los_Angeles)", "at 09:00 tomorrow
// (…)", "at 09:00 on Mon Nov 9 (America/Los_Angeles)".
func remPhrase(at, now time.Time, loc *time.Location) string {
	a, n := at.In(loc), now.In(loc)
	same := func(x, y time.Time) bool { return x.Year() == y.Year() && x.YearDay() == y.YearDay() }
	day := ""
	switch {
	case same(a, n):
		day = "today"
	case same(a, n.AddDate(0, 0, 1)):
		day = "tomorrow"
	}
	if day != "" {
		return fmt.Sprintf("at %s %s (%s, %s)", a.Format("15:04"), day, a.Format("Mon Jan 2"), locName(loc))
	}
	return fmt.Sprintf("at %s on %s (%s)", a.Format("15:04"), a.Format("Mon Jan 2"), locName(loc))
}

// nowLine is the time now as the turn input says it: "Now: Thu Nov 5 10:13
// America/Los_Angeles (18:13 UTC)."
func nowLine(now time.Time, loc *time.Location) string {
	line := "Now: " + now.In(loc).Format("Mon Jan 2 15:04") + " " + locName(loc)
	if loc != time.UTC {
		line += " (" + now.UTC().Format("15:04") + " UTC)"
	}
	return line + ", the space's time zone. Say times to people in it; message times below are in UTC.\n"
}

// forPerson: a reminder's wake for a person still in the space.
func (w tcWake) forPerson(fl *tcFleet) bool {
	if w.Kind != "reminder" || w.For == "" {
		return false
	}
	p := fl.members[w.For]
	return p != nil && !p.agent()
}

// remindTarget is where a person's reminder is posted: the thread or
// conversation of the message it is about, else the DM with the person.
func (t *TeamChat) remindTarget(ctx context.Context, fl *tcFleet, m *tcMember, w tcWake) (string, string) {
	if c := fl.convs[w.CID]; c != nil && !c.Archived && (c.has(w.For) || fl.visible(c, w.For)) {
		return w.CID, w.Root
	}
	res, err := t.convCreate(ctx, fl, m.MID, "dm", "", false, []string{w.For}, "", "")
	if err != nil {
		return "", ""
	}
	return res.(map[string]any)["conversation"].(*tcConv).CID, ""
}

// remindWhere tells the agent how to post a person's reminder.
func (t *TeamChat) remindWhere(fl *tcFleet, m *tcMember, w tcWake) string {
	who := "@" + fl.members[w.For].Handle
	script := teamTool(m)
	cmd := fmt.Sprintf("%s dm %s - <<'EOF' … EOF", script, who)
	where := "in your DM with " + who
	if c := fl.convs[w.CID]; c != nil && !c.Archived {
		switch {
		case w.ID != "":
			cmd = fmt.Sprintf("%s reply %s - <<'EOF' … EOF", script, w.ID)
			where = "where it was asked (" + c.label(fl, m.MID)
			if w.Root != "" {
				where += ", in the thread"
			}
			where += ")"
		case c.Kind == "channel":
			cmd = fmt.Sprintf("%s post %s - <<'EOF' … EOF", script, shellQuote("#"+c.Name))
			where = "in " + c.label(fl, m.MID)
		}
	}
	return fmt.Sprintf("This reminder is for %s, a person: post it for them %s, mentioning %s, with: %s\n"+
		"Do what the note asks only when it is your work; the reminder itself is for %s. "+
		"If you post nothing, Team Chat posts your final answer, or the note, for %s there.\n", who, where, who, cmd, who, who)
}

// remindPeople posts each person's reminder of a turn that the agent did
// not post where it belongs: its final text (with the person's @handle), or
// "⏰ Reminder for @x: <note>". fl.mu is held; tr may be nil (the agent
// could not run: each reminder is then posted, even several in one
// conversation). It returns the keys of the reminders that reached their
// person (posted now, or posted there by the agent in its turn).
func (t *TeamChat) remindPeople(ctx context.Context, fl *tcFleet, m *tcMember, tr *tcTurn, events []tcWake, final string) map[string]bool {
	var posted []string
	if tr != nil {
		posted = append(posted, tr.Posted...)
	}
	done := map[string]bool{}
	seen := map[string]bool{}
	for _, e := range events {
		if !e.forPerson(fl) || seen[e.key()] {
			continue
		}
		seen[e.key()] = true
		p := fl.members[e.For]
		cid, root := t.remindTarget(ctx, fl, m, e)
		if cid == "" {
			continue
		}
		if tr != nil && (contains(posted, cid) || contains(posted, dmID(m.MID, p.MID))) {
			done[e.key()] = true
			continue
		}
		fallback := "⏰ Reminder for @" + p.Handle + ": " + strings.TrimSpace(e.Note)
		text := fallback
		if f := strings.TrimSpace(final); f != "" && !declines(f) {
			text = f
			if !strings.Contains(strings.ToLower(f), "@"+p.Handle) {
				text = "@" + p.Handle + " " + f
			}
			final = "" // once
		}
		// It works for the reminder's person (whom the turn that set it
		// worked for), also when Team Chat posts it outside a turn.
		worksFor := fl.worksFor(m, e)
		msg, err := t.agentPost(ctx, fl, m, tr, worksFor, cid, root, text, nil)
		if err != nil && tr != nil {
			msg, err = t.agentPost(ctx, fl, m, nil, worksFor, cid, root, text, nil)
		}
		if err != nil {
			log.Printf("[team-chat] %s reminder for %s by %s: %v", fl.fid, p.Handle, m.Handle, err)
			msg, err = t.system(ctx, fl, cid, root, m.MID, "", fallback)
		}
		if err == nil && msg != nil {
			posted = append(posted, cid)
			done[e.key()] = true
			log.Printf("[team-chat] %s %s: reminder %s posted for @%s", fl.fid, m.Handle, e.Rem, p.Handle)
		}
	}
	return done
}

// remindNow posts, for an agent whose queued turn cannot start now (a limit
// paused it, its computer is offline, its Workspace or Agent App is not
// ready, it needs its owner, or it is stopped), each person's reminder
// waiting in its queue, so a reminder reaches its person on time whether or
// not the agent gets a turn. The posted ones leave the queue; one that
// could not be posted stays (it is posted on the next try). fl.mu is held;
// why goes to the log. It returns what is left in the queue.
func (t *TeamChat) remindNow(ctx context.Context, fl *tcFleet, m *tcMember, why string) []tcWake {
	q := fl.queues[m.MID]
	var due []tcWake
	for _, e := range q {
		if e.forPerson(fl) {
			due = append(due, e)
		}
	}
	if len(due) == 0 {
		return q
	}
	posted := map[string]bool{}
	for _, e := range due {
		// One at a time: two reminders for one conversation are both posted.
		for k := range t.remindPeople(ctx, fl, m, nil, []tcWake{e}, "") {
			posted[k] = true
		}
	}
	if len(posted) == 0 {
		return q
	}
	var rest []tcWake
	for _, e := range fl.queues[m.MID] {
		if !posted[e.key()] {
			rest = append(rest, e)
		}
	}
	log.Printf("[team-chat] %s %s: %d reminder(s) posted for people without a turn (%s)", fl.fid, m.Handle, len(posted), why)
	t.saveQueue(ctx, fl, m.MID, rest)
	t.emitStatus(fl, m.MID)
	return rest
}

// cannotRun: an agent that will not start a turn soon (stopped, needs the
// person, paused by a limit, its Agent App is starting, or its computer is
// offline).
func (t *TeamChat) cannotRun(fl *tcFleet, m *tcMember) bool {
	// (The limits are looked at now, not a pause noted earlier: a pause
	// that has run out does not count.)
	if m.State == "stopped" || m.Attention != nil || t.guard(fl, m) != "" || t.appStarting(fl, m) {
		return true
	}
	node, known := fl.nodes[m.NodeID]
	return known && !node.Online
}
