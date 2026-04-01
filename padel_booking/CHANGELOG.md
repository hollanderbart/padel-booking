## 2.0.0

- Herstructurering naar provider/orchestrator architectuur
- Voeg Playtomic support toe via REST API (geen browser nodig)
- Orchestrator coördineert providers als parallelle subprocessen — eerste succes wint
- Addon hernoemd van `knltb_padel_booking` naar `padel_booking`
- Configuratie pad gewijzigd van `/config/knltb/` naar `/config/padel/`
- Nieuwe config-opties: `meetandplay_enabled`, `playtomic_enabled`, `playtomic_email`, `playtomic_password`, `location_latitude`, `location_longitude`
- Boekingsgeschiedenis bevat nu `provider` veld

## 1.2.5

- Fix: echte betaalprovider-URL wordt nu correct opgehaald en verstuurd in de notificatie
- Fix: voorwaarden-checkbox wordt aangevinkt via JavaScript zodat Alpine.js x-model triggert
- Fix: wacht expliciet op zichtbaarheid van de TOS-checkbox na Livewire-navigatie in plaats van vaste timeout
- Fix: reCAPTCHA-vertraging afgedekt met 20s timeout op wait_for_url

## 1.2.4

- Fix: betaalknop wordt niet meer geklikt — de `href` van de betaallink wordt uitgelezen en verstuurd in de notificatie zonder de betaling zelf af te ronden
- Fix: integratietest doet hetzelfde: leest betaallink uit zonder te klikken, maakt geen echte boeking

## 1.2.3

- Fix: boeking faalde in headless mode — na klikken op "Afrekenen" werd de URL-check te vroeg uitgevoerd omdat Livewire asynchroon navigeert; vervangen door `wait_for_url` zodat het script wacht tot de navigatie daadwerkelijk plaatsvindt

## 1.2.2

- Fix: timeout bij laden clubpagina verhoogd van 30s naar 60s
- Fix: timeout of andere fout bij één club stopt het script niet meer — die club wordt overgeslagen en het script gaat door naar de volgende

## 1.2.1

- Fix: `mkdir -p /config/knltb` naar vóór `booking.py` verplaatst zodat `last_run.json` en `booking_history.json` altijd geschreven kunnen worden
- Fix: `json_attributes_template` sensor gebruikt nu `{"bookings": value_json}` zodat Lovelace card `state_attr(..., 'bookings')` direct als lijst kan gebruiken (was kapot door dubbele JSON-encoding)

## 1.2.0

- Voeg `CHANGELOG.md` toe aan addon voor weergave in HA Changelog tabblad

## 1.1.9

- Schrijf `last_run.json` na elke run naar `/config/knltb/last_run.json` voor weergave in Lovelace dashboard
- Voeg `last_run_file` config-sleutel toe

## 1.1.8

- Sla `payment_url` op in boekingsgeschiedenis zodat betaling direct via dashboard-link afgerond kan worden

## 1.1.7

- Voeg boekingsgeschiedenis toe: schrijf `booking_history.json` na succesvolle boeking
- Maximaal 20 entries, nieuwste bovenaan
- `history_file` config-sleutel toegevoegd, wijst naar `/config/knltb/booking_history.json`
- README: instructies voor `command_line` sensor en Lovelace markdown card

## 1.1.6

- Voeg deduplicatie toe: sla boekingsstatus op in `.booking_state.json` en sla volgende runs over als boeking al aanwezig is voor een toekomstige datum

## 1.1.5

- Herstel `knltb_email` naar email type en `knltb_password` naar password type in config UI

## 1.1.4

- Toon email, wachtwoord en device_id als plaintext in config UI

## 1.1.3

- Fix `duration_minutes` default: gebruik string `"90"` zodat HA list-optie correct preselecteert

## 1.1.2

- Voeg addon icoon toe

## 1.1.1

- Voeg HA addon configuratie UI toe via `/data/options.json`
- Configuratie via addon Configuration tab in plaats van handmatig `.env` bestand

## 1.1.0

- Zoek 4 weken vooruit naar beschikbare tijdsloten
- Stuur betaallink mee in push notificatie
- Voeg `weeks_ahead` config-optie toe

## 1.0.2

- Fix login: klik submit-knop na invullen e-mailadres
- Verwijder overbodige notificaties, alleen push bij succesvolle boeking
- Blokkeer headed browser in Docker, verbeter login foutafhandeling
- Voeg `hassio_api` en `homeassistant_api` rechten toe aan addon

## 1.0.1

- Voeg HA mobile push notificaties toe via `notify.mobile_app_*`

## 1.0.0

- Eerste release: automatisch padelbanen boeken op meetandplay.nl
- Zoekt clubs op locatie, filtert op binnenbaan/avond/duur/speltype
- Logt automatisch in via KNLTB ID SSO
