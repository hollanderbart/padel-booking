## 2.5.0

- Feature: meerdere weekdagen — nieuwe optie `booking_days` (lijst, bijv. dinsdag + donderdag) vervangt `booking_day`. Na de update staat `booking_days` op `thursday` (de standaardwaarde); had je een andere `booking_day`, pas `booking_days` dan aan in de addon-configuratie. `weeks_ahead` geldt per gekozen weekdag
- Feature: alleen boeken op datums zonder reservering. Vóór elke volledige run (start addon en 00:01) worden je reserveringen opgehaald uit je account; die datums én datums waarvoor de addon eerder een slot vond (`booking_history.json`) worden overgeslagen
- Een run boekt nu alle open datums (na elke boeking zoekt hij verder zonder die datum), in plaats van te stoppen na de eerste. Per slot een notificatie met betaallink
- Fix: Meet & Play reserveringen ophalen gebruikte een niet-bestaande pagina (`mijn-reserveringen`) en geraden selectors. Nu via `meetandplay.nl/mijn-aankopen` (tabblad "Aankomend"), inclusief status; geannuleerde reserveringen tellen niet mee
- Mislukt het ophalen van reserveringen (bijv. inloggen), dan blijft `future_bookings.json` ongewijzigd, zodat al geboekte datums niet opnieuw geprobeerd worden
- Vervalt: de "verste doeldatum"-check op `.booking_state.json` en het aparte ophalen om 03:30. De reset-knop in de README werkt nu op `booking_history.json`

## 2.4.2

- Docs: logs en status van de addon uitlezen via SSH (Terminal & SSH addon, `ha apps logs <slug>`) — de HA MCP-server geeft geen toegang tot addon-logs
- Docs: de addon-slug is `<hash>_padel_booking` bij installatie via de GitHub-repository (alleen `local_padel_booking` bij een lokale installatie)

## 2.4.1

- Standaard `weeks_ahead` van 4 naar 5, zodat clubs met een boekingsvenster van ~30 dagen (Tennis en Padel Meerzicht, TERWEGEN INDOOR) op hun vrijgavemoment geboekt worden. Bestaande installaties: pas de optie aan in de addon-configuratie in HA
- Fix (scheduler): doeldagen die pas later binnen `weeks_ahead` vallen werden niet vooruit gepland (zichtbaar in `scheduler.py --plan`); per vrijgavemoment wordt nu gecontroleerd of de doeldag dan binnen bereik valt

## 2.4.0

- Feature: slimme planning (`schedule_mode: smart`, standaard). De addon draait nu continu en start de booker op het moment dat een club nieuwe banen vrijgeeft, op basis van het club-schema uit de booking research (`club_schedule.json` op de branch `booking-research`):
  - rollende clubs: N dagen vóór de doeldag, op elke starttijd binnen het tijdvenster (bijv. 19:30, 20:00, 20:30)
  - dagelijkse clubs: N dagen vóór de doeldag op het vrijgave-uur
  - gerichte runs gaan direct naar de club (geen zoekpagina) en worden na 1 en 5 minuten herhaald als er nog niets vrij was
  - de volledige run om 00:01 blijft als vangnet voor clubs zonder bekend vrijgavemoment
- `schedule_mode: nightly` behoudt het oude gedrag (één run per start van de addon)
- Nieuwe CLI-optie `orchestrator.py --target '<json>'` voor een gerichte run (datums + clubs); `scheduler.py --plan` toont de geplande runs
- Fix: `run.sh` brak af als er geen boeking gemaakt werd (exit code 1 met `set -e`), waardoor toekomstige boekingen niet opgehaald en sessiebestanden niet bewaard werden
- Sessiebestanden staan nu direct in `/config/padel/` (eenmalige migratie vanuit `/data`)

## 2.3.4

- Fix (Meet & Play): de clubpagina toont alleen slots van de geselecteerde starttijd. De booker bekeek daardoor alleen de eerste beschikbare tijd (bijv. 18:00) en vond niets binnen het venster (bijv. 19:30–21:00), ook als dat wel vrij was. De booker loopt nu alle starttijden binnen het venster af via de "Tijdstip"-kiezer
- Fix (Meet & Play): het duurfilter is een knop geworden ("90 minuten") i.p.v. een dropdown en werd stil overgeslagen; beide varianten worden nu ondersteund
- Fix (Meet & Play): "Toevoegen" klikte de eerste knop op de pagina (mogelijk een andere baan dan in de notificatie); nu wordt de knop van het gekozen slot gebruikt
- Het dagdeelfilter staat niet meer vast op "Avond"; de booker filtert zelf op het tijdvenster (werkt nu ook voor vensters vóór 18:00)

## 2.3.3

- Fix (booking research): starttijden worden nu uit de "Tijdstip"-kiezer op de clubpagina gelezen. Die bevat alle nog beschikbare starttijden van een dag; de slotlijst toont alleen de eerste tijd

## 2.3.2

- Fix (booking research): de Meet & Play clubpagina toont per keer slots van één starttijd, waardoor "laatste slot op de horizon-dag" niets zei over rollend vs. hele-dag release. Zulke observaties tellen niet meer mee voor die conclusie (het venster in dagen blijft wel geldig)
- Booking research: optie `snapshot` bij handmatig starten bewaart pagina-HTML per club als artifact (diagnose)

## 2.3.1

