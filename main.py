import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

DEPARTURE_DATE = "2026-10-19"
RETURN_DATE = "2026-10-30"
TARGET_PRICE_TRY = 10_000.0
DROP_THRESHOLD = 0.05
STATE_FILE = Path("state.json")

AIRLINES = {
    "VF": "AJet",
    "PC": "Pegasus",
    "TK": "Türk Hava Yolları",
    "J2": "Azerbaijan Airlines (AZAL)",
}

# Amadeus Self-Service production endpoint. Use test.api.amadeus.com while testing.
AMADEUS_BASE_URL = os.getenv("AMADEUS_BASE_URL", "https://api.amadeus.com").rstrip("/")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Eksik ortam değişkeni: {name}")
    return value


def get_amadeus_token() -> str:
    response = requests.post(
        f"{AMADEUS_BASE_URL}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": require_env("AMADEUS_CLIENT_ID"),
            "client_secret": require_env("AMADEUS_CLIENT_SECRET"),
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def search_offers(origin: str, airline_code: str, token: str) -> list[dict[str, Any]]:
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": "GYD",
        "departureDate": DEPARTURE_DATE,
        "returnDate": RETURN_DATE,
        "adults": 1,
        "currencyCode": "TRY",
        "includedAirlineCodes": airline_code,
        "max": 50,
    }
    response = requests.get(
        f"{AMADEUS_BASE_URL}/v2/shopping/flight-offers",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=45,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def parse_baggage(offer: dict[str, Any]) -> str:
    allowances: list[str] = []
    for pricing in offer.get("travelerPricings", []):
        for fare in pricing.get("fareDetailsBySegment", []):
            baggage = fare.get("includedCheckedBags") or {}
            if "weight" in baggage:
                allowances.append(f"{baggage['weight']} {baggage.get('weightUnit', 'KG')}")
            elif "quantity" in baggage:
                allowances.append(f"{baggage['quantity']} parça")
    if not allowances:
        return "Bilgi yok"
    return ", ".join(sorted(set(allowances)))


def itinerary_summary(itinerary: dict[str, Any]) -> str:
    segments = itinerary.get("segments", [])
    if not segments:
        return "Uçuş bilgisi yok"
    first = segments[0]
    last = segments[-1]
    dep = first.get("departure", {})
    arr = last.get("arrival", {})
    carriers = "/".join(segment.get("carrierCode", "?") for segment in segments)
    flight_numbers = "/".join(
        f"{segment.get('carrierCode', '')}{segment.get('number', '')}" for segment in segments
    )
    stops = max(len(segments) - 1, 0)
    return (
        f"{dep.get('iataCode', '?')} {dep.get('at', '?')} → "
        f"{arr.get('iataCode', '?')} {arr.get('at', '?')} | "
        f"{flight_numbers} | {stops} aktarma | taşıyıcı {carriers}"
    )


def normalize_offer(offer: dict[str, Any], airline_code: str, origin: str) -> dict[str, Any]:
    itineraries = offer.get("itineraries", [])
    total = float(offer.get("price", {}).get("grandTotal") or offer.get("price", {}).get("total"))
    return {
        "airline_code": airline_code,
        "airline": AIRLINES[airline_code],
        "origin": origin,
        "price_try": total,
        "outbound": itinerary_summary(itineraries[0]) if len(itineraries) > 0 else "Bilgi yok",
        "return": itinerary_summary(itineraries[1]) if len(itineraries) > 1 else "Bilgi yok",
        "baggage": parse_baggage(offer),
        "source": "Amadeus Flight Offers Search API",
    }


def collect_best_by_airline() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    token = get_amadeus_token()
    best: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}

    for code, name in AIRLINES.items():
        candidates: list[dict[str, Any]] = []
        for origin in ("IST", "SAW"):
            try:
                offers = search_offers(origin, code, token)
                candidates.extend(normalize_offer(offer, code, origin) for offer in offers)
            except requests.RequestException as exc:
                errors[f"{name} / {origin}"] = str(exc)

        if candidates:
            best[code] = min(candidates, key=lambda item: item["price_try"])

    return best, errors


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"lowest_price_try": None, "history": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"lowest_price_try": None, "history": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text: str) -> None:
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    response.raise_for_status()


def build_alert(best_offer: dict[str, Any], previous_low: float | None, reason: str) -> str:
    previous_text = "İlk kayıt" if previous_low is None else f"{previous_low:,.2f} TL"
    return (
        "✈️ Uçuş fiyat alarmı\n\n"
        f"Neden: {reason}\n"
        f"Havayolu: {best_offer['airline']}\n"
        f"Toplam: {best_offer['price_try']:,.2f} TL\n"
        f"Önceki en düşük: {previous_text}\n"
        f"Gidiş: {best_offer['outbound']}\n"
        f"Dönüş: {best_offer['return']}\n"
        f"Bagaj: {best_offer['baggage']}\n"
        f"Kaynak: {best_offer['source']}\n"
        f"Rota: {best_offer['origin']} → GYD / GYD → İstanbul\n"
        f"Tarihler: {DEPARTURE_DATE} – {RETURN_DATE}"
    )


def main() -> int:
    today = date.today()
    if today > date.fromisoformat(DEPARTURE_DATE):
        print("Takip dönemi sona erdi.")
        return 0

    best_by_airline, errors = collect_best_by_airline()
    if not best_by_airline:
        print("Hiç uçuş sonucu alınamadı.")
        for key, value in errors.items():
            print(f"{key}: {value}")
        return 1

    overall_best = min(best_by_airline.values(), key=lambda item: item["price_try"])
    state = load_state()
    previous_low_raw = state.get("lowest_price_try")
    previous_low = float(previous_low_raw) if previous_low_raw is not None else None
    current = float(overall_best["price_try"])

    reasons: list[str] = []
    if current <= TARGET_PRICE_TRY:
        reasons.append(f"Fiyat hedef olan {TARGET_PRICE_TRY:,.0f} TL veya altına indi")
    if previous_low is not None and current <= previous_low * (1 - DROP_THRESHOLD):
        drop_pct = ((previous_low - current) / previous_low) * 100
        reasons.append(f"Önceki en düşük fiyata göre %{drop_pct:.1f} düştü")

    timestamp = datetime.now(timezone.utc).isoformat()
    state.setdefault("history", []).append(
        {
            "checked_at_utc": timestamp,
            "overall_best": overall_best,
            "airlines": best_by_airline,
            "errors": errors,
        }
    )
    state["history"] = state["history"][-120:]
    if previous_low is None or current < previous_low:
        state["lowest_price_try"] = current
        state["lowest_offer"] = overall_best
        state["lowest_recorded_at_utc"] = timestamp
    state["last_checked_at_utc"] = timestamp
    state["last_best_offer"] = overall_best
    save_state(state)

    print(json.dumps({"best_by_airline": best_by_airline, "errors": errors}, ensure_ascii=False, indent=2))

    if reasons:
        send_telegram(build_alert(overall_best, previous_low, " + ".join(reasons)))
        print("Telegram bildirimi gönderildi.")
    else:
        print("Bildirim eşiği oluşmadı; günlük kayıt güncellendi.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        sys.exit(1)
