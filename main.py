import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import requests
import yfinance as yf

app = FastAPI(title="Beurs Watchlist & Alert API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Vul hier jouw gekozen Ntfy kanaalnaam in
NTFY_KANAAL = "beurs_alerts_piet_9872"


class AlertAanvraag(BaseModel):
    tickers: list[str]


def verstuur_pushmelding(titel: str, bericht: str) -> bool:
    """Stuurt een gratis pushmelding naar de Ntfy app op je telefoon."""
    try:
        response = requests.post(
            f"https://ntfy.sh/{NTFY_KANAAL}",
            data=bericht.encode("utf-8"),
            headers={
                "Title": titel,
                "Priority": "high",
                "Tags": "chart_with_upwards_trend,warning",
            },
            timeout=5,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Fout bij versturen pushmelding: {e}")
        return False


def verwerk_ticker_of_isin(input_str: str) -> str:
    """Zet een ISIN-code automatisch om naar de juiste Yahoo Finance ticker.

    Verwerkt ook normale tickers en Xetra-extensies.
    """
    schoon = input_str.upper().strip()

    # 1. Controleer of het een ISIN-formaat is (bijv. IE0002631328)
    is_isin = bool(re.match(r"^[A-Z]{2}[A-Z0-9]{10}$", schoon))

    if is_isin:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={schoon}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            respons = requests.get(url, headers=headers, timeout=5)
            data = respons.json()
            quotes = data.get("quotes", [])

            if quotes and "symbol" in quotes[0]:
                gevonden_ticker = quotes[0]["symbol"]
                print(f"ISIN {schoon} omgezet naar ticker: {gevonden_ticker}")
                return gevonden_ticker
        except Exception as e:
            print(f"Fout bij opzoeken ISIN {schoon}: {e}")

    # 2. Omzetten van Xetra notatie (bijv. JEDI:XETR -> JEDI.DE)
    if ":XETR" in schoon or ":XETRA" in schoon:
        return schoon.replace(":XETRA", ".DE").replace(":XETR", ".DE")

    return schoon


@app.get("/api/grafiek/{ticker_symbool}")
def haal_grafiek_data_op(ticker_symbool: str):
    gecorrigeerde_ticker = verwerk_ticker_of_isin(ticker_symbool)
    ticker_object = yf.Ticker(gecorrigeerde_ticker)

    # 5 jaar historie ophalen voor een complete 200 SMA vanaf dag 1
    df = ticker_object.history(period="5y", interval="1d")

    if df.empty or len(df) < 50:
        raise HTTPException(
            status_code=404, detail="Geen koersdata gevonden."
        )

    # SMA's berekenen op de volledige dataset
    df["SMA_50"] = df["Close"].rolling(window=50).mean()
    df["SMA_200"] = df["Close"].rolling(window=200).mean()

    df = df.reset_index()
    df["time"] = df["Date"].dt.strftime("%Y-%m-%d")

    candles = []
    sma50_data = []
    sma200_data = []

    for _, row in df.iterrows():
        candles.append({
            "time": row["time"],
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
        })

        if pd.notna(row["SMA_50"]):
            sma50_data.append({
                "time": row["time"],
                "value": round(float(row["SMA_50"]), 2),
            })
        if pd.notna(row["SMA_200"]):
            sma200_data.append({
                "time": row["time"],
                "value": round(float(row["SMA_200"]), 2),
            })

    laatste_sma50 = df["SMA_50"].iloc[-1]
    laatste_sma200 = df["SMA_200"].iloc[-1]
    is_bullish = (
        bool(laatste_sma50 > laatste_sma200)
        if pd.notna(laatste_sma50) and pd.notna(laatste_sma200)
        else False
    )

    return {
        "ticker": gecorrigeerde_ticker,
        "candles": candles,
        "sma_50": sma50_data,
        "sma_200": sma200_data,
        "is_bullish": is_bullish,
        "laatste_koers": round(float(df["Close"].iloc[-1]), 2),
    }


@app.post("/api/controleer-crosses")
def controleer_watchlist_crosses(aanvraag: AlertAanvraag):
    gevonden_alerts = []

    for ticker in aanvraag.tickers:
        gecorrigeerde_ticker = verwerk_ticker_of_isin(ticker)
        ticker_obj = yf.Ticker(gecorrigeerde_ticker)
        df = ticker_obj.history(period="2y", interval="1d")

        if len(df) < 201:
            continue

        df["SMA_50"] = df["Close"].rolling(window=50).mean()
        df["SMA_200"] = df["Close"].rolling(window=200).mean()

        vandaag_sma50 = df["SMA_50"].iloc[-1]
        vandaag_sma200 = df["SMA_200"].iloc[-1]
        gisteren_sma50 = df["SMA_50"].iloc[-2]
        gisteren_sma200 = df["SMA_200"].iloc[-2]

        if gisteren_sma50 <= gisteren_sma200 and vandaag_sma50 > vandaag_sma200:
            gevonden_alerts.append(
                f"🚀 {gecorrigeerde_ticker}: GOLDEN CROSS gedetecteerd!"
            )
        elif (
            gisteren_sma50 >= gisteren_sma200 and vandaag_sma50 < vandaag_sma200
        ):
            gevonden_alerts.append(
                f"⚠️ {gecorrigeerde_ticker}: DEATH CROSS gedetecteerd!"
            )

    if gevonden_alerts:
        bericht_tekst = "\n".join(gevonden_alerts)
        verstuur_pushmelding("🚨 Beurs Alert!", bericht_tekst)
        return {"status": "Alerts verzonden", "meldingen": gevonden_alerts}

    return {"status": "Geen kruisingen gedetecteerd"}