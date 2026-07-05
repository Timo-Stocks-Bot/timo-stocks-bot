#!/usr/bin/env python3
"""Telegram-Bot fuer Markt- und Kursalarme (yfinance -> Telegram Bot API)."""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

STATE_FILE = Path(__file__).parent / "state.json"
TIMEZONE = ZoneInfo("Europe/Berlin")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

GOLD_TICKER = "GC=F"  # COMEX Gold-Future, praktischster yfinance-Proxy fuer XAU/USD

# Watchlist: hier Symbole/Schwellenwerte anpassen oder erweitern.
WATCHLIST = [
    {"name": "Bitcoin", "ticker": "BTC-USD", "threshold": 6.0},
    {"name": "Solana", "ticker": "SOL-USD", "threshold": 8.0},
    {"name": "NVIDIA", "ticker": "NVDA", "threshold": 5.0},
    {"name": "Sued MicroTec", "ticker": "SMHN.DE", "threshold": 5.0},
    {"name": "Nebius Group A", "ticker": "NBIS", "threshold": 5.0},
    {"name": "Lumentum Holdings", "ticker": "LITE", "threshold": 5.0},
    {"name": "Coherent Corp", "ticker": "COHR", "threshold": 5.0},
    {"name": "Rekor Systems", "ticker": "REKR", "threshold": 5.0},
    {"name": "S&P 500", "ticker": "^GSPC", "threshold": 3.0},
    {"name": "Nasdaq Composite", "ticker": "^IXIC", "threshold": 3.0},
    {"name": "DAX", "ticker": "^GDAXI", "threshold": 3.0},
    # "Big Caps" - Einkaufschance-Signal ab +/-4%. Liste nach Belieben anpassen.
    {"name": "Apple", "ticker": "AAPL", "threshold": 4.0},
    {"name": "Microsoft", "ticker": "MSFT", "threshold": 4.0},
    {"name": "Alphabet", "ticker": "GOOGL", "threshold": 4.0},
    {"name": "Amazon", "ticker": "AMZN", "threshold": 4.0},
    {"name": "Meta Platforms", "ticker": "META", "threshold": 4.0},
    {"name": "Tesla", "ticker": "TSLA", "threshold": 4.0},
]


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN oder TELEGRAM_CHAT_ID fehlt (als Umgebungsvariable/Secret setzen)."
        )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    resp.raise_for_status()


def get_price_change(ticker: str) -> tuple[float, float, float]:
    """Liefert (aktueller_kurs, vortagesschluss, veraenderung_in_prozent)."""
    t = yf.Ticker(ticker)
    try:
        fast = t.fast_info
        current = float(fast["last_price"])
        prev_close = float(fast["previous_close"])
    except Exception:
        hist = t.history(period="5d", interval="1d")
        if len(hist) < 2:
            raise RuntimeError(f"Nicht genug Kursdaten fuer {ticker}")
        current = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
    pct_change = (current - prev_close) / prev_close * 100
    return current, prev_close, pct_change


def load_state() -> dict:
    today = datetime.now(TIMEZONE).date().isoformat()
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    else:
        state = {}
    if state.get("date") != today:
        state = {"date": today, "alerted": {}}
    state.setdefault("alerted", {})
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def run_alerts() -> None:
    state = load_state()
    for item in WATCHLIST:
        name, ticker, threshold = item["name"], item["ticker"], item["threshold"]
        try:
            current, prev_close, pct_change = get_price_change(ticker)
        except Exception as exc:
            print(f"[WARN] Konnte {ticker} nicht abrufen: {exc}", file=sys.stderr)
            continue

        already_alerted = state["alerted"].get(ticker, False)
        if abs(pct_change) >= threshold and not already_alerted:
            direction = "\U0001F7E2" if pct_change > 0 else "\U0001F534"
            text = (
                f"{direction} *{name}* ({ticker})\n"
                f"Kurs: {current:.2f}\n"
                f"Tagesveraenderung: {pct_change:+.2f}% (Schwelle: {threshold}%)"
            )
            send_telegram(text)
            state["alerted"][ticker] = True
            print(f"[ALERT] {name}: {pct_change:+.2f}%")
        else:
            print(
                f"[OK] {name}: {pct_change:+.2f}% "
                f"(Schwelle {threshold}%, bereits gemeldet: {already_alerted})"
            )

    save_state(state)


def run_gold_update() -> None:
    current, prev_close, pct_change = get_price_change(GOLD_TICKER)
    direction = "\U0001F7E2" if pct_change >= 0 else "\U0001F534"
    text = (
        f"\U0001F947 *Gold Tagesupdate*\n"
        f"Kurs: {current:.2f} USD\n"
        f"Veraenderung: {direction} {pct_change:+.2f}%"
    )
    send_telegram(text)
    print(f"[GOLD] {current:.2f} USD ({pct_change:+.2f}%)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["alerts", "gold"], required=True)
    args = parser.parse_args()

    if args.mode == "alerts":
        run_alerts()
    elif args.mode == "gold":
        run_gold_update()


if __name__ == "__main__":
    main()
