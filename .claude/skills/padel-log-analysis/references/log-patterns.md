# Padel Booking — log reference

Log format: `YYYY-MM-DD HH:MM:SS  LEVEL     message`. Times are Europe/Amsterdam.
Lines prefixed `[meetandplay]` / `[playtomic]` come from a provider subprocess.

## Contents
1. Run lifecycle (what a healthy run looks like)
2. Message catalogue: meaning and action
3. Known pitfalls when reading the log

---

## 1. Run lifecycle

**Smart mode** (`scheduler.py`, runs continuously):

```
Club-schema (<generated_at>): N club(s), M met bekend vrijgavemoment   ← startup / every 6h refresh
  <club> — rollend|dagelijks, N dagen vooruit[, om HH:00 …]
Run gestart: startrun                     ← once per addon start (so: count = restarts)
Run gestart: nachtelijke volledige run    ← daily 00:01, preceded by fetch_bookings output
Run gestart: vrijgave <club> (<strategy>, N d); …   ← targeted run at a release moment
Run van HH:MM wordt ingehaald (N min te laat)        ← ≥ 2.7.1: run fell due while another was busy
Run gestart: ingehaalde volledige run (gemist: …) | ingehaald (…): …   ← ≥ 2.7.1: missed runs merged
```

**Since v2.9.0** a targeted run starts 2 min before the release (`PREPARE_AHEAD`): `Run gestart:
vrijgave … — vrijgave om HH:MM:SS` at `HH:58:00`, then `Klaar voor vrijgave om …: N clubdag(en) open,
X s wachten`, then 90 s of refreshes every ~2 s. It ends with `Slot gezien bij <club>, +N s na vrijgave`
or `Geen slot binnen 90 s na vrijgave (N verversronde(s))`. If nothing was booked it runs once more at
release +5 min, so release runs come in pairs (`HH:58:00`, `HH:05:00`). The `Slot gezien … +N s` delay is
the measure of how fast the bot was. If the available start times in the first round (just before
the release) already lack the window slots, and so does the last round, then the slot never came
free for guests or someone else was faster. A prep that ends with `0 s wachten` started too late
(slow login or many tabs).
Before v2.9.0 targeted runs started 5 s after the release and were retried at +1 and +5 min, so they
showed up in triples (`HH:00:05`, `HH:01:05`, `HH:05:05`), with the first look 30–65 s after the release.

**Since v2.9.0** `booking_time_end` is inclusive (the latest start time). Before that, a slot starting
exactly at `time_end` was dropped (`binnen venster: geen` with e.g. `21:00` in the list).

**Nightly mode** (`run.sh`, one run per addon start): `fetch_bookings.py`, then the orchestrator,
with no `Run gestart` lines.

**Orchestrator** (both modes), one run:

```
Padel Booking Orchestrator gestart
Al geboekte datums uit future_bookings: …
skip_booked_dates ingeschakeld — N datum(s) worden overgeslagen
Provider 'meetandplay' ingeschakeld | uitgeschakeld | tijdelijk uitgeschakeld: <reason>
[Gerichte run: <dates> bij <clubs>]
Nog te boeken: <dates>
Provider 'meetandplay' gestart...
  [meetandplay] … search, slot filtering, cart, payment page …
Provider 'meetandplay' meldt succes: <club> op <date>   | meldt geen boeking: <reason>
Booking state opgeslagen / Boekingsgeschiedenis bijgewerkt / HA push notificatie verzonden
Verder zoeken voor: <dates>               ← keeps going for remaining dates
N boeking(en) gevonden … | WARNING Geen boeking gemaakt door alle providers
```

**fetch_bookings** (before every full run): `Meet & Play reserveringen ophalen` →
`N reserveringskaart(en) op 'Aankomend'` → `Reservering gevonden: <club> op <date> om <time> (<status>)`
or `Reservering op <date> overgeslagen (status: Geannuleerd|…)` → `N boeking(en) geschreven naar …`.

## 2. Message catalogue

