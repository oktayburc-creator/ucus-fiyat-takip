import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

OUTBOUND_DATE = "2026-10-19"
RETURN_DATE = "2026-10-30"
STOP_DATE = date(2026, 10, 19)
TARGET_PRICE_TL = 10_000
DROP_ALERT_PERCENT = 5.0
AIRPORTS = ["IST", "SAW"]
DESTINATION = "GYD"
TARGET_AIRLINES = {
    "AJet": ["ajet", "anadolujet"],
    "Pegasus": ["pegasus", "pegasus airlines"],
    "Türk Hava Yolları": ["turkish airlines", "türk hava yolları"],
    "Azerbaijan Airlines (AZAL)": ["azerbaijan airlines", "azal"],
}
STATE_PATH = Path("state.json")
SERPAPI_URL = "https://serpapi.com/search.json"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Eksik ortam değişkeni: {name}")
    return value


def serpapi_search(params: dict[str, Any]) -> dict[str, Any]:
    params = {
        **params,
        "engine": "google_flights",
        "api_key": require_env("SERPAPI_KEY"),
        "currency": "TRY",
        "hl": "tr",
        "gl": "tr",
        "type": "1",
        "travel_class": "1",
        "deep_search": "true",
    }
    response = requests.get(SERPAPI_URL, params=params, timeout=90)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"SerpApi hatası: {data['error']}")
    return data


def all_flight_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(data.get("best_flights", [])) + list(data.get("other_flights", []))


