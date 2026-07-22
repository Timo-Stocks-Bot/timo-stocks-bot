#!/usr/bin/env python3
"""Telegram-Bot fuer Markt- und Kursalarme (yfinance -> Telegram Bot API)."""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

import requests
import yfinance as yf

STATE_FILE = Path(__file__).parent / "state.json"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

GOLD_TICKER = "GC=F"  # COMEX Gold-Future, praktischster yfinance-Proxy fuer XAU/USD
FX_TICKER = "EURUSD=X"  # fuer USD -> EUR Umrechnung der Anzeige

# Watchlist: hier Symbole/Schwellenwerte anpassen oder erweitern.
# currency: "USD" (wird fuer die Anzeige nach EUR umgerechnet), "EUR" (bereits Euro),
#           "INDEX" (Indexpunkte, keine Waehrung/Umrechnung)
WATCHLIST = [
    {"name": "Gold", "ticker": GOLD_TICKER, "threshold": 5.0, "currency": "USD"},
    {"name": "Bitcoin", "ticker": "BTC-USD", "threshold": 6.0, "currency": "USD"},
    {"name": "Solana", "ticker": "SOL-USD", "threshold": 8.0, "currency": "USD"},
    {"name": "NVIDIA", "ticker": "NVDA", "threshold": 5.0, "currency": "USD"},
    {"name": "Süss MicroTec SE", "ticker": "SMHN.DE", "threshold": 5.0, "currency": "EUR"},
    {"name": "Nebius Group A", "ticker": "NBIS", "threshold": 5.0, "currency": "USD"},
    {"name": "Lumentum Holdings", "ticker": "LITE", "threshold": 5.0, "currency": "USD"},
    {"name": "Coherent Corp", "ticker": "COHR", "threshold": 5.0, "currency": "USD"},
    {"name": "Rekor Systems", "ticker": "REKR", "threshold": 5.0, "currency": "USD"},
    {"name": "S&P 500", "ticker": "^GSPC", "threshold": 3.0, "currency": "INDEX"},
    {"name": "Nasdaq Composite", "ticker": "^IXIC", "threshold": 3.0, "currency": "INDEX"},
    {"name": "DAX", "ticker": "^GDAXI", "threshold": 3.0, "currency": "INDEX"},
    # "Big Caps" - Einkaufschance-Signal ab +/-4%. Liste nach Belieben anpassen.
    {"name": "Apple", "ticker": "AAPL", "threshold": 4.0, "currency": "USD"},
    {"name": "Microsoft", "ticker": "MSFT", "threshold": 4.0, "currency": "USD"},
    {"name": "Alphabet", "ticker": "GOOGL", "threshold": 4.0, "currency": "USD"},
    {"name": "Amazon", "ticker": "AMZN", "threshold": 4.0, "currency": "USD"},
    {"name": "Meta Platforms", "ticker": "META", "threshold": 4.0, "currency": "USD"},
    {"name": "Tesla", "ticker": "TSLA", "threshold": 4.0, "currency": "USD"},
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


def get_eur_per_usd() -> Optional[float]:
    """Liefert, wieviel Euro einem US-Dollar entsprechen, oder None falls nicht abrufbar."""
    try:
        current, _, _ = get_price_change(FX_TICKER)  # EURUSD=X: USD je 1 EUR
        return 1.0 / current
    except Exception as exc:
        print(f"[WARN] Konnte Wechselkurs nicht abrufen: {exc}", file=sys.stderr)
        return None


def format_price(value: float, currency: str, eur_per_usd: Optional[float]) -> str:
    if currency == "USD":
        if eur_per_usd is not None:
            return f"{value * eur_per_usd:.2f} EUR"
        return f"{value:.2f} USD"
    if currency == "EUR":
        return f"{value:.2f} EUR"
    return f"{value:.2f} Pkt."


def load_state() -> dict:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    else:
        state = {}
    state.setdefault("tickers", {})
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def run_alerts() -> None:
    state = load_state()
    eur_per_usd = get_eur_per_usd()
    for item in WATCHLIST:
        name, ticker, threshold = item["name"], item["ticker"], item["threshold"]
        currency = item.get("currency", "USD")
        try:
            current, prev_close, pct_change = get_price_change(ticker)
        except Exception as exc:
            print(f"[WARN] Konnte {ticker} nicht abrufen: {exc}", file=sys.stderr)
            continue

        # Alerted-Status ist an den prev_close gebunden, nicht an das Kalenderdatum:
        # aendert sich prev_close nicht (Markt noch geschlossen, z.B. kurz nach Mitternacht
        # oder am Wochenende bei Aktien), bleibt der Status bestehen und es wird nicht erneut
        # fuer dieselbe, unveraenderte Bewegung gemeldet.
        ticker_state = state["tickers"].get(ticker, {})
        if ticker_state.get("prev_close") != prev_close:
            ticker_state = {"prev_close": prev_close, "alerted": False}

        already_alerted = ticker_state["alerted"]
        if abs(pct_change) >= threshold and not already_alerted:
            direction = "\U0001F7E2" if pct_change > 0 else "\U0001F534"
            price_str = format_price(current, currency, eur_per_usd)
            text = (
                f"{direction} *{name}* ({ticker})\n"
                f"Kurs: {price_str}\n"
                f"Tagesveraenderung: {pct_change:+.2f}% (Schwelle: {threshold}%)"
            )
            send_telegram(text)
            ticker_state["alerted"] = True
            print(f"[ALERT] {name}: {pct_change:+.2f}%")
        else:
            print(
                f"[OK] {name}: {pct_change:+.2f}% "
                f"(Schwelle {threshold}%, bereits gemeldet: {already_alerted})"
            )

        state["tickers"][ticker] = ticker_state

    save_state(state)


def run_gold_update() -> None:
    current, prev_close, pct_change = get_price_change(GOLD_TICKER)
    eur_per_usd = get_eur_per_usd()
    price_str = format_price(current, "USD", eur_per_usd)
    direction = "\U0001F7E2" if pct_change >= 0 else "\U0001F534"
    text = (
        f"\U0001F947 *Gold Tagesupdate*\n"
        f"Kurs: {price_str}\n"
        f"Veraenderung: {direction} {pct_change:+.2f}%"
    )
    send_telegram(text)
    print(f"[GOLD] {price_str} ({pct_change:+.2f}%)")


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
