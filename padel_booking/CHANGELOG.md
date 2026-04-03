## 2.2.0

- Fix: `fetch_bookings.py` ontbrak in Dockerfile COPY instructie — bestand werd nooit in de image opgenomen ondanks aanwezigheid in de build context

## 2.1.9

- Fix: Playtomic deep link gebruikt nu `/tenant/` i.p.v. `/clubs/` — correcte URL die werkt in de app

## 2.1.8

- README: instructies toegevoegd voor "Boeking resetten" knop via HA shell_command en Lovelace button card

## 2.1.7

- Fix: `fetch_bookings.py` ontbrak in de addon build context — `run.sh` gaf `No such file or directory` na elke succesvolle run

## 2.1.6

- Playtomic club-URL bevat nu datum, starttijd en resource ID als query parameters zodat de app direct op het juiste slot en baan opent (`?sport=PADEL&date=...&startTime=...&resourceId=...`)

## 2.1.5

- Fix: clubnaam in notificatietekst ("Boek nu: Sunset Padel Alphen") wordt nu direct uit de bestaande locatiestring gehaald — geen overbodige extra parameter meer

## 2.1.4

- Notificatie toont clubnaam als leesbare linktekst ("Boek nu: Sunset Padel Alphen") i.p.v. de kale URL; tikken op de notificatie opent de club-URL nog steeds direct

## 2.1.3

- Playtomic boekt niet meer automatisch — bij een beschikbaar slot wordt een notificatie gestuurd met de club-URL (`https://app.playtomic.io/clubs/{id}`) zodat de gebruiker de boeking en betaling zelf handmatig kan afronden
- Notificatietitel gewijzigd van "Padelbaan geboekt!" naar "Padelbaan beschikbaar!"
- Console-output bijgewerkt: "BOEKING GESLAAGD" → "BESCHIKBAARHEID GEVONDEN", URL-label → "Boek nu:"

## 2.1.2

- Fix: Playtomic boeking faalt met 503 bij clubs die alleen iDEAL/Bancontact/Swish aanbieden — online betaalmethoden vereisen een browser-redirect en werken niet via de API; zulke clubs worden nu overgeslagen met een duidelijke log-melding in plaats van een fout
- Voeg `NoSuitablePaymentMethodError` toe zodat "geen offline betaalmethode" onderscheiden wordt van echte boekingsfouten

## 2.1.1

- Fix: Playtomic boeking mislukt met `can't compare offset-naive and offset-aware datetimes` — token expiry uit API-response zonder timezone-info wordt nu altijd als UTC behandeld (zelfde fix als in `_load_cached_token`)

## 2.1.0

- Voeg `fetch_bookings.py` toe: haalt live toekomstige boekingen op van beide accounts (Playtomic via `/v1/matches` API, Meet & Play via scraping van `mijn-reserveringen`)
- Schrijft resultaat naar `/config/padel/future_bookings.json` na elke run
- Voeg `command_line` sensor en Lovelace card toe voor weergave in dashboard

## 2.0.9

- Fix Playtomic boeking 400 Bad Request: payload structuur herschreven naar correcte API formaat (`cart.requested_item.cart_item_data` met `start` i.p.v. `start_date`, `CUSTOMER_MATCH` type, `match_registrations`, `user_id`)
- Sla `user_id` op in token cache zodat deze hergebruikt wordt zonder opnieuw in te loggen
- Selecteer betaalmethode dynamisch uit API response i.p.v. hardcoded `AT_CLUB`
- Log foutresponse body bij 400/500 voor snellere diagnose

## 2.0.8

- Verbeter slot-filtering logging: elk overgeslagen slot toont nu de reden (buitenbaan, tijdvenster, duur) en het label dat werd gelezen — maakt debuggen van missende slots mogelijk

## 2.0.7

- Voeg uitgebreide unit tests toe: 147 tests voor alle modules (orchestrator, notify, base, Playtomic client/booker, MeetAndPlay booker/session)
- Voeg pytest.ini toe met asyncio_mode=auto en pytest-asyncio dependency

## 2.0.6

- Fix: Playtomic token vergelijking met tijdzone faalt niet meer (`can't compare offset-naive and offset-aware datetimes`)
- Sorteer Playtomic clubs op afstand zodat de dichtstbijzijnde club altijd als eerste geprobeerd wordt

## 2.0.5

- Fix Playtomic slot parsing: API response is genest per court (`slots` array met `start_time`/`duration`), niet plat per slot
- Fix dubbele logging: provider timestamp+level prefix wordt gestript zodat de HA log leesbaar blijft

## 2.0.4

- Fix: provider stderr altijd zichtbaar in HA log (was alleen op DEBUG level)
- Fix: exit code van provider wordt getoond bij lege output voor betere diagnose

## 2.0.3

- Fix Playtomic slot matching: UTC tijden worden nu correct naar lokale tijd geconverteerd (zomertijd/wintertijd)
- Fix Playtomic duration check: API geeft duur soms in seconden, wordt automatisch herkend en omgezet naar minuten
- Voeg debug logging toe voor slot-tijden zodat mismatches zichtbaar zijn in de log

## 2.0.2

- Playtomic email en wachtwoord naar boven in de configuratie UI verplaatst
- Playtomic email en wachtwoord zijn nu verplichte velden (geen validatiefout meer bij lege waarde)

## 2.0.1

- Fix: Docker build mislukte omdat providers/ en orchestrator.py niet beschikbaar waren in de addon build context; gesynchroniseerde kopieën toegevoegd aan padel_booking/

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
