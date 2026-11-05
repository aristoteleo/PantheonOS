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
)

const (
	tcMaxRems     = 50 // per agent
	tcMinEvery    = time.Minute
	tcMaxRemAhead = 366 * 24 * time.Hour
)

type tcRem struct {
	ID        string `json:"id"`
	Agent     string `json:"agent"`
	Note      string `json:"note"`
	At        string `json:"at"`              // next time, RFC 3339 UTC
	Every     string `json:"every,omitempty"` // the repeat as written ("weekday 10:00")
	TimeZone  string `json:"time_zone,omitempty"`
	CID       string `json:"cid,omitempty"`
	Root      string `json:"root,omitempty"`
	About     string `json:"about,omitempty"` // the message it is tied to
	Created   string `json:"created"`
	CreatedBy string `json:"created_by"`
	Fired     int    `json:"fired,omitempty"`
	LastFired string `json:"last_fired,omitempty"`
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

// setReminder makes a reminder for agent m; fl.mu is held.
func (t *TeamChat) setReminder(ctx context.Context, fl *tcFleet, m *tcMember, by, when, every, note, cid, root, about string) (*tcRem, error) {
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
	loc, now := spaceLoc(fl), t.clock()
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
		CID: cid, Root: root, About: about, Created: t.stamp(), CreatedBy: by}
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
	t.emit(fl, map[string]any{"type": "reminder", "id": r.ID, "agent": r.Agent, "deleted": true})
	return nil
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
		cid, root := r.CID, r.Root
		if cid != "" && fl.convs[cid] == nil {
			cid, root = "", ""
		}
		t.enqueue(ctx, fl, r.Agent, tcWake{ID: r.About, Event: "rem-" + r.ID + "-" + strconv.FormatInt(due.Unix(), 10), CID: cid,
			Root: root, Kind: "reminder", Reason: "reminder", From: r.Agent, At: now.UTC().Format(time.RFC3339Nano), Rem: r.ID, Note: r.Note})
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

// agentRemind serves `pantheon-team remind …`; fl.mu is held.
func (t *TeamChat) agentRemind(ctx context.Context, fl *tcFleet, m *tcMember, tr *tcTurn, req agentRequest) (string, error) {
	loc := spaceLoc(fl)
	switch arg(req, 0) {
	case "list", "":
		var lines []string
		for _, r := range remindersOf(fl, m.MID) {
			at, _ := time.Parse(time.RFC3339, r.At)
			line := fmt.Sprintf("[%s] %s", r.ID, remWhen(at, loc))
			if r.Every != "" {
				line += " (every " + r.Every + ")"
			}
			line += ": " + clipText(r.Note, 120)
			lines = append(lines, line)
		}
		if len(lines) == 0 {
			return "You have no reminders.", nil
		}
		return strings.Join(lines, "\n"), nil
	case "cancel", "delete", "remove":
		id := arg(req, 1)
		r := fl.rems[id]
		if r == nil || r.Agent != m.MID {
			return "", fmt.Errorf("Not cancelled: you have no reminder %s (see: remind list)", id)
		}
		if err := t.cancelReminder(ctx, fl, r); err != nil {
			return "", err
		}
		return "Cancelled reminder " + id + ".", nil
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
		return "", fmt.Errorf("Not set: %v. Use: remind 'in 2 minutes' - <<'EOF' (the note, then a line EOF)", err)
	}
	if note == "" {
		note = strings.Join(words[n:], " ")
	}
	// Tied to a message: --about, else what woke this turn.
	cid, root, about := "", "", ""
	if id := strings.TrimSpace(req.Opts["about"]); id != "" {
		msg, err := t.findMsg(ctx, fl, id, "")
		if err != nil {
			return "", fmt.Errorf("Not set: %v", err)
		}
		if !fl.visible(fl.convs[msg.CID], m.MID) {
			return "", fmt.Errorf("Not set: you are not in the conversation of message %s", id)
		}
		cid, root, about = msg.CID, msg.Root, msg.ID
	} else if tr != nil {
		c := tr.Cause
		cid, root, about = c.CID, c.Root, c.ID
	}
	r, err := t.setReminder(ctx, fl, m, m.MID, when, every, note, cid, root, about)
	if err != nil {
		return "", err
	}
	at, _ := time.Parse(time.RFC3339, r.At)
	out := fmt.Sprintf("Reminder %s set for %s", r.ID, remWhen(at, loc))
	if r.Every != "" {
		out += ", then every " + r.Every
	}
	out += ". It wakes you with: " + clipText(r.Note, 120)
	if about != "" {
		out += " (about message " + about + ")"
	}
	return out + ". Cancel it with: remind cancel " + r.ID, nil
}
