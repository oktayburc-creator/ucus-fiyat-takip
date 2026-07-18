import os
import re
import time
from datetime import datetime
from typing import Any

import requests

SERPAPI_URL = "https://serpapi.com/search.json"
TELEGRAM_API = "https://api.telegram.org"

CITY_AIRPORTS = {
    "istanbul": ["IST", "SAW"],
    "ankara": ["ESB"],
    "izmir": ["ADB"],
    "antalya": ["AYT"],
    "bakü": ["GYD"],
    "baku": ["GYD"],
    "adana": ["COV"],
    "gaziantep": ["GZT"],
    "diyarbakır": ["DIY"],
    "diyarbakir": ["DIY"],
    "trabzon": ["TZX"],
}


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Eksik ortam değişkeni: {name}")
    return value


def normalize_place(value: str) -> list[str]:
    raw = value.strip()
    if re.fullmatch(r"[A-Za-z]{3}", raw):
        return [raw.upper()]
    key = raw.casefold()
    if key in CITY_AIRPORTS:
        return CITY_AIRPORTS[key]
    return [raw]


def parse_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Tarih anlaşılamadı: {value}")


def serpapi_search(params: dict[str, Any]) -> dict[str, Any]:
    merged = {
        **params,
        "engine": "google_flights",
        "api_key": require_env("SERPAPI_KEY"),
        "currency": "TRY",
        "hl": "tr",
        "gl": "tr",
        "travel_class": "1",
        "deep_search": "true",
    }
    response = requests.get(SERPAPI_URL, params=merged, timeout=90)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data


def all_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(data.get("best_flights", [])) + list(data.get("other_flights", []))


def price_value(item: dict[str, Any]) -> int | None:
    try:
        return int(round(float(item.get("price"))))
    except (TypeError, ValueError):
        return None


def itinerary_summary(item: dict[str, Any]) -> str:
    segments = item.get("flights", [])
    if not segments:
        return "Uçuş detayı yok"
    first = segments[0]
    last = segments[-1]
    dep = first.get("departure_airport", {})
    arr = last.get("arrival_airport", {})
    airline_names = []
    for seg in segments:
        airline = str(seg.get("airline", "")).strip()
        if airline and airline not in airline_names:
            airline_names.append(airline)
    route = " → ".join(
        [str(segments[0].get("departure_airport", {}).get("id", "?"))]
        + [str(seg.get("arrival_airport", {}).get("id", "?")) for seg in segments]
    )
    return (
        f"{' / '.join(airline_names) or 'Havayolu bilinmiyor'} | "
        f"{dep.get('time', '?')} | {route} | {arr.get('time', '?')}"
    )


def baggage_summary(item: dict[str, Any]) -> str:
    candidates = list(item.get("extensions", []))
    for segment in item.get("flights", []):
        candidates.extend(segment.get("extensions", []))
    found = []
    for text in candidates:
        lowered = str(text).casefold()
        if any(word in lowered for word in ("bag", "baggage", "carry-on", "checked", "valiz", "bagaj")):
            clean = str(text).strip()
            if clean and clean not in found:
                found.append(clean)
    return "; ".join(found[:2]) if found else "Kaynakta belirtilmedi"


def search_one_way(origin: str, destination: str, outbound_date: str, limit: int = 8) -> list[dict[str, Any]]:
    results = []
    for origin_id in normalize_place(origin):
        for destination_id in normalize_place(destination):
            data = serpapi_search(
                {
                    "departure_id": origin_id,
                    "arrival_id": destination_id,
                    "outbound_date": outbound_date,
                    "type": "2",
                    "show_hidden": "true",
                }
            )
            for item in all_results(data):
                price = price_value(item)
                if price is not None:
                    results.append({"price": price, "flight": item})
    results.sort(key=lambda x: x["price"])
    return results[:limit]


def search_roundtrip(origin: str, destination: str, outbound_date: str, return_date: str, limit: int = 8) -> list[dict[str, Any]]:
    results = []
    for origin_id in normalize_place(origin):
        for destination_id in normalize_place(destination):
            outbound_data = serpapi_search(
                {
                    "departure_id": origin_id,
                    "arrival_id": destination_id,
                    "outbound_date": outbound_date,
                    "return_date": return_date,
                    "type": "1",
                    "show_hidden": "true",
                }
            )
            outbound_options = [x for x in all_results(outbound_data) if x.get("departure_token")]
            outbound_options.sort(key=lambda x: price_value(x) or 10**9)
            for outbound in outbound_options[:5]:
                return_data = serpapi_search(
                    {
                        "departure_id": origin_id,
                        "arrival_id": destination_id,
                        "outbound_date": outbound_date,
                        "return_date": return_date,
                        "type": "1",
                        "departure_token": outbound["departure_token"],
                    }
                )
                for returning in all_results(return_data):
                    price = price_value(returning)
                    if price is not None:
                        results.append({"price": price, "outbound": outbound, "return": returning})
    results.sort(key=lambda x: x["price"])
    return results[:limit]