- Playtomic provider tijdelijk uitgeschakeld (ook als `playtomic_enabled: true` is ingesteld): de API-host `api.playtomic.io` resolveert niet meer. Geldt voor zowel boeken als het ophalen van toekomstige boekingen; de log toont een waarschuwing

## 2.3.0

- Feature: booking research — de GitHub Action `booking-research.yml` meet zes keer per dag per Meet & Play club tot hoe ver vooruit en vanaf welk moment banen vrijkomen, en leidt de release-strategie af (rollend op exact tijdstip, dagelijks vast uur, blok-release). Rapport: `BOOKING_WINDOWS.md` op de branch `booking-research`
- Meet & Play `_search_clubs` kan clubs zoeken zonder datumfilter (voor de research)
- README: uitleg over release-strategieën en bekend probleem met de Playtomic API-host (`api.playtomic.io` resolveert niet meer)

## 2.2.12

- Fix: GitHub Actions workflow was ongeldig (`secrets` in een job-level `if`), waardoor CI nooit draaide. De integratietest wordt nu per stap overgeslagen als `KNLTB_EMAIL` niet als secret is ingesteld

## 2.2.11

Codekwaliteit-refactor (geen nieuwe functionaliteit of configuratie-opties).

- Fix: Playtomic HTTP-requests hadden geen timeout — een hangende verbinding kon de nachtelijke run onbeperkt blokkeren. Alle requests hebben nu een timeout van 30s
- Fix: de orchestrator en de providers rekenden het zoekvenster verschillend uit wanneer vandaag de boekingsdag is (orchestrator sloeg vandaag altijd over, providers niet). Er is nu één gedeelde datumfunctie in `providers/base.py`
- Fix: macOS-notificaties braken bij een backslash in de tekst (escaping in AppleScript)
- Fix: CI draaide alleen `test_unit.py` en niet de `tests/` map, waardoor een falende test onopgemerkt bleef. CI draait nu alle unit tests, `ruff` lint, en controleert of `padel_booking/` gesynchroniseerd is
- `fetch_bookings.py` gebruikt nu `PlaytomicClient` i.p.v. een eigen kopie van de login/token-cache logica
- Grote functies opgesplitst (max. cyclomatische complexiteit 43 → 13), dubbele code verwijderd (WEEKDAYS/datumlogica, user-agent, cookiebanner, provider entry points)
- Verouderde pre-refactor scripts verwijderd: `booking.py`, `session.py`, `run_booking.sh` (werden niet meer gebruikt of meegebouwd)
- Ongebruikte code verwijderd: `notify_no_courts_available`, `notify_booking_error`, `notify_session_expired`, `SessionManager.manual_login`
- 38 extra unit tests (o.a. Meet & Play checkout-flow, automatisch inloggen, `fetch_bookings.py`)

## 2.2.10

- Fix: Playtomic `/v1/availability` geeft het rooster terug, niet de echte bezetting — een slot in de response betekent niet dat het vrij is. De code probeert nu een `payment_intent` aan te maken als validatie; een 4xx betekent dat het slot al bezet is en de code gaat door naar het volgende slot/club

## 2.2.9

- Fix: Playtomic `court_type` filter werkte niet — `_find_slot` controleerde het baantype (indoor/outdoor) nooit, waardoor outdoor clubs werden gematcht terwijl `court_type: indoor` was geconfigureerd
- Fix: Playtomic baannamen werden altijd hardcoded als "Padelbaan" — worden nu correct uitgelezen uit de tenant resources (bijv. "Eric Sarelse Baan #1")
- Fix: Playtomic clubadres was altijd leeg — `full_address` bestaat niet in de API response; wordt nu samengesteld uit `street` + `city`

## 2.2.8

- Feature: nieuwe configuratie-optie `skip_booked_dates` (standaard `true`) — als ingeschakeld controleert de orchestrator `future_bookings.json` vóór het zoeken en slaat datums over die al bevestigd geboekt zijn

## 2.2.7

- Fix: deduplicatie blokkeerde te vroeg — boekingen werden overgeslagen zodra *enige* toekomstige datum al geboekt was, ook als er nog latere data in het zoekvenster zaten. Nu wordt pas overgeslagen als de verste doeldatum (weeks_ahead weken vooruit) al geboekt is.

## 2.2.6

- Fix: baan en tijdstip samengevoegd tot één regel in push notificatie (bijv. "Baan 3 — 19:30 - 21:00")

## 2.2.5

- Fix: provider naam toegevoegd aan push notificatie (bijv. "via playtomic")

## 2.2.4

- Fix: boekingsdatum toegevoegd aan push notificatie — was eerder afwezig zodat niet duidelijk was voor welke datum er geboekt was

## 2.2.3

- Fix: Playtomic boekingen ophalen — API geeft gepagineerd object terug (`{"content": [...]}`) i.p.v. kale lijst; `len()` telde nu keys in de dict waardoor altijd 0 boekingen werden verwerkt
- Feature: nieuwe configuratie-optie `fetch_bookings_enabled` (standaard `true`) om het ophalen van toekomstige boekingen in/uit te schakelen

## 2.2.2

- Fix: `playtomic_password` schema type gewijzigd van `str` naar `password` zodat het veld gemaskeerd wordt in de HA configuratie UI

## 2.2.1

- Fix: booking state bestand verplaatst van `/data/.booking_state.json` naar `/config/padel/.booking_state.json` zodat het van buitenaf bereikbaar is (bijv. via shell_command reset knop)

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
