# Data

Everything here is a **scripted role-play**, written for development and evaluation. No real client
said any of it. Replace these files with recorded, consented meeting transcripts before drawing
conclusions about quality.

How the data was made: the gold requirement list (`gold/*.json`) was written first, as a role-play
script per requirement (what is said, by whom, and which trap it carries). The transcripts were then
written to that script with realistic speech: filler words, interruptions, tangents, recaps.

| File | What it is |
|---|---|
| `transcripts/trailhead/meeting-01-kickoff.txt` | Discovery kickoff, 55 min. Agency client running GuideFinder, a Brilliant Directories directory of outdoor guides. |
| `transcripts/trailhead/meeting-02-deep-dive.txt` | Technical deep dive, 46 min. Adds two requirements, fills integration details. |
| `transcripts/trailhead/meeting-03-follow-up.txt` | Follow-up after scope v1 was approved, 45 min. It modifies, drops and adds items, and makes requests outside the agreed scope. |
| `transcripts/petalpath/meeting-01-discovery.txt` | A second client for variety: a directory of wedding florists, 45 min. Held out from development. |
| `gold/trailhead.json`, `gold/petalpath.json` | Gold requirements with delivery kind, keyword groups for matching, traps, things that must not become requirements, expected open-question categories, and the expected follow-up changes. |

Traps built into the meetings:
- an offhand requirement said while the call is wrapping up;
- reversed decisions ("actually, scratch that");
- non-functional needs (mobile speed, cookie consent, accessibility);
- requests that are just an existing product setting (auto-suspending past-due members, approving new listings);
- vague asks ("make search better", "feel more premium").

Transcript format, one utterance per line: `[HH:MM:SS] ROLE (Name): text`, where ROLE is CLIENT or
CONSULTANT. Three `#` header lines give the meeting title, date and participants. The parser also
accepts unformatted text.
