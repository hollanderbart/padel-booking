# Padel Booking

Zoekt automatisch naar beschikbare padelbanen op [meetandplay.nl](https://www.meetandplay.nl) en [Playtomic](https://playtomic.io). Bij een beschikbaar slot ontvang je een push notificatie met een directe link naar de club, zodat je de boeking en betaling zelf handmatig kunt afronden.

## Hoe het werkt

Een **orchestrator** coördineert meerdere providers die parallel draaien als losse subprocessen. De eerste provider die een boeking succesvol afrondt wint; de andere wordt gestopt.

**Meet & Play provider** (browser automatisering via Playwright):
1. Logt automatisch in via KNLTB ID SSO
2. Zoekt beschikbare clubs in de regio op de komende weken (één of meer configureerbare weekdagen)
3. Filtert op binnenbaan, avond, gewenste duur en speltype
4. Boekt het eerste beschikbare tijdslot en navigeert naar de betalingspagina

**Playtomic provider** (REST API — geen browser nodig):
1. Logt in via de Playtomic API met email/wachtwoord
2. Zoekt clubs in de buurt via coördinaten, gesorteerd op afstand (dichtstbijzijnde eerst)
3. Haalt beschikbaarheid op en stuurt een notificatie met de club-URL (`https://app.playtomic.io/clubs/{id}`) zodra een passend slot gevonden is

De boeking en betaling doe je zelf handmatig via de link in de notificatie. Er worden alleen notificaties gestuurd als er een beschikbaar slot gevonden wordt.

**Alleen datums zonder boeking**: vóór elke volledige run haalt de addon je reserveringen op uit je
Meet & Play account ([mijn-aankopen](https://meetandplay.nl/mijn-aankopen), tabblad "Aankomend") en,
als die provider aan staat, uit Playtomic. Alleen datums waarop je in je account al een baan hebt
worden overgeslagen. Een slot dat de addon eerder vond maar dat je niet betaald hebt, telt niet: die datum
wordt opnieuw gezocht. Een run probeert **alle** overgebleven datums te boeken; per gevonden slot krijg je
een notificatie met betaallink (iDEAL of de betaalpagina van de club). Komt de addon niet tot de
betaalpagina, dan telt het slot niet als gevonden en zoekt hij verder — de winkelwagen zit namelijk alleen
in de browsersessie van de addon, dus een link naar meetandplay.nl zou voor jou leeg zijn.

---

## Installatie als Home Assistant addon

### Vereisten

- Home Assistant OS of Supervised
- [HA Companion app](https://companion.home-assistant.io/) op je telefoon voor push notificaties
- Account op meetandplay.nl en/of Playtomic

### Stap 1 — Voeg de custom repository toe

1. Ga naar **Settings → Add-ons → Add-on store**
2. Klik op **⋮ (drie puntjes) → Repositories**
3. Voeg toe: `https://github.com/hollanderbart/padel-booking`
4. Sluit het venster en ververs de pagina
5. De addon **Padel Booking** verschijnt onderaan de store

### Stap 2 — Installeer de addon

Klik op **Padel Booking → Install**. De installatie duurt even omdat Playwright en Chromium worden gedownload (~500 MB).

### Stap 3 — Configureer de addon

Ga naar **Settings → Add-ons → Padel Booking → Configuration** en vul in:

| Veld | Beschrijving |
|---|---|
| `knltb_email` | E-mailadres van je KNLTB/meetandplay account |
| `knltb_password` | Wachtwoord van je KNLTB account |
| `ha_notify_device_id` | Device ID voor push notificaties (optioneel) |
| `location_city` | Stad om clubs in te zoeken (bijv. `Boskoop`) |
| `location_radius_km` | Zoekradius in kilometers (hemelsbreed vanaf `location_latitude`/`location_longitude`). Elke waarde werkt, bijv. 15: Meet & Play zoekt met de eerstvolgende stap uit de dropdown (5, 10, 20, 50 … km) en de addon laat clubs verder dan je straal zelf vallen |
| `location_latitude` | Breedtegraad voor Playtomic zoeken (bijv. `52.07`) |
| `location_longitude` | Lengtegraad voor Playtomic zoeken (bijv. `4.66`) |
| `booking_days` | Eén of meer weekdagen om te boeken, bijv. `thursday` of `tuesday` + `thursday` (standaard `thursday`). Vervangt `booking_day` uit versies vóór 2.5.0 — na de update staat `booking_days` op `thursday`; controleer dit als je een andere dag had |
| `booking_time_start` | Vroegste starttijd (bijv. `19:30`) |
| `booking_time_end` | Laatste starttijd, inclusief: met `21:00` telt een slot van 21:00 ook mee (bijv. `21:00`) |
| `duration_minutes` | Duur van de boeking: `60` of `90` minuten |
| `court_type` | Baantype: `indoor` of `outdoor` |
| `game_type` | Speltype: `double` (4 spelers) of `single` (2 spelers) |
| `weeks_ahead` | Aantal weken vooruit zoeken, per gekozen weekdag (standaard `5`, zodat ook clubs met een venster van ~30 dagen op hun vrijgavemoment geboekt worden) |
| `meetandplay_enabled` | Meet & Play provider in/uitschakelen (standaard `true`) |
| `schedule_mode` | `smart` (standaard): addon draait continu en boekt op het vrijgavemoment per club. `nightly`: één run per start van de addon (oude gedrag) |
| `playtomic_enabled` | Playtomic provider in/uitschakelen (standaard `false`). **Tijdelijk genegeerd** — Playtomic staat uit sinds v2.3.1, zie Troubleshooting |
| `fetch_bookings_enabled` | Vóór elke volledige run je reserveringen ophalen uit je account en opslaan naar `/config/padel/future_bookings.json` (standaard `true`) |
| `skip_booked_dates` | Sla datums over waarop je al een reservering hebt (uit `future_bookings.json`) (standaard `true`). Zet dit én `fetch_bookings_enabled` aan om alleen op vrije datums te boeken |
| `playtomic_email` | E-mailadres van je Playtomic account (vereist als Playtomic ingeschakeld) |
| `playtomic_password` | Wachtwoord van je Playtomic account (vereist als Playtomic ingeschakeld) |

**`ha_notify_device_id`** vind je via:
**Developer Tools → Actions → zoek op `notify.mobile_app`**

De naam van de service is bijv. `notify.mobile_app_mijn_telefoon` → device ID is `mijn_telefoon`.

**Playtomic coördinaten** vind je eenvoudig via [maps.google.com](https://maps.google.com) — klik rechts op je locatie en kopieer de coördinaten.

### Stap 4 — Test handmatig

Ga naar **Settings → Add-ons → Padel Booking → Start** en bekijk de **Log** tab. Een succesvolle run ziet er zo uit:

```
INFO  Padel Booking Orchestrator gestart
INFO  Provider 'meetandplay' ingeschakeld
INFO  Provider 'meetandplay' gestart...
INFO  Automatisch inloggen gelukt!
INFO  8 club(s) gevonden na filteren
INFO  Tijdslot gevonden: Sportcentrum Boskoop om 19:30 (baan: Padelbaan 1)
INFO  Winkelwagen bereikt
INFO  HA push notificatie verzonden: Padelbaan beschikbaar!
```

### Stap 5 — Automatisering instellen

**`schedule_mode: smart` (standaard)**: de addon blijft draaien en plant zelf de runs (zie
[Wanneer komen banen vrij?](#wanneer-komen-banen-vrij-booking-research)). Een automation is niet nodig,
maar wel handig als vangnet: als de addon gestopt is, start de automation hem weer. Is hij al
actief, dan doet `addon_start` niets.

**`schedule_mode: nightly`**: de addon doet één run per start. Voeg dan een automation toe via
**Settings → Automations → + Create automation**:

- **Trigger**: Time → `00:00:30`
- **Action**: Call service → `hassio.addon_start` → `addon: local_padel_booking`

> De slug hangt af van de installatie: via de GitHub-repository is het `<hash>_padel_booking`
> (bijv. `a30a93bf_padel_booking`), alleen bij een lokale installatie `local_padel_booking`.
> Zie [Logs en status uitlezen via SSH](#logs-en-status-uitlezen-via-ssh) om de slug op te zoeken.

Of voeg dit toe aan `automations.yaml`:

```yaml
- alias: "Padel booking — dagelijkse run"
  trigger:
    - platform: time
      at: "00:00:30"
  action:
    - service: hassio.addon_start
      data:
        addon: local_padel_booking
```

---

## Toekomstige boekingen in Home Assistant

Vóór elke volledige run (bij het starten van de addon en om 00:01) haalt de addon live je toekomstige reserveringen op (Meet & Play via [mijn-aankopen](https://meetandplay.nl/mijn-aankopen), Playtomic via de API als die provider aan staat) en schrijft ze naar `/config/padel/future_bookings.json`. De booker slaat die datums over (`skip_booked_dates`), en je kunt ze weergeven in een Lovelace dashboard. Meet & Play toont op de kaarten geen jaar en laat net verstreken reserveringen nog even onder "Aankomend" staan; de addon neemt het jaar dat het dichtst bij vandaag ligt en laat verstreken reserveringen weg (`Reservering op … overgeslagen (verstreken)`).

Lukt het ophalen niet (bijv. inloggen mislukt), dan blijft het bestaande bestand staan — anders zou de booker
datums die al geboekt zijn opnieuw proberen. Alleen dit bestand bepaalt welke datums als geboekt
gelden; `booking_history.json` is puur een logboek. Betaal je een gevonden slot, dan staat die datum na de
eerstvolgende volledige run (00:01 of herstart) in `future_bookings.json`. Tot die tijd kan een gerichte run
op een vrijgavemoment voor dezelfde datum nog een tweede betaallink sturen — die kun je negeren.

### Stap 1 — Voeg de sensor toe aan `configuration.yaml`

```yaml
command_line:
  - sensor:
      name: Padel Future Bookings
      unique_id: padel_future_bookings
      command: "cat /config/padel/future_bookings.json 2>/dev/null || echo '[]'"
      value_template: "{{ value_json | length }} aankomende boeking(en)"
      json_attributes_template: >
        {{ {"bookings": value_json} | tojson }}
      scan_interval: 300
```

Herstart daarna Home Assistant (of ga naar **Developer Tools → YAML → Reload command_line entities**).

### Stap 2 — Voeg de Lovelace card toe

```yaml
type: markdown
title: Aankomende padel boekingen
content: >
  {% set bookings = state_attr('sensor.padel_future_bookings', 'bookings') %}
  {% if bookings and bookings | length > 0 %}
  | Datum | Club | Baan | Tijd | Via |
  |-------|------|------|------|-----|
  {% for b in bookings %}
  | {{ b.booked_date }} | {{ b.club_name }} | {{ b.court_name }} | {{ b.time_range }} | {{ b.provider }} |
  {% endfor %}
  {% else %}
  *Geen aankomende boekingen gevonden.*
  {% endif %}
```

### Formaat van `future_bookings.json`

```json
[
  {
    "booked_date": "2026-04-10",
    "provider": "meetandplay",
    "club_name": "Sportcentrum Boskoop",
    "club_address": "",
    "court_name": "Padelbaan 2",
    "time_range": "19:30 - 21:00",
    "payment_url": "https://meetandplay.nl/mijn-aankopen/order/...",
    "status": "betaald"
  },
  {
    "booked_date": "2026-04-17",
    "provider": "playtomic",
    "club_name": "Padelclub X",
    "club_address": "...",
    "court_name": "Padelbaan",
    "time_range": "20:00 - 21:30",
    "payment_url": "https://playtomic.io/booking/...",
    "status": "confirmed"
  }
]
```

---

## Boekingsgeschiedenis in Home Assistant

Na elke succesvolle boeking schrijft het script een entry naar `/config/padel/booking_history.json`. Je kunt de geschiedenis weergeven in een Lovelace dashboard via een `command_line` sensor.

### Stap 1 — Voeg de sensor toe aan `configuration.yaml`

```yaml
command_line:
  - sensor:
      name: Padel Booking History
      unique_id: padel_booking_history
      command: "cat /config/padel/booking_history.json 2>/dev/null || echo '[]'"
      value_template: "{{ value_json | length }} boeking(en)"
      json_attributes_template: >
        {{ {"bookings": value_json} | tojson }}
      scan_interval: 300
```

Herstart daarna Home Assistant (of ga naar **Developer Tools → YAML → Reload command_line entities**).

### Stap 2 — Voeg de Lovelace markdown card toe

```yaml
type: markdown
title: Padel Boekingen
content: >
  {% set bookings = state_attr('sensor.padel_booking_history', 'bookings') %}
  {% if bookings %}
  | Datum | Geboekt op | Club | Baan | Tijd | Via |
  |-------|-----------|------|------|------|-----|
  {% for b in bookings %}
  | {{ b.booked_date }} | {{ b.booked_at[:16] | replace('T', ' ') }} | {{ b.club_name }} | {{ b.court_name }} | {{ b.time_range }} | {{ b.provider }} |
  {% endfor %}
  {% else %}
  *Nog geen boekingen.*
  {% endif %}
```

### Formaat van `booking_history.json`

```json
[
  {
    "booked_date": "2026-04-03",
    "booked_at": "2026-03-26T00:01:47",
    "provider": "meetandplay",
    "club_name": "Sportcentrum Boskoop",
    "club_address": "Koningin Julianaplein 1, Boskoop",
    "court_name": "Padelbaan 2",
    "time_range": "19:30 - 21:00 90 minuten",
    "payment_url": "https://..."
  }
]
```

Het bestand bevat maximaal 20 entries. De nieuwste boeking staat bovenaan.

---

## Boeking resetten (opnieuw zoeken forceren)

Niet meer nodig sinds v2.6.0: alleen reserveringen in je account tellen als geboekt. Een gevonden maar niet
betaald slot wordt bij de volgende run vanzelf opnieuw gezocht. Had je de `padel_reset_booking`
shell_command uit een eerdere versie, dan kun je die verwijderen.

---

## Wanneer komen banen vrij? (booking research)

De addon draait standaard één keer per nacht (00:01). Dat werkt alleen goed voor clubs die
hun banen om middernacht vrijgeven. Clubs hanteren verschillende **release-strategieën**:

| Strategie | Hoe het werkt | Beste moment om te boeken |
|---|---|---|
| **Rollend (exact tijdstip)** | Slot T komt vrij op T − N dagen, bijv. de baan van 20:00 komt 7 dagen eerder om 20:00 vrij | Precies N dagen vóór het slot, op de starttijd |
| **Dagelijks vast uur** | Elke dag om uur H (vaak 00:00 of 07:00) komt de dag vandaag + N in zijn geheel vrij | Dagelijks om H |
| **Blok (wekelijks/periodiek)** | Een hele periode komt in één keer vrij, bijv. elke maandag de volgende week | Op het wekelijkse vrijgavemoment |
| **Leden vs. gasten** | Leden mogen verder vooruit boeken dan gasten (Meet & Play = gast) | Volgens het gastenvenster |
| **Annuleringen** | Slots komen terug vlak vóór de gratis-annuleringsgrens (bijv. 24 uur vooraf) of na een verlopen winkelwagen | Pollen rond die grens |

De workflow [`.github/workflows/booking-research.yml`](.github/workflows/booking-research.yml)
meet zes keer per dag, voor elke Meet & Play club binnen de straal uit `config.yaml`, wat de
verste dag met een boekbaar padel-slot is en welke starttijden die dag open staan (uit de
"Tijdstip"-kiezer op de clubpagina).
`research/booking_windows.py` leidt daaruit per club de strategie, het venster (N dagen) en
het vrijgave-uur af. Het resultaat staat op de branch
[`booking-research`](https://github.com/hollanderbart/padel-booking/blob/booking-research/BOOKING_WINDOWS.md) (`BOOKING_WINDOWS.md`)
en in de samenvatting van elke workflow-run.

- Meting gebeurt als niet-ingelogde bezoeker (het gastenvenster), niet als clublid.
- Een volgeboekte dag lijkt op een dag die nog niet open is; daarom telt de meerderheid
  over meerdere metingen. Reken op een paar dagen data voor een betrouwbaar beeld.
- Handmatig starten: **Actions → Booking research → Run workflow**. Met `snapshot` aangevinkt
  wordt per club de pagina-HTML als artifact bewaard (handig als Meet & Play de pagina wijzigt).
- Lokaal: `python -m research.booking_windows --data-dir research-data --max-clubs 3`.

### Boeken op het vrijgavemoment (`schedule_mode: smart`)

`scheduler.py` haalt elke 6 uur `club_schedule.json` op van de `booking-research` branch (met een
cache in `/config/padel/club_schedule.json`) en plant voor elke doeldag (alle `booking_days`) binnen `weeks_ahead`:

- **rollende clubs**: een run N dagen vóór de doeldag op elke starttijd binnen het tijdvenster,
  inclusief `booking_time_end` (bijv. 19:30, 20:00, 20:30 en 21:00)
- **dagelijkse clubs**: een run N dagen vóór de doeldag op het vrijgave-uur
- **altijd**: een volledige run om 00:01 (alle clubs, alle open datums) als vangnet, en bij het starten van de addon.
  Vóór een volledige run worden eerst je reserveringen opgehaald; gerichte runs doen dat niet, zodat ze
  precies op het vrijgavemoment starten (ze gebruiken de reserveringen van de laatste volledige run)

Een gerichte run start **2 minuten vóór** het vrijgavemoment. In die tijd logt de booker in (de sessie
is dan meestal verlopen) en opent hij elke clubdag met filters en datum in een eigen tabblad. Vanaf 3 seconden
vóór het moment ververst hij die dagen elke ~2 seconden (Livewire `$refresh`, zonder de pagina te herladen),
90 seconden lang. Een slot dat vrijkomt ligt zo binnen enkele seconden in de winkelwagen; voorheen startte de
run pas op het moment zelf en keek hij pas na 30–65 seconden (browser starten, inloggen, pagina laden), als
anderen de avondslots al hadden. Was er niets, dan volgt na 5 minuten nog een gewone poging. In de log:

```
Run gestart: vrijgave Hal 0172 (rollend, 14 d) — vrijgave om 20:00:00
[meetandplay]  Klaar voor vrijgave om 20:00:00: 1 clubdag(en) open, 41 s wachten
[meetandplay]  Slot gezien bij Hal 0172, +1.8 s na vrijgave (ronde 3)
[meetandplay]  Geen slot binnen 90 s na vrijgave (42 verversronde(s))   ← als er niets kwam
```

De starttijden worden alleen in de eerste en laatste verversronde gelogd (met `--debug` elke ronde). Runs draaien na elkaar: valt een run terwijl een andere nog bezig is (bijv. de
nachtrun van 00:01 tijdens een gerichte run voor 00:00 met retry), dan wordt hij direct daarna ingehaald
(`Run van … wordt ingehaald` in de log). Zijn er meerdere gemist, dan worden ze samengevoegd tot één run.
Zit er een volledige run tussen, dan wordt het één volledige run. Clubs met strategie "onvoldoende data" of "blok" worden alleen door de
nachtelijke run gedekt. Let op: een club met een venster groter dan `weeks_ahead` weken (bijv. 30 dagen
bij `weeks_ahead: 4`) geeft zijn banen vrij vóórdat de doeldag binnen het zoekbereik valt; met de
standaard `weeks_ahead: 5` vallen vensters tot 35 dagen binnen bereik.

Bekijk de geplande runs:

```bash
python scheduler.py --plan                                  # met het schema van GitHub
python scheduler.py --plan --schedule-file club_schedule.json
```

## Push notificaties

Bij een beschikbaar slot ontvang je een push notificatie ("Padelbaan beschikbaar!") met:
- Baannaam en exacte tijdstip
- Clubnaam en adres
- Een directe link naar de clubpagina om zelf te boeken (tik op de notificatie om te openen)

Er worden **geen** notificaties gestuurd als er geen baan gevonden wordt.

---

## Lokaal testen (zonder HA)

```bash
# Vereisten installeren
pip install -r requirements.txt
playwright install chromium --with-deps

# .env aanmaken
cp padel_booking/.env.example .env
# Vul KNLTB_EMAIL, KNLTB_PASSWORD (en optioneel PLAYTOMIC_EMAIL, PLAYTOMIC_PASSWORD) in

# Uitvoeren
python orchestrator.py

# Met debug logging
python orchestrator.py --debug

# Dry-run: zoekt slots maar boekt niet echt
python orchestrator.py --dry-run

# Gerichte run: alleen deze datum(s) bij deze club(s), zonder zoekpagina
python orchestrator.py --dry-run --target '{"dates": ["2026-10-22"], "clubs": [{"name": "Sportcentrum midden Boskoop", "url": "https://meetandplay.nl/club/88093", "address": "Sportlaan 1, Boskoop"}]}'

# Individuele provider testen
echo '{"booking_request":{"location":{"city":"Boskoop","radius_km":20},"days":["thursday"],"time_start":"19:30","time_end":"21:00","duration_minutes":90,"court_type":"indoor","game_type":"double","weeks_ahead":4},"credentials":{"email":"...","password":"..."},"provider_config":{"cookies_file":".meetandplay_cookies.json"},"dry_run":true}' | python -m providers.meetandplay.provider
```

### Unit tests uitvoeren

```bash
# Alle unit tests (geen credentials of browser nodig)
pytest tests/ -v

# Inclusief de integratie test (vereist KNLTB_EMAIL en KNLTB_PASSWORD in .env)
pytest tests/ test_integration.py -v
```

De `tests/` map bevat 262 tests voor alle modules:
- `test_orchestrator.py` — open datums, al geboekte datums, alle open datums boeken, provider subprocess, first-wins logica, provider-selectie
- `test_orchestrator_files.py` — boekingsgeschiedenis en `last_run.json`
- `test_base.py` — ProviderResult JSON contract, gedeelde datum/tijd-helpers, provider entry point
- `test_notify.py` — HA push, macOS, console notificaties
- `test_playtomic_client.py` — REST API client, token caching, auth, toekomstige boekingen
- `test_playtomic_booking.py` — slot zoeken, afstandssortering, boekingsflow
- `test_meetandplay_booking.py` — slot filters, login, checkout-flow, Playwright mocks
- `test_session.py` — cookie/sessie beheer, automatisch inloggen
- `test_fetch_bookings.py` — datum-parsing en omzetten van toekomstige boekingen
- `test_research.py` — herkennen van release-strategieën met gesimuleerde clubs
- `test_scheduler.py` — plannen van runs op vrijgavemomenten

### Lint

```bash
pip install ruff
ruff check .
```

CI (`.github/workflows/test.yml`) draait bij elke push naar `main`: `ruff check .`, een controle
of `padel_booking/` gesynchroniseerd is met de bronbestanden, en alle unit tests.
De integratietest draait alleen als de repository-secrets `KNLTB_EMAIL` en `KNLTB_PASSWORD` zijn ingesteld.

### Via Docker

```bash
docker build -f padel_booking/Dockerfile -t padel-booking .
docker run --rm -v $(pwd)/.env:/app/.env padel-booking
```

---

## Upgraden van v1.x naar v2.0

Bij de upgrade van v1 (knltb_padel_booking) naar v2 (padel_booking) moet je:

1. De **oude addon verwijderen** in HA (Settings → Add-ons → KNLTB Padel Booking → Uninstall)
2. De **nieuwe repository-URL** toevoegen: `https://github.com/hollanderbart/padel-booking`
3. De nieuwe addon **Padel Booking** installeren en configureren
4. De **automation aanpassen**: `addon: local_knltb_padel_booking` → `addon: local_padel_booking`
5. Bestanden verplaatsen: `/config/knltb/` → `/config/padel/` (optioneel, voor historieoverdracht)

---

## Troubleshooting

### Login mislukt (Meet & Play)

1. Controleer `knltb_email` en `knltb_password` in de Configuration tab
2. Bekijk de debug screenshot via **File Editor**: `/config/padel/debug_login_failed.png`

### Meet & Play niet bereikbaar

```
Meet & Play niet bereikbaar (net::ERR_CONNECTION_REFUSED) — storing of netwerkprobleem, geen inlogprobleem
```

De site gaf geen verbinding, dus het inloggen is niet eens begonnen. Je credentials zijn niet het probleem.
Opent meetandplay.nl ook in je browser niet, dan is het een storing bij Meet & Play: de addon probeert het
bij de volgende geplande run vanzelf opnieuw. Werkt de site in je browser wel, kijk dan naar het netwerk van
je HA host, bijv. een DNS-blocker (AdGuard, Pi-hole) die `meetandplay.nl` blokkeert (`ERR_NAME_NOT_RESOLVED`
of een verbinding naar `0.0.0.0`). Sinds v2.8.1 overschrijft zo'n storing `debug_login_failed.png` niet meer.

### Login mislukt (Playtomic)

- Controleer `playtomic_email` en `playtomic_password`
- Als je Playtomic account via Google/Apple SSO is aangemaakt, stel dan eerst een wachtwoord in via "Wachtwoord vergeten" op playtomic.io
- Bekend probleem (oktober 2026): de API-host `api.playtomic.io` heeft geen DNS-record meer. Fouten als
  `Could not resolve host` / `NameResolutionError` in de log komen daardoor. Daarom is de Playtomic provider
  sinds v2.3.1 tijdelijk uitgeschakeld (`disabled_reason` in `orchestrator.py`), ook als `playtomic_enabled` aan staat.
  Sinds v2.6.2 verschijnt de waarschuwing hierover alleen nog als je `playtomic_enabled` aanzet

### Script vindt geen banen terwijl er wel slots beschikbaar zijn

Elk overgeslagen slot wordt nu gelogd met de reden. Zoek in de HA log naar regels als:

```
Slot 3 overgeslagen: tijd 19:30 buiten venster 20:00–21:00 (label: 'Dubbelspel binnenbaan')
Slot 5 overgeslagen: duur 60 min ≠ gewenste 90 min (tijd: 19:30, label: '')
Slot 7 overgeslagen: buitenbaan ('Buitenbaan dubbelspel')
```

Sinds v2.3.4 logt de booker per club ook welke starttijden beschikbaar zijn en welke binnen het venster vallen:

```
Beschikbare starttijden bij Sportcentrum midden Boskoop: 18:00, 19:30, 21:30 — binnen venster: 19:30
```

Mogelijke oorzaken:
- **Versie ouder dan v2.3.4**: Meet & Play toont per keer alleen de slots van één starttijd; oudere versies keken alleen naar de eerste tijd
- **Tijdvenster te smal**: het slot valt net buiten `booking_time_start`–`booking_time_end` (beide inclusief; vóór v2.9.0 telde een slot precies op `booking_time_end` niet mee)
- **Duur mismatch**: de site toont bijv. "60 min" maar `duration_minutes` staat op `90`
- **Buitenbaan filter**: het label van het slot bevat "buiten" terwijl `court_type=indoor`
- **Enkelspel filter**: bij `game_type=double` worden banen overgeslagen met "enkelspel" in het label of "single" / "max. 2 spelers" in de baannaam

### Meerdere boekingen achter elkaar (Meet & Play)

Meet & Play staat per sessie één openstaande (nog niet betaalde) reservering toe. Vindt de addon in één
run meerdere datums, dan verschijnt bij de tweede de pop-up "Je reservering bij …". De addon kiest
"Annuleren" en logt opnieuw in voor een nieuwe sessie, zodat de eerste betaallink geldig blijft. In de log:
`Openstaande reservering in deze sessie — pop-up annuleren` gevolgd door `Nieuwe sessie starten`.

### Club ontbreekt of wordt overgeslagen vanwege afstand (Meet & Play)

De addon rekent per club de hemelsbrede afstand uit vanaf `location_latitude`/`location_longitude` (standaard
Boskoop) en slaat clubs buiten `location_radius_km` over:

```
TERWEGEN INDOOR overgeslagen: 18.9 km (straal 15 km)
```

De coördinaten komen van de zoekpagina van Meet & Play, of van de clubpagina als een club daar ontbreekt, en
worden bewaard in `/config/padel/.meetandplay_club_coords.json` zodat ook de gerichte runs op het
vrijgavemoment filteren. Rijafstand kan flink afwijken van hemelsbreed; zet de straal daarop af.

In `schedule_mode: smart` gebruikt de planner hetzelfde bestand: voor clubs buiten de straal worden geen
gerichte runs meer gepland (bij het starten en bij elke verversing van het club-schema, elke 6 uur):

```
TERWEGEN INDOOR buiten straal: 18.9 km (straal 14 km) — niet gepland
```

Een club waarvan de locatie nog niet bekend is, blijft ingepland. De eerste run die hem bezoekt slaat de
coördinaten op, en vanaf de volgende verversing valt hij af als hij te ver weg ligt.

### Notificatie zonder betaallink / "Geen betaallink — slot telt niet als geboekt" (Meet & Play)

Na "Betaling starten" moet de addon op de betaalpagina uitkomen (iDEAL of de boekingssite van de club).
Blijft hij op `meetandplay.nl/club/<id>/reserveren` staan, dan probeert hij het nog één keer en anders telt
het slot niet als gevonden (de winkelwagen zit alleen in de sessie van de addon, dus jij kunt er niet bij).
Hij zoekt dan verder bij de volgende club. Ter diagnose bewaart de addon een screenshot en de HTML van die
pagina:

```bash
$SSH "ls -l /config/padel/debug_payment_failed.*"
scp -i ~/.ssh/id_ed25519_ha root@homeassistant.local:/config/padel/debug_payment_failed.png .
```

### Script vindt geen banen

- Er zijn geen banen beschikbaar in het opgegeven tijdvenster
- Verbreed het tijdvenster of vergroot `location_radius_km`
- Schakel beide providers in voor meer kans op een beschikbare baan

### Push notificaties komen niet aan

- Controleer `ha_notify_device_id` in de Configuration tab
- Zoek de juiste device ID via **Developer Tools → Actions → `notify.mobile_app`**

### Logs en status uitlezen via SSH

De HA MCP-server (Assist) geeft geen toegang tot addon-logs. Via SSH kan dat wel, ook vanaf je
eigen machine (bijv. voor Claude Code):

1. Maak een SSH-sleutel zonder wachtwoord (zodat scripts hem kunnen gebruiken):
   ```bash
   ssh-keygen -t ed25519 -C "jij@example.com" -f ~/.ssh/id_ed25519_ha -N ""
   cat ~/.ssh/id_ed25519_ha.pub
   ```
2. Installeer de **Terminal & SSH** addon (Settings → Add-ons → Add-on Store)
3. Op de **Configuration** tab:
   - plak de publieke sleutel in `authorized_keys` — in de formulierweergave alléén de sleutelregel, zonder `- ` ervoor
   - vul bij **Network** poort `22` in; die kaart heeft een **eigen Save-knop**
   - is de Save-knop grijs, dan klopt de configuratie niet (bijv. een afgebroken sleutelregel)
4. **Herstart** de SSH addon. Pas daarna luistert sshd (zonder poort luistert hij helemaal niet:
   `Connection refused`)

Daarna:

```bash
SSH="ssh -i ~/.ssh/id_ed25519_ha -o IdentitiesOnly=yes root@homeassistant.local"

# Slug opzoeken — bij installatie via de GitHub-repository is dat <hash>_padel_booking,
# alleen bij een lokale installatie is het local_padel_booking
$SSH "ha apps list"

# Logs van de laatste (of lopende) run
$SSH "ha apps logs <slug>"

# Status, versie en opties (wachtwoorden weggefilterd); de host heeft geen python3, wel jq
$SSH "ha apps info <slug> --raw-json | jq '.data | {state, boot, watchdog, version, options: (.options | with_entries(select(.key|test(\"pass\")|not)))}'"

# State-bestanden
$SSH "cat /config/padel/last_run.json /config/padel/booking_history.json"
```

Op oudere HA-versies heet het commando `ha addons` in plaats van `ha apps`.

Let op: in `schedule_mode: smart` hoort de addon **continu** te draaien (`state: started`). Staat hij op
`stopped`, dan worden de vrijgavemomenten gemist. Na een update van een gestopte addon start HA hem niet
vanzelf; start hem dan handmatig (`ha apps start <slug>`) en zet eventueel de watchdog aan.

### Log-analyse met Claude Code

De repo bevat een Claude Code skill (`.claude/skills/padel-log-analysis/`) die bovenstaande
SSH-toegang gebruikt voor een complete gezondheidscheck. Vraag in Claude Code bijvoorbeeld
*"hoe gaat het met de padel booker?"* of *"waarom is donderdag niet geboekt?"*. De skill:

- haalt read-only een snapshot op: status, opties, logs, state-bestanden. Wachtwoorden, cookies
  en tokens worden al op de host weggefilterd
- legt gemelde successen naast je echte reserveringen, zodat een onbetaalde betaallink die later
  `Geannuleerd` is opvalt
- controleert per dag of de nachtrun van 00:01 en de gerichte runs gedraaid hebben, telt herstarts
  en groepeert waarschuwingen (en laat zien of ze sinds de laatste herstart nog voorkomen)
- herstart of wijzigt nooit iets op de host

De scripts kun je ook zelf draaien:

```bash
.claude/skills/padel-log-analysis/scripts/collect.sh /tmp/padel-snap 5000   # host/sleutel/slug via PADEL_HA_HOST, PADEL_HA_KEY, PADEL_ADDON_SLUG
python3 .claude/skills/padel-log-analysis/scripts/analyze.py /tmp/padel-snap
```

---

## Bestandsstructuur

```
padel-booking/
├── README.md
├── CLAUDE.md                        # ontwikkelrichtlijnen
├── .claude/skills/padel-log-analysis/  # Claude Code skill: logs/state van de addon analyseren
├── repository.yaml                  # HA custom repository manifest
├── padel_booking/                   # HA addon
│   ├── config.yaml                  # addon manifest + versienummer + options schema
│   ├── Dockerfile
│   ├── run.sh                       # entrypoint
│   ├── options_to_config.py         # converteert /data/options.json naar .env + config.yaml
│   ├── requirements.txt
│   └── booking_config.yaml          # standaard booking configuratie (fallback)
├── orchestrator.py                  # hoofdscript — coördineert providers
├── fetch_bookings.py                # haalt je reserveringen op uit je account (future_bookings.json)
├── notify.py                        # notificaties (HA push + macOS + console)
├── providers/
│   ├── base.py                      # ProviderResult contract + gedeelde datum/tijd-helpers
│   ├── meetandplay/
│   │   ├── provider.py              # entry point subprocess
│   │   ├── booking.py               # Playwright browser automatisering
│   │   └── session.py               # cookie/sessie beheer
│   └── playtomic/
│       ├── provider.py              # entry point subprocess
│       ├── client.py                # REST API client
│       └── booking.py               # boekingslogica
├── scheduler.py                     # plant runs op de vrijgavemomenten per club
├── research/booking_windows.py      # meet release-strategieën per club
├── tests/                           # unit tests (pytest)
├── test_integration.py              # integratietest Meet & Play (vereist credentials)
├── ruff.toml                        # lint-configuratie
├── config.yaml                      # booking configuratie (lokaal/dev)
└── requirements.txt
```
