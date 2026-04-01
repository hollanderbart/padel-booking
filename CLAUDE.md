# CLAUDE.md — ontwikkelrichtlijnen

## Versiebeheer addon

Bij **elke aanpassing die gepusht wordt**, moet het versienummer in
`padel_booking/config.yaml` verhoogd worden. HA detecteert updates
op basis van dit versienummer.

### Versie verhogen

Pas `version` aan in `padel_booking/config.yaml`:

```yaml
version: "2.0.1"  # verhoog bij elke push
```

Gebruik [semantic versioning](https://semver.org/):
- **patch** (2.0.x) — bugfixes, kleine aanpassingen
- **minor** (2.x.0) — nieuwe functionaliteit, backwards compatible
- **major** (x.0.0) — breaking changes

### Workflow bij een aanpassing

1. Maak de wijziging in de bronbestanden in de repo root
2. Verhoog het versienummer in `padel_booking/config.yaml`
3. **Voeg een entry toe aan `padel_booking/CHANGELOG.md`** met het nieuwe versienummer en een beknopte beschrijving van de wijzigingen (nieuwste versie bovenaan)
4. **Update altijd `README.md`** — bij elke aanpassing, ook kleine (zie sectie hieronder voor wat er in de README hoort)
5. Commit en push naar `main` — **doe dit altijd zonder te vragen**
6. In HA: **Settings → Add-ons → Padel Booking → ⋮ → Check for updates → Update**

> **Claude**: commit en push altijd automatisch na een aanpassing. Vraag hier niet om toestemming voor.
> **Claude**: werk altijd direct op de `main` branch. Maak geen feature branches. Push altijd naar `origin/main`.

**Let op**: Er zijn geen gesynchroniseerde kopieën meer van Python-bestanden in `padel_booking/`. De Dockerfile kopieert de `providers/` map en `orchestrator.py` direct vanuit de repo root.

## README bijhouden

Werk `README.md` bij bij wijzigingen die de gebruiker raken, zoals:

- Nieuwe of gewijzigde configuratie-opties (`config.yaml`)
- Gewijzigd gedrag van notificaties
- Gewijzigde installatiestappen of HA-integratie
- Nieuwe CLI-vlaggen (`--dry-run`, `--debug`, etc.)
- Gewijzigde automation service name of addon slug
- Nieuwe providers of troubleshooting-scenario's

## Projectstructuur

```
padel-booking/
├── CLAUDE.md                        # deze richtlijnen
├── repository.yaml                  # HA custom repository manifest
├── padel_booking/                   # HA addon (slug = directory naam)
│   ├── config.yaml                  # addon manifest + versienummer
│   ├── Dockerfile                   # Python 3.12 + Playwright + Chromium
│   ├── run.sh                       # entrypoint: sync config, run orchestrator.py
│   ├── options_to_config.py         # converteert /data/options.json naar config
│   ├── requirements.txt             # Python dependencies
│   ├── booking_config.yaml          # standaard booking configuratie
│   └── CHANGELOG.md
├── orchestrator.py                  # hoofdscript — coördineert providers
├── notify.py                        # notificaties (HA push + macOS + console)
├── providers/
│   ├── base.py                      # ProviderResult contract (stdin/stdout JSON)
│   ├── meetandplay/
│   │   ├── provider.py              # __main__ entry point
│   │   ├── booking.py               # Playwright browser automatisering
│   │   └── session.py               # cookie/sessie beheer
│   └── playtomic/
│       ├── provider.py              # __main__ entry point
│       ├── client.py                # REST API client
│       └── booking.py               # boekingslogica
├── config.yaml                      # booking configuratie (lokaal/dev)
└── requirements.txt                 # Python dependencies
```

## Architectuur

De orchestrator (`orchestrator.py`) start elke provider als een **apart subprocess**
en stuurt een JSON BookingRequest op stdin. Elke provider schrijft een JSON ProviderResult
naar stdout. Providers draaien parallel — de eerste die succes meldt wint.

- **meetandplay**: Playwright browser automatisering (heeft Chromium nodig)
- **playtomic**: REST API calls (geen browser nodig)

Om een provider in/uit te schakelen: pas `providers.meetandplay.enabled` / `providers.playtomic.enabled` aan in `config.yaml` (lokaal) of via de HA addon configuratie UI.

## Configuratie op de HA host

Credentials worden geconfigureerd via de addon Configuration tab in HA.
Sessiepersistentie draait via `/config/padel/` (volume map `config:rw`):

```
/config/padel/.meetandplay_cookies.json   # Meet & Play sessie cookies
/config/padel/.playtomic_token.json       # Playtomic JWT token cache
/config/padel/booking_history.json        # boekingsgeschiedenis
/config/padel/last_run.json               # laatste run status
```

**HA automation na hernoemen:**
De automation moet `addon: local_padel_booking` gebruiken (was `local_knltb_padel_booking`).
