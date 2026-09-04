import json
from datetime import datetime
from pathlib import Path

from flight_tracker import TARGET_AIRLINES, choose_cheapest_roundtrip_for_airline, load_state

OUTPUT_PATH = Path("automation_state.json")


def main() -> int:
    previous = load_state()
    previous_low = previous.get("lowest_ever")
    results = []
    errors = []

    for airline in TARGET_AIRLINES:
        try:
            result = choose_cheapest_roundtrip_for_airline(airline)
            if result:
                results.append(result)
            else:
                errors.append(f"{airline}: doğrulanabilir sonuç bulunamadı")
        except Exception as exc:
            errors.append(f"{airline}: {exc}")

    current_low = min((item["price"] for item in results), default=None)
    drop_percent = None
    if current_low is not None and isinstance(previous_low, (int, float)) and previous_low > 0:
        drop_percent = (previous_low - current_low) / previous_low * 100

    payload = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "previous_lowest": previous_low,
        "current_lowest": current_low,
        "drop_percent_vs_previous_lowest": drop_percent,
        "threshold_10000_or_below": bool(current_low is not None and current_low <= 10000),
        "threshold_drop_5_percent_or_more": bool(drop_percent is not None and drop_percent >= 5),
        "results": results,
        "errors": errors,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