| Message (substring) | Meaning | Action |
|---|---|---|
| `meldt succes` + `Betaalprovider-URL: …mollie…` | Slot in cart, payment link sent by push | User must pay. Not a booking until it shows as `Betaald` in fetch_bookings |
| `Reservering op X overgeslagen (status: Geannuleerd)` | Reservation lapsed or was cancelled, usually an unpaid payment link | Highlight: the user may think X is booked. Bot will retry X |
| `Geen boeking gemaakt door alle providers` | Nothing suitable free | Normal on its own |
| `Beschikbare starttijden bij <club>: … — binnen venster: geen` | Club has slots, none in the time window | Normal; useful to show "near misses" |
| `<club> overgeslagen: X km (straal N km)` | Own distance filter (v2.7.0) | Explains why a club is never tried |
| `<club> buiten straal: X km (straal N km) — niet gepland` | Scheduler drops the club from the schedule (≥ 2.8.0), so no targeted runs | Normal. Before 2.8.0 such clubs still got (useless) targeted runs |
| `Gerichte run overgeslagen — X al geboekt` | Release run for an already-booked date | Normal |
| `Provider 'playtomic' tijdelijk uitgeschakeld: … (DNS)` | Hard-coded kill switch (`disabled_reason`) | Since v2.6.2 only logged when Playtomic is enabled; before that, noise |
| `Meet & Play sessie verlopen — opnieuw inloggen...` → `Automatisch inloggen gelukt!` | Session cookie expired, re-login OK | Normal |
| `Automatisch inloggen mislukt` / `Fout tijdens automatisch inloggen` / `Geen wachtwoordveld gevonden op SSO pagina` | KNLTB SSO login broken (credentials or page changed) | Serious: no searching possible. Check `debug_login_failed.png` mtime |
| `Meet & Play niet bereikbaar (net::ERR_…) — storing of netwerkprobleem` (≥ 2.8.1) | Site unreachable before any credentials were sent. Before 2.8.1 this showed up as `Fout tijdens automatisch inloggen: Page.goto: net::ERR_…` + "Controleer KNLTB_EMAIL/KNLTB_PASSWORD" | Not a login or block problem. Check from another network (e.g. WebFetch) whether the site is down for everyone; same DNS answer + refusal everywhere = outage. Only DNS differences between host and elsewhere point at a local DNS blocker |
| `Doorgestuurd naar loginpagina tijdens boeking` | Session died mid-booking | Serious if it repeats |
| `Livewire datum-component niet gevonden` / `'Toevoegen…'-knop niet gevonden` / `'Afrekenen'-knop niet gevonden` / `TOS checkbox niet gevonden` / `Betaalknop niet gevonden` | Meet & Play page structure changed | Code fix in `providers/meetandplay/booking.py` likely |
| `Betaalpagina niet bereikt na klik` followed by a Mollie URL on attempt 2 | Retry worked | Noise |
| `Geen betaallink — slot telt niet als geboekt` | Cart reached but no payment URL | Slot not reserved; investigate if it repeats |
| `openstaande reservering in deze sessie` (PendingReservationError) | An earlier unpaid cart blocks a new one; provider opens a fresh session | Fine if followed by success |
| `Geen clubs gevonden met de opgegeven filters` / `0 club(s) gevonden` | Search filters/radius/city wrong, or search page broken | Check options; serious if sudden |
| `Geen clubcoördinaten op de zoekpagina` | Distance filter can't work | Code fix |
| `Run duurde langer dan 15 minuten en is afgebroken` | Orchestrator killed | Check what hung (browser wait loops) |
| `Run mislukt` + traceback | Scheduler-level exception, but the scheduler keeps running | Read traceback |
| `Club-schema ophalen mislukt (…) — cache gebruiken` | GitHub raw fetch failed | Fine short-term; check `generated_at` |
| `Geen club-schema beschikbaar` | No cache either, so only nightly runs | Serious in smart mode |
| `HA push notificatie mislukt` | User never gets the payment link | Serious: check `ha_notify_device_id` |
| `HA push notificatie verzonden: Betaal vóór HH:MM: …` (≥ 2.10.0; before: `Padelbaan beschikbaar!`) | HA accepted the push (HTTP 200). The deadline is sent time + 15 min (iDEAL via Mollie) | Delivery to the phone isn't visible here. If the user saw nothing, test from Developer Tools (README) |
| `Herinnering gepland om HH:MM …` → `… nog niet betaald — herinnering sturen` / `… is betaald — geen herinnering` / `Betaaltermijn … al verlopen` | Reminder 8 min after the link (≥ 2.10.0), after a fresh `fetch_bookings` | `nog niet betaald` + later `Geannuleerd` = the user missed both pushes |
| `Betaalherinnering mislukt — openstaande herinneringen gewist` | Unreadable `pending_payments.json` | Code bug: read the traceback |
| `Ophalen mislukt voor … — future_bookings.json niet bijgewerkt` | Account fetch failed; skip list is stale | May cause double bookings or skipped dates |

