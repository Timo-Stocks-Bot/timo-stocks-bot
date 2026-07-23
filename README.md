# Timo Stocks Bot

Telegram-Bot, der ueber `bot.py` Kurs-/Marktbewegungen prueft und bei Ueberschreitung
von Schwellenwerten (siehe `WATCHLIST` in `bot.py`) eine Nachricht schickt. Gold bekommt
zusaetzlich ein taegliches Fixzeit-Update.

Zusaetzlich sammelt `news_check.py` markrelevante News (Zinsentscheidungen, Regulierung,
grosse Tech/KI-News) per RSS + Groq-Relevanz-Check (Llama-Modell) und schickt gebuendelt
eine Zusammenfassung, falls etwas relevant ist (sonst bleibt es still).

Hinweis: urspruenglich war hierfuer Google Gemini vorgesehen, dessen Free-Tier ist aber in
der EU/EWR nicht verfuegbar (Quota 0). Deshalb wird stattdessen Groq genutzt (echter
kostenloser Tier, kein EU-Ausschluss bekannt).

## Wichtig: Automatisierung laeuft ueber cron-job.org, nicht ueber GitHubs eigenen Zeitplan

GitHubs eingebauter `schedule:`-Trigger in den Workflows (`.github/workflows/*.yml`) hat sich
als unzuverlaessig erwiesen (Laeufe blieben stundenlang aus). Deshalb werden beide Workflows
stattdessen ueber einen externen, kostenlosen Cron-Dienst ausgeloest:

- **Account**: cron-job.org (Login: siehe eigene Zugangsdaten)
- **Cronjob "Gold Update Trigger"**: taeglich, ruft
  `POST https://api.github.com/repos/Timo-Stocks-Bot/timo-stocks-bot/actions/workflows/gold.yml/dispatches`
- **Cronjob "Market Alerts Trigger"**: alle 15-20 Min., ruft
  `POST https://api.github.com/repos/Timo-Stocks-Bot/timo-stocks-bot/actions/workflows/alerts.yml/dispatches`
- **Cronjob "Market News Trigger - Morgens"** (8:00 Uhr) und **"... - Abends"** (22:00 Uhr):
  ruft `POST https://api.github.com/repos/Timo-Stocks-Bot/timo-stocks-bot/actions/workflows/news.yml/dispatches`
  (zwei separate Cronjobs statt einem, da cron-job.org pro Job nur eine feste Uhrzeit erlaubt)

Alle Cronjobs senden dabei einen GitHub **Fine-grained Personal Access Token**
(Scope: nur dieses Repo, Permission "Actions: Read and write") als
`Authorization: Bearer <token>`-Header sowie Body `{"ref":"main"}`.

**Falls Alerts/Gold-Update ausbleiben**: zuerst bei cron-job.org pruefen, ob die Cronjobs
aktiv sind und ob der letzte Lauf erfolgreich war (Token evtl. abgelaufen/widerrufen).

**Falls News-Update laenger ausbleibt, obwohl die GitHub-Actions-Laeufe "success" zeigen**:
`GROQ_API_KEY` kann abgelaufen sein (Groq-Keys hatten schon mal ein Ablaufdatum, das man beim
Erstellen explizit auf "No expiration" stellen sollte). Symptom im Actions-Log: `[WARN]
Groq-Relevanz-Check fehlgeschlagen ... expired_api_key`. Fix: neuen Key bei console.groq.com
(nicht console.grok.com!) erstellen, `GROQ_API_KEY`-Secret in GitHub aktualisieren.

**Kursdaten-Quelle**: `bot.py` nutzt `Ticker.get_info()` (regularMarketPrice/
regularMarketPreviousClose), nicht `fast_info` - Letzteres lieferte bei manchen Tickern
(beobachtet bei TSLA) einen falschen `previous_close` und produzierte dadurch False-Positive-
Alerts (Kurs + %-Aenderung stimmten nicht mit echten Boersendaten ueberein). Nebeneffekt:
der angezeigte Kurs ist der letzte **regulaere Sitzungsschluss**, nicht der Pre-/After-Market-
Preis - waehrend US-Vorboerse (vor 15:30 Uhr dt. Zeit) kann der angezeigte Wert daher leicht
von Echtzeit-Kursen bei europaeischen Brokern (z.B. Trade Republic/Tradegate) abweichen, die
Pre-Market-Bewegungen schon einpreisen. Ist so gewollt (vermeidet False-Positives), kein Bug.

## Secrets (GitHub Repo Settings -> Secrets and variables -> Actions)

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GROQ_API_KEY` (Groq API Key von console.groq.com, kostenloser Free-Tier, fuer `news_check.py`)

## Lokale Anpassung

- Watchlist/Schwellenwerte: `WATCHLIST`-Liste in `bot.py` aendern, committen, pushen.
- News-Kategorien/Suchbegriffe: `NEWS_CATEGORIES`-Dict in `news_check.py` aendern.
