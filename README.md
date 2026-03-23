# KNLTB Padel Booking Automatisering

Automatisch padelbanen boeken op het KNLTB Meet & Play platform (meetandplay.nl).

## Functionaliteit

Dit script automatiseert het boeken van padelbanen met de volgende kenmerken:
- **Sport**: Padel, dubbel (4 spelers)
- **Baantype**: Binnenbaan (configureerbaar)
- **Regio**: Boskoop, straal 20km (configureerbaar)
- **Dag**: Elke donderdagavond (configureerbaar)
- **Tijdslot**: 19:00-21:30 (configureerbaar)

Het script gebruikt Playwright voor browser-automatisering en hergebruikt sessies via cookies, zodat je niet elke keer opnieuw hoeft in te loggen.

## Installatie

### 1. Vereisten

- Python 3.8 of hoger
- pip (Python package manager)

### 2. Installeer dependencies

```bash
# Installeer Python packages
pip install -r requirements.txt

# Installeer Playwright browsers
playwright install chromium
```

### 3. Configuratie

#### Environment variables (optioneel)

Maak een `.env` bestand aan in de projectmap voor eventuele credentials:

```bash
# .env
KNLTB_EMAIL=jouw@email.nl
KNLTB_PASSWORD=jouwwachtwoord
```

**Let op**: Het `.env` bestand staat in de `.gitignore` en wordt niet gecommit naar git.

#### Config bestand

Pas `config.yaml` aan naar jouw voorkeuren:

```yaml
location:
  city: Boskoop           # Jouw stad
  radius_km: 20           # Zoekradius in kilometers

booking:
  day: thursday           # Dag van de week (monday t/m sunday)
  time_start: "19:00"     # Start tijd
  time_end: "21:30"       # Eind tijd
  court_type: indoor      # indoor of outdoor
  game_type: double       # double = 4 spelers

session:
  cookies_file: .session_cookies.json  # Locatie voor opgeslagen sessie
```

## Gebruik

### Eerste keer uitvoeren

Bij de eerste keer draaien, of als je sessie is verlopen, zal het script een browser openen waarin je handmatig moet inloggen:

```bash
python booking.py --headed
```

Volg de instructies in de terminal:
1. Log in op Meet & Play in de browser die wordt geopend
2. Wacht tot de homepage volledig is geladen
3. Druk op ENTER in de terminal

Het script slaat je sessie op en zal deze hergebruiken bij volgende runs.

### Normale uitvoering

Na de eerste login kun je het script in headless mode draaien:

```bash
python booking.py
```

Het script zal:
1. Je opgeslagen sessie hergebruiken
2. Zoeken naar beschikbare padelbanen
3. De eerste beschikbare binnenbaan selecteren
4. Tot aan de betalingspagina gaan
5. Stoppen en een notificatie sturen

**Let op**: Het script stopt bij de betalingspagina. Je moet zelf de betaling afronden!

### Automatisch uitvoeren met cron

Om het script wekelijks automatisch uit te voeren (bijvoorbeeld 5 dagen van tevoren, om 9:00):

```bash
# Bewerk je crontab
crontab -e

# Voeg deze regel toe (pas het pad aan):
0 9 * * 6 cd /Users/jouw-naam/Projects/knltb-padel-booking && python booking.py >> booking.log 2>&1
```

Dit runt het script elke zaterdag om 9:00 en logt de output naar `booking.log`.

Voor 120 uur (5 dagen) van tevoren, bereken je de juiste dag en tijd op basis van je gewenste boekingsdatum.

## Notificaties

Het script verstuurt macOS notificaties voor de volgende gebeurtenissen:
- ✅ **Baan gevonden**: Er is een baan beschikbaar en het script staat klaar op de betalingspagina
- ⚠️ **Geen banen**: Er zijn geen banen beschikbaar
- ❌ **Fout**: Er is een fout opgetreden tijdens het boeken
- 🔐 **Sessie verlopen**: Je moet opnieuw inloggen

## Sessie management

Het script slaat cookies op in `.session_cookies.json`. Dit bestand:
- Staat in `.gitignore` en wordt niet gecommit
- Bevat je login sessie
- Wordt automatisch vernieuwd als de sessie verloopt

### Sessie verwijderen

Als je opnieuw wilt inloggen of problemen hebt met de sessie:

```bash
rm .session_cookies.json
python booking.py --headed
```

## Troubleshooting

### "Sessie verlopen" melding

Als je vaak de melding krijgt dat je sessie is verlopen:
1. Verwijder het cookies bestand: `rm .session_cookies.json`
2. Run het script opnieuw met `--headed`
3. Log handmatig in

### Script vindt geen banen

Dit kan verschillende oorzaken hebben:
- Er zijn echt geen banen beschikbaar in jouw regio/tijdslot
- De selectors in het script zijn verouderd (Meet & Play heeft hun website aangepast)
- Je zoekparameters zijn te specifiek

### Script crasht of geeft errors

1. Check of Playwright goed is geïnstalleerd: `playwright install chromium`
2. Check of alle dependencies zijn geïnstalleerd: `pip install -r requirements.txt`
3. Run in headed mode om te zien wat er gebeurt: `python booking.py --headed`

## Structuur

```
knltb-padel-booking/
├── README.md              # Deze documentatie
├── requirements.txt       # Python dependencies
├── config.yaml           # Configuratie (locatie, tijd, etc.)
├── .env                  # Environment variables (optioneel, niet in git)
├── .gitignore           # Git ignore regels
├── booking.py           # Hoofdscript
├── session.py           # Cookie/session management
├── notify.py            # Notificatie systeem
└── .session_cookies.json # Opgeslagen sessie (niet in git)
```

## Belangrijke notities

⚠️ **Dit script dient als startpunt en moet mogelijk worden aangepast**:
- De selectors in `booking.py` zijn placeholders en moeten worden aangepast op basis van de daadwerkelijke structuur van meetandplay.nl
- Test het script altijd eerst handmatig met `--headed` mode
- Het script stopt bij de betalingspagina - je moet zelf betalen

⚠️ **Gebruik op eigen risico**:
- Dit is een automatiseringsscript voor persoonlijk gebruik
- Controleer altijd de boeking voordat je betaalt
- Het script maakt gebruik van browser-automatisering, wat tegen de Terms of Service van bepaalde websites kan zijn

## Licentie

Voor persoonlijk gebruik.

## Support

Voor vragen of problemen, open een issue in de repository.
