package hosted

import (
	"net/url"
	"strings"
	"unicode/utf8"
)

// TeamPush is one notification for the space owner's own devices: the Hub
// sends it to each browser or home-screen web app the owner turned
// notifications on in (POST /api/push/notify, with the owner's credential).
type TeamPush struct {
	Title string `json:"title"`
	Body  string `json:"body"`
	// URL is the page a tap opens: the conversation, and the thread when the
	// message is a reply.
	URL string `json:"url"`
	// Tag: notifications of one conversation replace each other.
	Tag string `json:"tag"`
}

const tcPushBody = 180

// pushFor says whether a new message notifies the owner's devices, and with
// what. It is the page's rule (the conversation's bell: everything, mentions,
// mute) without the page: DMs, mentions of the owner and replies in threads
// the owner follows, plus every message where the bell says so; never the
// owner's own messages, nor system lines. fl.mu is held.
func pushFor(fl *tcFleet, c *tcConv, m *tcConvMsgs, e *tcEvent) (TeamPush, bool) {
	sender := fl.members[e.From]
	if e.Kind != "post" || sender == nil || e.From == tcOwner || !c.has(tcOwner) {
		return TeamPush{}, false
	}
	dm := c.Kind == "dm"
	level := fl.notify[tcOwner+"."+c.CID]
	if level == "" {
		level = "mentions"
		if dm {
			level = "all"
		}
	}
	if level == "mute" {
		return TeamPush{}, false
	}
	followed := e.Root != "" && fl.followsThread(m, tcOwner, e.Root)
	if !dm && !contains(e.Mentions, tcOwner) && !followed && level != "all" {
		return TeamPush{}, false
	}
	title := sender.Name
	if title == "" {
		title = "@" + sender.Handle
	}
	if !dm {
		title += " in #" + c.Name
	}
	body := strings.Join(strings.Fields(e.Text), " ")
	if body == "" && len(e.Files) > 0 {
		body = "Sent " + e.Files[0].Name
	}
	if len(body) > tcPushBody {
		cut := tcPushBody
		for cut > 0 && !utf8.RuneStart(body[cut]) {
			cut--
		}
		body = body[:cut] + "…"
	}
	q := url.Values{"conv": {c.CID}}
	if e.Root != "" {
		q.Set("thread", e.Root)
	}
	return TeamPush{Title: title, Body: body, URL: "/app/team-chat?" + q.Encode(), Tag: "team-chat." + c.CID}, true
}