## 3. Pitfalls

- **Provider timestamps are relay times.** The orchestrator collects the provider's stderr
  and logs it all when the subprocess exits, so every `[meetandplay]` line of one run has the
  same timestamp (when the provider finished). Don't read durations from them; use
  `Provider … gestart` → `meldt …`.
- **Collisions between runs.** The scheduler is single-threaded: while a run and its retries
  (up to ~+6 min) execute, other due runs wait. **Before v2.7.1** anything more than 2 min late
  was silently dropped. Seen in practice: a targeted 00:00 release run with retries swallowed
  the 00:01 nightly run, including that night's `fetch_bookings`. **Since v2.7.1** missed runs
  are caught up right afterwards (`Run van HH:MM wordt ingehaald (N min te laat)`), and several
  missed runs become one run with reason `ingehaalde volledige run (gemist: …)` or
  `ingehaald (…): …`. A day without a nightly run on ≥ 2.7.1 is a new bug; check
  `run_forever`/`next_due` in `scheduler.py`.
- **`.booking_state.json` and `booking_history.json` record claims, not bookings.** They are
  written on `meldt succes`. Since v2.6.0 only `future_bookings.json` counts as "booked".
- **Unpaid reservations are held 15 minutes** (verified 2026-10-29). While pending they show under
  "Aankomend" as `In afwachting van betaling`, with an order page (`/mijn-aankopen/order/<club>/<uuid>`):
  "De reservering wordt tot HH:MM vastgehouden" plus **Betaling starten**, from any logged-in device.
  Pressing it again opens a new Mollie page for the same order, but the hold time doesn't move.
  Then `Betaald` or `Geannuleerd`. `fetch_bookings` keeps pending ones in `future_bookings.json` (status
  `in afwachting van betaling`), so that date counts as booked until it lapses. Use
  `meetandplay.nl/mijn-aankopen`: the `www.` variant redirects to the homepage.
- **Payment link type.** `Betaalprovider-URL` on `mollie.com` (verified: opens without any session)
  or `pay.ideal.nl` is payable from the phone. Since ≥ 2.11.0 the push also carries the order page
  (`Reservering in je account: …, vastgehouden tot HH:MM`) as an "Open in Meet & Play" button. A
  `meetandplay.nl/club/…/reserveren` link or a club's own cart (e.g. `boeken.terwegen.nl/nl/cart/`)
  most likely only works in the bot's session; there the order page is the way to pay.
- **`last_run.json` `success: false`** just means the latest run booked nothing. Normal most of
  the time.
- **Duplicate claims for the same date** (e.g. two successes for one date hours apart) mean
  the first payment link lapsed and the bot booked again. The user may have received
  several payment pushes.
- **Config changes mid-window** show as different `Config geladen: …` lines; outcomes before
  and after aren't comparable.
- **Log retention:** `ha apps logs` reads the host journal, which survives addon restarts and
  updates. The window is limited by `-n` and journal size, so for older dates raise `-n` first.
