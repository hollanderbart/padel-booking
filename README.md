# Padel Booking

Automatiseert het boeken van padelbanen op [meetandplay.nl](https://www.meetandplay.nl) en [Playtomic](https://playtomic.io). Het script zoekt elke nacht naar beschikbare binnenbanen in een opgegeven regio en tijdvenster, en stuurt een push notificatie met een directe link naar de betalingspagina zodra een boeking gelukt is.

## Hoe het werkt

Een **orchestrator** coördineert meerdere providers die parallel draaien als losse subprocessen. De eerste provider die een boeking succesvol afrondt wint; de andere wordt gestopt.

**Meet & Play provider** (browser automatisering via Playwright):
1. Logt automatisch in via KNLTB ID SSO
2. Zoekt beschikbare clubs in de regio op de komende weken (configureerbare dag)
3. Filtert op binnenbaan, avond, gewenste duur en speltype
4. Boekt het eerste beschikbare tijdslot en navigeert naar de betalingspagina

**Playtomic provider** (REST API — geen browser nodig):
1. Logt in via de Playtomic API met email/wachtwoord
2. Zoekt clubs in de buurt via coördinaten, gesorteerd op afstand (dichtstbijzijnde eerst)
3. Haalt beschikbaarheid op en boekt het eerste passende slot

De betaling zelf doe je handmatig via de link in de notificatie. Er worden alleen notificaties gestuurd bij een succesvolle boeking.

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
| `location_radius_km` | Zoekradius in kilometers |
| `location_latitude` | Breedtegraad voor Playtomic zoeken (bijv. `52.0738`) |
| `location_longitude` | Lengtegraad voor Playtomic zoeken (bijv. `4.6567`) |
| `booking_day` | Dag van de week om te boeken |
| `booking_time_start` | Vroegste starttijd (bijv. `19:30`) |
| `booking_time_end` | Laatste starttijd (bijv. `21:00`) |
| `duration_minutes` | Duur van de boeking: `60` of `90` minuten |
| `court_type` | Baantype: `indoor` of `outdoor` |
| `game_type` | Speltype: `double` (4 spelers) of `single` (2 spelers) |
| `weeks_ahead` | Aantal weken vooruit zoeken |
| `meetandplay_enabled` | Meet & Play provider in/uitschakelen (standaard `true`) |
| `playtomic_enabled` | Playtomic provider in/uitschakelen (standaard `false`) |
| `playtomic_email` | E-mailadres van je Playtomic account (vereist als Playtomic ingeschakeld) |
| `playtomic_password` | Wachtwoord van je Playtomic account (vereist als Playtomic ingeschakeld) |

**`ha_notify_device_id`** vind je via:
**Developer Tools → Actions → zoek op `notify.mobile_app`**

De naam van de service is bijv. `notify.mobile_app_iphone_van_bart_2` → device ID is `iphone_van_bart_2`.

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
INFO  HA push notificatie verzonden: Padelbaan geboekt!
```

### Stap 5 — Automatisering instellen

Voeg een automation toe via **Settings → Automations → + Create automation**:

- **Trigger**: Time → `00:00:30`
- **Action**: Call service → `hassio.addon_start` → `addon: local_padel_booking`

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

## Push notificaties

Bij een succesvolle boeking ontvang je een push notificatie met:
- Baannaam
- Tijdstip
- Clubnaam en adres
- Een directe link naar de betalingspagina (tik op de notificatie om te openen)

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

# Individuele provider testen
echo '{"booking_request":{"location":{"city":"Boskoop","radius_km":20},"day":"thursday","time_start":"19:30","time_end":"21:00","duration_minutes":90,"court_type":"indoor","game_type":"double","weeks_ahead":4},"credentials":{"email":"...","password":"..."},"provider_config":{"cookies_file":".meetandplay_cookies.json"},"dry_run":true}' | python -m providers.meetandplay.provider
```

### Unit tests uitvoeren

```bash
# Alle unit tests (geen credentials of browser nodig)
pytest tests/ -v

# Inclusief de integratie test (vereist KNLTB_EMAIL en KNLTB_PASSWORD in .env)
pytest tests/ test_integration.py -v
```

De `tests/` map bevat 147 tests voor alle modules:
- `test_orchestrator.py` — deduplicatie, state/history, provider subprocess, first-wins logica
- `test_base.py` — ProviderResult JSON contract
- `test_notify.py` — HA push, macOS, console notificaties
- `test_playtomic_client.py` — REST API client, token caching, auth
- `test_playtomic_booking.py` — slot zoeken, afstandssortering, boekingsflow
- `test_meetandplay_booking.py` — slot filters, login, Playwright mocks
- `test_session.py` — cookie/sessie beheer

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

### Login mislukt (Playtomic)

- Controleer `playtomic_email` en `playtomic_password`
- Als je Playtomic account via Google/Apple SSO is aangemaakt, stel dan eerst een wachtwoord in via "Wachtwoord vergeten" op playtomic.io

### Script vindt geen banen terwijl er wel slots beschikbaar zijn

Elk overgeslagen slot wordt nu gelogd met de reden. Zoek in de HA log naar regels als:

```
Slot 3 overgeslagen: tijd 19:30 buiten venster 20:00–21:00 (label: 'Dubbelspel binnenbaan')
Slot 5 overgeslagen: duur 60 min ≠ gewenste 90 min (tijd: 19:30, label: '')
Slot 7 overgeslagen: buitenbaan ('Buitenbaan dubbelspel')
```

Mogelijke oorzaken:
- **Tijdvenster te smal**: het slot valt net buiten `booking_time_start`–`booking_time_end`
- **Duur mismatch**: de site toont bijv. "60 min" maar `duration_minutes` staat op `90`
- **Buitenbaan filter**: het label van het slot bevat "buiten" terwijl `court_type=indoor`

### Script vindt geen banen

- Er zijn geen banen beschikbaar in het opgegeven tijdvenster
- Verbreed het tijdvenster of vergroot `location_radius_km`
- Schakel beide providers in voor meer kans op een beschikbare baan

### Push notificaties komen niet aan

- Controleer `ha_notify_device_id` in de Configuration tab
- Zoek de juiste device ID via **Developer Tools → Actions → `notify.mobile_app`**

---

## Bestandsstructuur

```
padel-booking/
├── README.md
├── CLAUDE.md                        # ontwikkelrichtlijnen
├── repository.yaml                  # HA custom repository manifest
├── padel_booking/                   # HA addon
│   ├── config.yaml                  # addon manifest + versienummer + options schema
│   ├── Dockerfile
│   ├── run.sh                       # entrypoint
│   ├── options_to_config.py         # converteert /data/options.json naar .env + config.yaml
│   ├── requirements.txt
│   └── booking_config.yaml          # standaard booking configuratie (fallback)
├── orchestrator.py                  # hoofdscript — coördineert providers
├── notify.py                        # notificaties (HA push + macOS + console)
├── providers/
│   ├── base.py                      # ProviderResult contract (stdin/stdout JSON)
│   ├── meetandplay/
│   │   ├── provider.py              # entry point subprocess
│   │   ├── booking.py               # Playwright browser automatisering
│   │   └── session.py               # cookie/sessie beheer
│   └── playtomic/
│       ├── provider.py              # entry point subprocess
│       ├── client.py                # REST API client
│       └── booking.py               # boekingslogica
├── config.yaml                      # booking configuratie (lokaal/dev)
└── requirements.txt
```
