#!/usr/bin/env python3
"""Screener fuer aussergewoehnliche Kurstuerze im S&P 500 (nicht auf die normale
Watchlist beschraenkt). Meldet nur FAKTEN + evtl. gefundenen Ausloeser per News-Suche -
keine Kauf-/Verkaufsempfehlung."""
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import requests
import yfinance as yf

from sp500_tickers import SP500_TICKERS

STATE_FILE = Path(__file__).parent / "movers_state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Ab dieser Tagesbewegung (in Prozent, negativ) gilt eine Aktie als "extremer Sturz".
# Bewusst deutlich hoeher als die normalen Watchlist-Schwellen (3-8%), da hier gezielt
# nach echten Ausreissern gesucht wird, nicht nach normaler Volatilitaet.
EXTREME_DROP_THRESHOLD = -10.0


def fetch_sp500_changes() -> dict:
    """Liefert {ticker: (current, prev_close, pct_change)} fuer alle S&P-500-Ticker,
    per Batch-Download (deutlich schneller als 500 Einzelabfragen)."""
    tickers = list(SP500_TICKERS.keys())
    data = yf.download(
        tickers=tickers,
        period="5d",
        interval="1d",
        group_by="ticker",
        threads=True,
        progress=False,
    )

    results = {}
    for ticker in tickers:
        try:
            closes = data[ticker]["Close"].dropna()
            if len(closes) < 2:
                continue
            current = float(closes.iloc[-1])
            prev_close = float(closes.iloc[-2])
            pct_change = (current - prev_close) / prev_close * 100
            results[ticker] = (current, prev_close, pct_change)
        except Exception as exc:
            print(f"[WARN] Konnte {ticker} nicht auswerten: {exc}", file=sys.stderr)
    return results


def fetch_headlines(company_name: str) -> list:
    query = f"{company_name} Aktie"
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=de&gl=DE&ceid=DE:de"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return [item.findtext("title", "") for item in root.findall(".//item")][:5]


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"tickers": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def ask_llm_for_context(flagged: list) -> str:
    """flagged: Liste von dicts mit ticker/name/pct_change/headlines.
    Liefert fertigen Telegram-Text (nur Fakten/moeglicher Ausloeser, keine Empfehlung)."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY fehlt (als Umgebungsvariable/Secret setzen).")

    lines = []
    for item in flagged:
        lines.append(f"### {item['name']} ({item['ticker']}), Tagesveraenderung: {item['pct_change']:+.2f}%")
        for h in item["headlines"]:
            lines.append(f"- {h}")
        if not item["headlines"]:
            lines.append("(keine Schlagzeilen gefunden)")

    prompt = (
        "Fuer einen persoenlichen Telegram-Bot: die folgenden S&P-500-Aktien hatten heute einen "
        "aussergewoehnlich starken Kurssturz (>=10%). Fasse pro Aktie in 1-2 Saetzen auf Deutsch "
        "zusammen, was laut den Schlagzeilen der wahrscheinliche Ausloeser war. Falls die "
        "Schlagzeilen keinen klaren Grund liefern, schreib das ehrlich so "
        "('kein klarer Ausloeser aus den Schlagzeilen ersichtlich').\n\n"
        "WICHTIG: Gib KEINE Kauf-/Verkaufsempfehlung und keine Einschaetzung, ob sich ein "
        "Investment lohnt - nur die Fakten zum Kursverlauf und zum moeglichen Ausloeser. "
        "Antworte NUR mit fertigem Telegram-Text (Markdown), ein Abschnitt pro Aktie.\n\n"
        f"{chr(10).join(lines)}"
    )

    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Groq API {resp.status_code}: {resp.text[:500]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN oder TELEGRAM_CHAT_ID fehlt.")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    if resp.status_code == 400:
        print("[WARN] Markdown-Versand fehlgeschlagen, sende als Klartext nach.", file=sys.stderr)
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
    resp.raise_for_status()


def main() -> None:
    state = load_state()
    changes = fetch_sp500_changes()

    flagged = []
    for ticker, (current, prev_close, pct_change) in changes.items():
        if pct_change > EXTREME_DROP_THRESHOLD:
            continue

        ticker_state = state["tickers"].get(ticker, {})
        if ticker_state.get("prev_close") != prev_close:
            ticker_state = {"prev_close": prev_close, "alerted": False}

        if ticker_state["alerted"]:
            state["tickers"][ticker] = ticker_state
            continue

        name = SP500_TICKERS.get(ticker, ticker)
        try:
            headlines = fetch_headlines(name)
        except Exception as exc:
            print(f"[WARN] Konnte Headlines fuer {name} nicht abrufen: {exc}", file=sys.stderr)
            headlines = []

        flagged.append({
            "ticker": ticker, "name": name, "pct_change": pct_change, "headlines": headlines,
        })
        ticker_state["alerted"] = True
        state["tickers"][ticker] = ticker_state
        print(f"[FLAG] {name} ({ticker}): {pct_change:+.2f}%")

    if not flagged:
        print("[OK] Keine extremen Ausreisser (< {:.0f}%) gefunden.".format(EXTREME_DROP_THRESHOLD))
        save_state(state)
        return

    try:
        summary = ask_llm_for_context(flagged)
    except Exception as exc:
        print(f"[WARN] Groq-Kontext-Check fehlgeschlagen, versuche es beim naechsten Lauf erneut: {exc}", file=sys.stderr)
        return  # State NICHT speichern -> naechster Lauf versucht es erneut

    text = f"\U0001F4C9 *Ausreisser-Screener (S&P 500)*\n\n{summary}"
    try:
        send_telegram(text)
    except Exception as exc:
        print(f"[WARN] Telegram-Versand fehlgeschlagen, versuche es beim naechsten Lauf erneut: {exc}", file=sys.stderr)
        return

    save_state(state)
    print("[MOVERS] Update gesendet.")


if __name__ == "__main__":
    main()