def format_one_way(origin: str, destination: str, date_str: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return "Uygun uçuş bulunamadı."
    lines = [f"✈️ {origin} → {destination}", f"📅 {date_str}", ""]
    for idx, result in enumerate(results, 1):
        flight = result["flight"]
        lines.extend([
            f"{idx}. {result['price']:,} TL".replace(",", "."),
            itinerary_summary(flight),
            f"Bagaj: {baggage_summary(flight)}",
            "",
        ])
    lines.append("Kaynak: Google Flights sonuçları (SerpApi). Fiyatı rezervasyon öncesinde yeniden doğrula.")
    return "\n".join(lines)[:4096]


def format_roundtrip(origin: str, destination: str, outbound_date: str, return_date: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return "Uygun gidiş-dönüş uçuş bulunamadı."
    lines = [f"✈️ {origin} ⇄ {destination}", f"📅 {outbound_date} / {return_date}", ""]
    for idx, result in enumerate(results, 1):
        lines.extend([
            f"{idx}. Toplam: {result['price']:,} TL".replace(",", "."),
            f"Gidiş: {itinerary_summary(result['outbound'])}",
            f"Dönüş: {itinerary_summary(result['return'])}",
            f"Bagaj: {baggage_summary(result['return']) if baggage_summary(result['return']) != 'Kaynakta belirtilmedi' else baggage_summary(result['outbound'])}",
            "",
        ])
    lines.append("Kaynak: Google Flights sonuçları (SerpApi). Fiyatı rezervasyon öncesinde yeniden doğrula.")
    return "\n".join(lines)[:4096]


def send_message(chat_id: str | int, text: str) -> None:
    token = require_env("TELEGRAM_BOT_TOKEN")
    response = requests.post(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    response.raise_for_status()


def help_text() -> str:
    return (
        "Komutlar:\n"
        "/ara İstanbul Ankara 11.11.2026 13.11.2026\n"
        "/tekyon İstanbul İzmir 15.11.2026\n\n"
        "Şehir adı yerine IST, SAW, ESB, ADB gibi IATA kodu da kullanabilirsin."
    )


def handle_message(message: dict[str, Any]) -> None:
    chat_id = message.get("chat", {}).get("id")
    text = str(message.get("text", "")).strip()
    if not chat_id or not text:
        return

    allowed_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if allowed_chat and str(chat_id) != allowed_chat:
        send_message(chat_id, "Bu bot özel kullanım için yapılandırılmıştır.")
        return

    parts = text.split()
    command = parts[0].split("@")[0].casefold()

    try:
        if command in ("/start", "/yardim", "/help"):
            send_message(chat_id, help_text())
            return

        if command == "/ara":
            if len(parts) != 5:
                send_message(chat_id, "Kullanım: /ara İstanbul Ankara 11.11.2026 13.11.2026")
                return
            origin, destination = parts[1], parts[2]
            outbound_date, return_date = parse_date(parts[3]), parse_date(parts[4])
            send_message(chat_id, "🔎 En ucuz gidiş-dönüş seçenekleri aranıyor...")
            results = search_roundtrip(origin, destination, outbound_date, return_date)
            send_message(chat_id, format_roundtrip(origin, destination, outbound_date, return_date, results))
            return

        if command == "/tekyon":
            if len(parts) != 4:
                send_message(chat_id, "Kullanım: /tekyon İstanbul İzmir 15.11.2026")
                return
            origin, destination = parts[1], parts[2]
            outbound_date = parse_date(parts[3])
            send_message(chat_id, "🔎 En ucuz tek yön seçenekleri aranıyor...")
            results = search_one_way(origin, destination, outbound_date)
            send_message(chat_id, format_one_way(origin, destination, outbound_date, results))
            return

        send_message(chat_id, help_text())
    except Exception as exc:
        send_message(chat_id, f"⚠️ Arama sırasında hata oluştu: {exc}")


def main() -> None:
    token = require_env("TELEGRAM_BOT_TOKEN")
    offset = 0
    print("Telegram anlık uçuş arama botu çalışıyor.")
    while True:
        try:
            response = requests.get(
                f"{TELEGRAM_API}/bot{token}/getUpdates",
                params={"timeout": 50, "offset": offset, "allowed_updates": '["message"]'},
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            for update in payload.get("result", []):
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                if "message" in update:
                    handle_message(update["message"])
        except Exception as exc:
            print(f"Polling hatası: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
