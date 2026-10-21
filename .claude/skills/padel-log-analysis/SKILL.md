---
name: padel-log-analysis
description: Analyse the Padel Booking Home Assistant addon running on the HA host — pull its logs, status and /config/padel state files over SSH (read-only), cross-check them, and report what is going wrong and why. Use this whenever the user asks how the padel booker is doing, why no court was booked, whether a booking actually went through, what the addon logs say, whether the scheduler/nightly run fired, or wants a health check / log scan / "kijk even naar de logs" of the padel addon on HA — even if they don't say "SSH" or "logs" explicitly.
---

# Padel Booking — log & state analysis

Goal: answer "is the booker healthy, and if not, why?" with evidence from the live HA host.
The addon runs unattended, so problems tend to be silent: a payment link nobody paid, a
nightly run that was skipped, a session that keeps failing. The value of this skill is
cross-checking sources against each other, not just grepping for ERROR.

## Ground rules

- **Read-only.** Never start, stop, restart or update the addon, and never edit files on the
  host. In `schedule_mode: smart` a stopped addon misses release moments, so if a restart looks
  needed, recommend it and let the user decide (CLAUDE.md says the same).
- **No secrets in the output.** `collect.sh` filters password fields, cookie values and the
  Playtomic access token on the host. Don't `cat` `.meetandplay_cookies.json` or
  `.playtomic_token.json` yourself. Payment URLs and the account email are fine to mention
  if relevant but don't need to be.
- Host facts: no python3 (use `jq`), `ha apps …` not `ha addons …`, slug
  `a30a93bf_padel_booking`. The host address comes from `PADEL_HA_HOST` (set in the local,
  git-ignored `.claude/settings.local.json`); the repo is public, so never write the real
  address into tracked files. In zsh `$SSH` is not word-split, so use an array:
  `SSH=(ssh -i ~/.ssh/id_ed25519_ha -o IdentitiesOnly=yes -o BatchMode=yes "$PADEL_HA_HOST")`
  and `"${SSH[@]}" "<cmd>"`. If `PADEL_HA_HOST` is unset, `collect.sh` falls back to
  `root@homeassistant.local`; when that fails, ask the user for the address.

## Workflow

### 1. Collect a snapshot

```bash
SNAP=<scratchpad>/padel-snapshot-$(date +%Y%m%d-%H%M)
.claude/skills/padel-log-analysis/scripts/collect.sh "$SNAP" 5000
```

`ha apps logs` returns only 100 lines by default; 5000 covers roughly 10 days at the current
verbosity. If the user asks about a specific older date and the log window (step 2) doesn't
reach it, rerun with more lines (e.g. 20000). Logs survive addon restarts, but the host journal
is finite. If the date is still out of reach, rely on the state files.

If SSH fails with `Connection refused`, the Terminal & SSH addon isn't running or has no port
configured. Tell the user; don't try alternatives.

### 2. Get the fact sheet

```bash
python3 -I .claude/skills/padel-log-analysis/scripts/analyze.py "$SNAP"
```

It prints addon status/options, log window and restarts, runs per day (full vs. targeted,
outcomes, whether the 00:01 nightly ran), claimed bookings vs. the account, target-date
coverage, grouped warnings/errors with first/last seen, login/session state, and state-file ages.
It reports facts; interpreting them is your job.

Also compare the deployed version with the repo: `grep '^version' padel_booking/config.yaml`.
A repo version ahead of the deployed one means the user hasn't clicked *Check for updates →
Update* in HA yet.

### 3. Cross-check and dig in

Work through these, roughly in order of impact. For anything suspicious, grep `$SNAP/addon.log`
around the timestamp to get the actual sequence. `references/log-patterns.md` explains
the known log messages, the run lifecycle, and known pitfalls. Read it when a message or
pattern isn't obvious.

1. **Is it running?** `state` should be `started` in smart mode. Last log line should be
   recent (the scheduler is quiet between runs, so a few hours of silence can be normal; check
   whether a run was planned in that gap).
2. **Did bookings actually land?** The bot can't pay: "meldt succes" means it got a payment
   link and sent a push notification. Only `future_bookings.json` (the account's
   *Aankomend* list) is the truth. A claimed success that shows up later as `Geannuleerd`, or
   never shows up, means the payment wasn't completed in time. That's usually the most
   important finding, because the date then looks booked to the user but isn't.
3. **Coverage.** Which target dates (booking_days × weeks_ahead) are still open? Combine with
   the club schedule: is there a release moment coming for those dates, or is only the nightly
   run covering them?
4. **Did the scheduler do what it should?** Every day needs a nightly full run around 00:01
   (it also refreshes `future_bookings.json`). A missing one is a real gap. Unexpected
   restarts (`startrun`) point at crashes or watchdog restarts. Runs near the 15-minute timeout,
   or runs without an outcome, mean they were killed.
5. **Warnings/errors.** Use the "sinds laatste herstart" column: issues that stopped before the
   last restart were probably fixed by an update. Recurring `Geen boeking gemaakt door alle
   providers` alone is normal (nothing free). It only matters with signs the search itself
   is broken (selectors not found, login failures, 0 clubs found).
6. **Sessions.** An expired `meetandplay_session` cookie is normal; the bot logs in again.
   Repeated `inloggen mislukt` is not. A fresh `debug_login_failed.png` means a recent
   login failure (its mtime tells you when).
7. **Freshness.** `club_schedule.json` `generated_at` older than ~a week means the
   booking-research GitHub workflow has stopped. `future_bookings.json` older than ~25h means
   the nightly fetch didn't run or failed.

When a finding points at a code bug, open the relevant source in the repo (`scheduler.py`,
`orchestrator.py`, `fetch_bookings.py`, `providers/…`) and confirm the mechanism before
claiming it. Name the file and line.

### 4. Report

Reply in the user's language. Lead with the verdict, then evidence. Keep it scannable:

```
**Status:** <one line: healthy / degraded / broken, plus the single most important reason>

**Bevindingen** (most severe first)
1. <finding>: <evidence: timestamp, log line or file, short> → <what it means / what to do>
...

**Boekingen:** <target dates with status: betaald / open / geannuleerd>
**Planner:** <runs per day summary, missed nightly runs, restarts>
**Ruis / geen actie nodig:** <things that look scary but are normal>
```

Separate "you need to act" (pay a link, update the addon, check credentials) from "code
change needed" (offer to fix it, but don't start editing without the user agreeing, since
fixes trigger the version-bump/push workflow from CLAUDE.md). Mention the snapshot path so the
user can look at the raw log.
