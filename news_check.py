#!/usr/bin/env python3
"""News-/Politik-Trigger: sammelt Marktnews per RSS, filtert per Gemini-LLM, sendet gebuendelt."""
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import requests

STATE_FILE = Path(__file__).parent / "news_state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Kategorie -> Google-News-RSS-Suchanfrage. Bei Bedarf anpassen/erweitern.
NEWS_CATEGORIES = {
    "Edelmetalle": "Fed Zinsentscheidung OR Goldpreis Zentralbank OR Inflation Gold",
    "Krypto": "Bitcoin SEC Regulierung OR Krypto ETF Zufluss OR Krypto Boerse Hack",
    "Tech/KI": "Chip Exportverbot OR KI Kartellrecht OR Nvidia Quartalszahlen Guidance",
}

SEEN_LIMIT = 500  # max. Anzahl gemerkter Links, aeltere werden verworfen


def fetch_headlines(query: str) -> list:
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=de&gl=DE&ceid=DE:de"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        items.append({"title": title, "link": link})
    return items


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen_links": []}


def save_state(state: dict) -> None:
    state["seen_links"] = state["seen_links"][-SEEN_LIMIT:]
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def collect_new_headlines() -> dict:
    """Liefert {kategorie: [neue headlines]} - nur Links, die noch nicht in vorherigen
    Laeufen gesehen wurden (verhindert doppelte Meldungen)."""
    state = load_state()
    seen = set(state["seen_links"])
    by_category = {}
    for category, query in NEWS_CATEGORIES.items():
        try:
            headlines = fetch_headlines(query)
        except Exception as exc:
            print(f"[WARN] Konnte News fuer '{category}' nicht abrufen: {exc}", file=sys.stderr)
            continue
        fresh = [h for h in headlines if h["link"] not in seen]
        for h in fresh:
            seen.add(h["link"])
        if fresh:
            by_category[category] = fresh

    state["seen_links"] = list(seen)
    save_state(state)
    return by_category


def ask_gemini_for_relevance(by_category: dict) -> str:
    """Schickt die Headlines an Gemini, laesst nur marktrelevante zusammenfassen.
    Gibt fertigen Telegram-Text zurueck, oder einen leeren String, wenn nichts relevant ist."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY fehlt (als Umgebungsvariable/Secret setzen).")

    lines = []
    for category, items in by_category.items():
        lines.append(f"### {category}")
        for item in items:
            lines.append(f"- {item['title']}")
    headlines_text = "\n".join(lines)

    prompt = (
        "Du filterst Finanz-News fuer einen persoenlichen Telegram-Alert-Bot. "
        "Hier ist eine Liste aktueller Headlines, gruppiert nach Kategorie:\n\n"
        f"{headlines_text}\n\n"
        "Waehle NUR die Headlines aus, die eine echte, konkrete Marktrelevanz haben "
        "(z.B. Zentralbank-Zinsentscheidungen, grosse geopolitische Ereignisse, "
        "Regulierungsentscheidungen, wichtige Unternehmens-/Quartalszahlen-News mit "
        "Kursrelevanz). Ignoriere Boulevard, Wiederholungen, vage/spekulative Artikel.\n\n"
        "Antworte NUR mit fertigem Telegram-Text (Markdown), gruppiert nach Kategorie, "
        "pro relevanter Meldung ein Aufzaehlungspunkt mit 1-2 Satz Zusammenfassung auf Deutsch. "
        "Wenn NICHTS davon wirklich marktrelevant ist, antworte exakt mit: KEINE_RELEVANTEN_NEWS"
    )

    resp = requests.post(
        f"{GEMINI_URL}?key={GOOGLE_API_KEY}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Gemini API {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    if "KEINE_RELEVANTEN_NEWS" in text:
        return ""
    return text


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN oder TELEGRAM_CHAT_ID fehlt.")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    resp.raise_for_status()


def main() -> None:
    by_category = collect_new_headlines()
    if not by_category:
        print("[NEWS] Keine neuen Headlines gefunden.")
        return

    try:
        summary = ask_gemini_for_relevance(by_category)
    except Exception as exc:
        print(f"[WARN] Gemini-Relevanz-Check fehlgeschlagen: {exc}", file=sys.stderr)
        return

    if not summary:
        print("[NEWS] Nichts davon als marktrelevant eingestuft.")
        return

    text = f"\U0001F4F0 *Markt-News Update*\n\n{summary}"
    send_telegram(text)
    print("[NEWS] Update gesendet.")


if __name__ == "__main__":
    main()