def itinerary_airline_names(itinerary: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for segment in itinerary.get("flights", []):
        airline = str(segment.get("airline", "")).strip()
        if airline:
            names.append(airline)
    return names


def airline_matches(itinerary: dict[str, Any], canonical: str) -> bool:
    names = itinerary_airline_names(itinerary)
    if not names:
        return False
    aliases = TARGET_AIRLINES[canonical]
    return all(any(alias in name.casefold() for alias in aliases) for name in names)


def price_value(item: dict[str, Any]) -> int | None:
    value = item.get("price")
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def collect_baggage_text(item: dict[str, Any]) -> str:
    texts: list[str] = []
    candidates = list(item.get("extensions", []))
    for segment in item.get("flights", []):
        candidates.extend(segment.get("extensions", []))
    for text in candidates:
        lowered = str(text).casefold()
        if any(word in lowered for word in ("bag", "baggage", "carry-on", "checked", "valiz", "bagaj")):
            value = str(text).strip()
            if value and value not in texts:
                texts.append(value)
    return "; ".join(texts[:3]) if texts else "Bagaj bilgisi kaynakta belirtilmedi"


def leg_summary(item: dict[str, Any]) -> str:
    segments = item.get("flights", [])
    if not segments:
        return "Uçuş detayı yok"
    first = segments[0]
    last = segments[-1]
    dep = first.get("departure_airport", {})
    arr = last.get("arrival_airport", {})
    flight_numbers = [str(s.get("flight_number", "")).strip() for s in segments if s.get("flight_number")]
    route = " → ".join(
        [str(segments[0].get("departure_airport", {}).get("id", "?"))]
        + [str(s.get("arrival_airport", {}).get("id", "?")) for s in segments]
    )
    number_text = ", ".join(flight_numbers) if flight_numbers else "uçuş no yok"
    return f"{dep.get('time', '?')} | {route} | {arr.get('time', '?')} | {number_text}"


def choose_cheapest_roundtrip_for_airline(canonical: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    for origin in AIRPORTS:
        outbound_data = serpapi_search(
            {
                "departure_id": origin,
                "arrival_id": DESTINATION,
                "outbound_date": OUTBOUND_DATE,
                "return_date": RETURN_DATE,
                "show_hidden": "true",
            }
        )
        outbound_options = [
            item
            for item in all_flight_results(outbound_data)
            if airline_matches(item, canonical) and item.get("departure_token")
        ]
        outbound_options.sort(key=lambda x: price_value(x) or 10**9)

        # API kullanımını kontrollü tutmak için havayolu/çıkış havalimanı başına en iyi 3 gidişi inceler.
        for outbound in outbound_options[:3]:
            return_data = serpapi_search(
                {
                    "departure_id": origin,
                    "arrival_id": DESTINATION,
                    "outbound_date": OUTBOUND_DATE,
                    "return_date": RETURN_DATE,
                    "departure_token": outbound["departure_token"],
                }
            )
            for returning in all_flight_results(return_data):
                if not airline_matches(returning, canonical):
                    continue
                total_price = price_value(returning)
                if total_price is None:
                    continue
                candidates.append(
                    {
                        "airline": canonical,
                        "price": total_price,
                        "outbound": leg_summary(outbound),
                        "return": leg_summary(returning),
                        "baggage": collect_baggage_text(returning) if collect_baggage_text(returning) != "Bagaj bilgisi kaynakta belirtilmedi" else collect_baggage_text(outbound),
                        "source": "Google Flights sonuçları (SerpApi)",
                    }
                )

    return min(candidates, key=lambda x: x["price"]) if candidates else None


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"lowest_ever": None, "history": []}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"lowest_ever": None, "history": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def send_telegram(message: str) -> None:
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram hatası: {result}")


def format_report(results: list[dict[str, Any]], alert_reasons: list[str], previous_low: int | None) -> str:
    lines = ["✈️ İstanbul – Bakü uçuş fiyat takibi", f"📅 {OUTBOUND_DATE} gidiş / {RETURN_DATE} dönüş", ""]
    if alert_reasons:
        lines.append("🚨 ALARM: " + " | ".join(alert_reasons))
        lines.append("")

    for canonical in TARGET_AIRLINES:
        item = next((r for r in results if r["airline"] == canonical), None)
        if not item:
            lines.extend([f"{canonical}: uygun/doğrulanabilir sonuç bulunamadı.", ""])
            continue
        lines.extend(
            [
                f"{canonical}: {item['price']:,} TL".replace(",", "."),
                f"Gidiş: {item['outbound']}",
                f"Dönüş: {item['return']}",
                f"Bagaj: {item['baggage']}",
                f"Kaynak: {item['source']}",
                "",
            ]
        )

    if results:
        cheapest = min(results, key=lambda x: x["price"])
        lines.append(f"🏆 Günün en düşük fiyatı: {cheapest['airline']} — {cheapest['price']:,} TL".replace(",", "."))
        if previous_low is not None:
            lines.append(f"Önceki kayıtlı en düşük: {previous_low:,} TL".replace(",", "."))
    lines.append("Not: Fiyat ve bagaj koşulları rezervasyon anında sağlayıcı tarafından yeniden doğrulanmalıdır.")
    return "\n".join(lines)[:4096]


def main() -> int:
    today = date.today()
    if today > STOP_DATE:
        print("Takip süresi sona erdi.")
        return 0

    state = load_state()
    previous_low = state.get("lowest_ever")
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for airline in TARGET_AIRLINES:
        try:
            result = choose_cheapest_roundtrip_for_airline(airline)
            if result:
                results.append(result)
        except Exception as exc:  # Bir havayolu başarısız olsa da diğerlerini kontrol et.
            errors.append(f"{airline}: {exc}")

    if not results:
        message = "⚠️ Uçuş fiyat kontrolü tamamlanamadı. Doğrulanabilir fiyat bulunamadı.\n" + "\n".join(errors[:4])
        send_telegram(message)
        return 1

    cheapest = min(results, key=lambda x: x["price"])
    current_low = cheapest["price"]
    alert_reasons: list[str] = []

    if current_low <= TARGET_PRICE_TL:
        alert_reasons.append(f"fiyat {TARGET_PRICE_TL:,} TL veya altında".replace(",", "."))

    if isinstance(previous_low, (int, float)) and previous_low > 0:
        drop = (previous_low - current_low) / previous_low * 100
        if drop >= DROP_ALERT_PERCENT:
            alert_reasons.append(f"önceki dip fiyata göre %{drop:.1f} düşüş")

    history = state.get("history", [])
    history.append(
        {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "daily_low": current_low,
            "airline": cheapest["airline"],
            "results": results,
            "errors": errors,
        }
    )
    state["history"] = history[-120:]
    state["lowest_ever"] = current_low if previous_low is None else min(previous_low, current_low)
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)

    # Kullanıcı günlük Telegram raporu istediği için her kontrolde mesaj gönderilir;
    # eşik veya %5 düşüş olduğunda mesaj ayrıca alarm olarak işaretlenir.
    send_telegram(format_report(results, alert_reasons, previous_low))

    for error in errors:
        print(error, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
