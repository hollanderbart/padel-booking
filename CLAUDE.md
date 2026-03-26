# CLAUDE.md — ontwikkelrichtlijnen

## Versiebeheer addon

Bij **elke aanpassing die gepusht wordt**, moet het versienummer in
`knltb_padel_booking/config.yaml` verhoogd worden. HA detecteert updates
op basis van dit versienummer.

### Versie verhogen

Pas `version` aan in `knltb_padel_booking/config.yaml`:

```yaml
version: "1.0.2"  # verhoog bij elke push
```

Gebruik [semantic versioning](https://semver.org/):
- **patch** (1.0.x) — bugfixes, kleine aanpassingen
- **minor** (1.x.0) — nieuwe functionaliteit, backwards compatible
- **major** (x.0.0) — breaking changes

### Workflow bij een aanpassing

1. Maak de wijziging in de bronbestanden in de repo root (`booking.py`, `notify.py`, etc.)
2. Sync gewijzigde bestanden naar `knltb_padel_booking/` als ze daar ook in staan
3. Verhoog het versienummer in `knltb_padel_booking/config.yaml`
4. **Update `README.md`** als de wijziging de installatie, configuratie, gebruik of gedrag van notificaties beïnvloedt (zie sectie hieronder)
5. Commit en push naar `main`
6. In HA: **Settings → Add-ons → KNLTB Padel Booking → ⋮ → Check for updates → Update**

## README bijhouden

Werk `README.md` bij bij wijzigingen die de gebruiker raken, zoals:

- Nieuwe of gewijzigde configuratie-opties (`.env`, `config.yaml`)
- Gewijzigd gedrag van notificaties
- Gewijzigde installatiestappen of HA-integratie
- Nieuwe CLI-vlaggen (`--headed`, `--debug`, etc.)
- Gewijzigde automation service name of addon slug
- Nieuwe troubleshooting-scenario's

## Projectstructuur

```
knltb-padel-booking/
├── CLAUDE.md                        # deze richtlijnen
├── repository.yaml                  # HA custom repository manifest
├── knltb_padel_booking/             # HA addon (slug = directory naam)
│   ├── config.yaml                  # addon manifest + versienummer
│   ├── Dockerfile                   # Python 3.12 + Playwright + Chromium
│   ├── run.sh                       # entrypoint: sync config, run booking.py
│   ├── booking.py                   # kopie van repo root (gesynchroniseerd)
│   ├── notify.py                    # kopie van repo root (gesynchroniseerd)
│   ├── session.py                   # kopie van repo root (gesynchroniseerd)
│   ├── requirements.txt             # kopie van repo root (gesynchroniseerd)
│   ├── booking_config.yaml          # standaard booking configuratie
│   └── .env.example                 # voorbeeld .env met alle variabelen
├── booking.py                       # hoofdscript
├── notify.py                        # notificaties (HA push + macOS + console)
├── session.py                       # sessiebeheer / cookie opslag
├── config.yaml                      # booking configuratie
└── requirements.txt                 # Python dependencies
```

## Configuratie op de HA host

Plaats de volgende bestanden op de HA host onder `/config/knltb/`:

```
/config/knltb/.env
```

Inhoud van `.env` (zie ook `knltb_padel_booking/.env.example`):

```
KNLTB_EMAIL=jouw@email.nl
KNLTB_PASSWORD=jouwwachtwoord
HA_NOTIFY_DEVICE_ID=iphone_van_bart_2
```

De `HA_NOTIFY_DEVICE_ID` is de suffix van de HA notify service. Te vinden via:
**Developer Tools → Actions → zoek op `notify.mobile_app`**
