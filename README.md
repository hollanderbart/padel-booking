# KNLTB Padel Booking

Automatiseert het boeken van padelbanen op [meetandplay.nl](https://www.meetandplay.nl). Het script zoekt elke nacht naar beschikbare binnenbanen in een opgegeven regio en tijdvenster, voegt het eerste beschikbare tijdslot toe aan de winkelwagen en stuurt een push notificatie met een directe link naar de betalingspagina.

## Hoe het werkt

1. Logt automatisch in via KNLTB ID SSO
2. Zoekt beschikbare clubs in de regio op de komende 4 weken (configureerbare dag)
3. Filtert op binnenbaan, avond, gewenste duur en speltype
4. Boekt het eerste beschikbare tijdslot en navigeert naar de betalingspagina
5. Stuurt een push notificatie naar de HA mobiele app met een link om de betaling af te ronden

De betaling zelf doe je handmatig via de link in de notificatie. Er worden alleen notificaties gestuurd bij een succesvolle boeking.

---

## Installatie als Home Assistant addon

### Vereisten

- Home Assistant OS of Supervised
- [HA Companion app](https://companion.home-assistant.io/) op je telefoon voor push notificaties
- KNLTB account op meetandplay.nl

### Stap 1 — Voeg de custom repository toe

1. Ga naar **Settings → Add-ons → Add-on store**
2. Klik op **⋮ (drie puntjes) → Repositories**
3. Voeg toe: `https://github.com/hollanderbart/knltb-padel-booking`
4. Sluit het venster en ververs de pagina
5. De addon **KNLTB Padel Booking** verschijnt onderaan de store

### Stap 2 — Installeer de addon

Klik op **KNLTB Padel Booking → Install**. De installatie duurt even omdat Playwright en Chromium worden gedownload (~500 MB).

### Stap 3 — Configureer de addon

Ga naar **Settings → Add-ons → KNLTB Padel Booking → Configuration** en vul in:

| Veld | Beschrijving |
|---|---|
| `knltb_email` | E-mailadres van je KNLTB account |
| `knltb_password` | Wachtwoord van je KNLTB account |
| `ha_notify_device_id` | Device ID voor push notificaties (optioneel) |
| `location_city` | Stad om clubs in te zoeken (bijv. `Boskoop`) |
| `location_radius_km` | Zoekradius in kilometers |
| `booking_day` | Dag van de week om te boeken |
| `booking_time_start` | Vroegste starttijd (bijv. `19:30`) |
| `booking_time_end` | Laatste starttijd (bijv. `21:00`) |
| `duration_minutes` | Duur van de boeking: `60` of `90` minuten |
| `court_type` | Baantype: `indoor` of `outdoor` |
| `game_type` | Speltype: `double` (4 spelers) of `single` (2 spelers) |
| `weeks_ahead` | Aantal weken vooruit zoeken |

**`ha_notify_device_id`** vind je via:
**Developer Tools → Actions → zoek op `notify.mobile_app`**

De naam van de service is bijv. `notify.mobile_app_iphone_van_bart_2` → device ID is `iphone_van_bart_2`.

### Stap 4 — Test handmatig

Ga naar **Settings → Add-ons → KNLTB Padel Booking → Start** en bekijk de **Log** tab. Een succesvolle run ziet er zo uit:

```
INFO  Automatisch inloggen gelukt!
INFO  8 club(s) gevonden na filteren
INFO  Tijdslot gevonden: Sportcentrum Boskoop om 19:30 (baan: Padelbaan 1)
INFO  Winkelwagen bereikt
INFO  HA push notificatie verzonden: Padelbaan geboekt!
```

### Stap 6 — Automatisering instellen

Voeg een automation toe via **Settings → Automations → + Create automation**:

- **Trigger**: Time → `00:00:30`
- **Action**: Call service → `hassio.addon_start` → `addon: local_knltb_padel_booking`

Of voeg dit toe aan `automations.yaml`:

```yaml
- alias: "Padel booking — dagelijkse run"
  trigger:
    - platform: time
      at: "00:00:30"
  action:
    - service: hassio.addon_start
      data:
        addon: local_knltb_padel_booking
```

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
cp knltb_padel_booking/.env.example .env
# Vul KNLTB_EMAIL en KNLTB_PASSWORD in

# Uitvoeren (headless)
python booking.py

# Met zichtbare browser (voor debugging)
python booking.py --headed

# Met debug logging
python booking.py --debug
```

### Via Docker

```bash
docker build -f knltb_padel_booking/Dockerfile -t knltb-test .
docker run --rm -v $(pwd)/.env:/app/.env knltb-test
```

---

## Troubleshooting

### Login mislukt

Als het script niet kan inloggen:
1. Controleer `knltb_email` en `knltb_password` in de Configuration tab van de addon
2. Bekijk de debug screenshot via **File Editor**: `/config/knltb/debug_login_failed.png`

### Script vindt geen banen

- Er zijn geen banen beschikbaar in het opgegeven tijdvenster
- Verbreed het tijdvenster (`booking_time_start`/`booking_time_end`) of vergroot `location_radius_km` in de Configuration tab

### Push notificaties komen niet aan

- Controleer `ha_notify_device_id` in de Configuration tab van de addon
- Zoek de juiste device ID via **Developer Tools → Actions → `notify.mobile_app`**

---

## Bestandsstructuur

```
knltb-padel-booking/
├── README.md
├── CLAUDE.md                        # ontwikkelrichtlijnen
├── repository.yaml                  # HA custom repository manifest
├── knltb_padel_booking/             # HA addon
│   ├── config.yaml                  # addon manifest + versienummer + options schema
│   ├── Dockerfile
│   ├── run.sh                       # entrypoint
│   ├── options_to_config.py         # converteert /data/options.json naar .env + config.yaml
│   ├── booking.py
│   ├── notify.py
│   ├── session.py
│   ├── requirements.txt
│   └── booking_config.yaml          # standaard booking configuratie (fallback)
├── booking.py                       # hoofdscript
├── notify.py                        # notificaties
├── session.py                       # sessiebeheer
├── config.yaml                      # booking configuratie
└── requirements.txt
```
