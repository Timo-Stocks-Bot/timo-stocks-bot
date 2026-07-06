# Timo Stocks Bot

Telegram-Bot, der ueber `bot.py` Kurs-/Marktbewegungen prueft und bei Ueberschreitung
von Schwellenwerten (siehe `WATCHLIST` in `bot.py`) eine Nachricht schickt. Gold bekommt
zusaetzlich ein taegliches Fixzeit-Update.

## Wichtig: Automatisierung laeuft ueber cron-job.org, nicht ueber GitHubs eigenen Zeitplan

GitHubs eingebauter `schedule:`-Trigger in den Workflows (`.github/workflows/*.yml`) hat sich
als unzuverlaessig erwiesen (Laeufe blieben stundenlang aus). Deshalb werden beide Workflows
stattdessen ueber einen externen, kostenlosen Cron-Dienst ausgeloest:

- **Account**: cron-job.org (Login: siehe eigene Zugangsdaten)
- **Cronjob "Gold Update Trigger"**: taeglich, ruft
  `POST https://api.github.com/repos/Timo-Stocks-Bot/timo-stocks-bot/actions/workflows/gold.yml/dispatches`
- **Cronjob "Market Alerts Trigger"**: alle 15-20 Min., ruft
  `POST https://api.github.com/repos/Timo-Stocks-Bot/timo-stocks-bot/actions/workflows/alerts.yml/dispatches`

Beide Cronjobs senden dabei einen GitHub **Fine-grained Personal Access Token**
(Scope: nur dieses Repo, Permission "Actions: Read and write") als
`Authorization: Bearer <token>`-Header sowie Body `{"ref":"main"}`.

**Falls Alerts/Gold-Update ausbleiben**: zuerst bei cron-job.org pruefen, ob die Cronjobs
aktiv sind und ob der letzte Lauf erfolgreich war (Token evtl. abgelaufen/widerrufen).

## Secrets (GitHub Repo Settings -> Secrets and variables -> Actions)

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Lokale Anpassung

Watchlist/Schwellenwerte direkt in `bot.py` (`WATCHLIST`-Liste) aendern, committen, pushen.
