#!/usr/bin/env python3
"""News-/Politik-Trigger: sammelt Marktnews per RSS, filtert per Groq-LLM, sendet gebuendelt."""
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
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

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
    Laeufen gesehen wurden (verhindert doppelte Meldungen).
    Speichert den Seen-Status NICHT selbst - das passiert erst in main(), nachdem
    die Headlines erfolgreich verarbeitet (Gemini-Check + ggf. Versand) wurden.
    So werden Headlines bei einem Fehler (z.B. Gemini-Quota) beim naechsten Lauf
    erneut versucht, statt fälschlich als 'erledigt' markiert zu werden."""
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
        if fresh:
            by_category[category] = fresh

    return by_category


def mark_as_seen(by_category: dict) -> None:
    state = load_state()
    seen = set(state["seen_links"])
    for items in by_category.values():
        for item in items:
            seen.add(item["link"])
    state["seen_links"] = list(seen)
    save_state(state)


def ask_llm_for_relevance(by_category: dict) -> str:
    """Schickt die Headlines an Groq (Llama), laesst nur marktrelevante zusammenfassen.
    Gibt fertigen Telegram-Text zurueck, oder einen leeren String, wenn nichts relevant ist."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY fehlt (als Umgebungsvariable/Secret setzen).")

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
    data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
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
    if resp.status_code == 400:
        # Von der KI generierter Text kann ungueltige/unausgeglichene Markdown-Zeichen
        # enthalten - dann als Klartext nachsenden statt die Nachricht ganz zu verlieren.
        print("[WARN] Markdown-Versand fehlgeschlagen, sende als Klartext nach.", file=sys.stderr)
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    resp.raise_for_status()


def main() -> None:
    by_category = collect_new_headlines()
    if not by_category:
        print("[NEWS] Keine neuen Headlines gefunden.")
        return

    try:
        summary = ask_llm_for_relevance(by_category)
    except Exception as exc:
        print(f"[WARN] Groq-Relevanz-Check fehlgeschlagen, versuche es beim naechsten Lauf erneut: {exc}", file=sys.stderr)
        return

    if not summary:
        # Erfolgreich geprueft, nur nichts Relevantes dabei - jetzt als 'gesehen' markieren.
        mark_as_seen(by_category)
        print("[NEWS] Nichts davon als marktrelevant eingestuft.")
        return

    text = f"\U0001F4F0 *Markt-News Update*\n\n{summary}"
    try:
        send_telegram(text)
    except Exception as exc:
        print(f"[WARN] Telegram-Versand fehlgeschlagen, versuche es beim naechsten Lauf erneut: {exc}", file=sys.stderr)
        return

    # Erst nach erfolgreichem Versand als 'gesehen' markieren.
    mark_as_seen(by_category)
    print("[NEWS] Update gesendet.")


if __name__ == "__main__":
    main()
