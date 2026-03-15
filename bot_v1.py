"""
Polymarket Weather Trading Bot — v1 (Paper Trading)
====================================================
Usage:
    python bot_v1.py            # Scan for signals only, no trades recorded
    python bot_v1.py --live     # Scan + record simulated trades
    python bot_v1.py --status   # Show open positions and current P&L
    python bot_v1.py --reset    # Wipe simulation state, start fresh
"""

import re
import json
import argparse
import requests
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# PART 1 — Configuration
# ---------------------------------------------------------------------------

LOCATIONS = {
    "nyc":     {"lat": 40.7772, "lon": -73.8726, "name": "New York City"},  # KLGA LaGuardia
    "chicago": {"lat": 41.9742, "lon": -87.9073, "name": "Chicago"},        # KORD O'Hare
    "miami":   {"lat": 25.7959, "lon": -80.2870, "name": "Miami"},          # KMIA
    "dallas":  {"lat": 32.8471, "lon": -96.8518, "name": "Dallas"},         # KDAL Love Field
    "seattle": {"lat": 47.4502, "lon": -122.3088, "name": "Seattle"},       # KSEA Sea-Tac
    "atlanta": {"lat": 33.6407, "lon": -84.4277,  "name": "Atlanta"},       # KATL Hartsfield
}

MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]

ENTRY_THRESHOLD    = 0.15   # Buy only if market price is below 15¢
EXIT_THRESHOLD     = 0.45   # Sell when price reaches 45¢ (not automated yet)
POSITION_PCT       = 0.05   # Flat 5% of balance per trade
MAX_TRADES_PER_RUN = 5      # Safety cap on trades per execution


# ---------------------------------------------------------------------------
# PART 2 — Weather forecast (NWS)
# ---------------------------------------------------------------------------

NWS_ENDPOINTS = {
    "nyc":     "https://api.weather.gov/gridpoints/OKX/37,39/forecast/hourly",
    "chicago": "https://api.weather.gov/gridpoints/LOT/66,77/forecast/hourly",
    "miami":   "https://api.weather.gov/gridpoints/MFL/106,51/forecast/hourly",
    "dallas":  "https://api.weather.gov/gridpoints/FWD/87,107/forecast/hourly",
    "seattle": "https://api.weather.gov/gridpoints/SEW/124,61/forecast/hourly",
    "atlanta": "https://api.weather.gov/gridpoints/FFC/50,82/forecast/hourly",
}

STATION_IDS = {
    "nyc": "KLGA", "chicago": "KORD", "miami": "KMIA",
    "dallas": "KDAL", "seattle": "KSEA", "atlanta": "KATL",
}


def get_forecast(city_slug: str) -> dict:
    """
    Returns { "YYYY-MM-DD": max_temp_fahrenheit } for the next few days.
    Combines real observations (past hours) + hourly forecast (future hours)
    so today's morning high is never lost.
    """
    forecast_url = NWS_ENDPOINTS.get(city_slug)
    station_id   = STATION_IDS.get(city_slug)
    daily_max    = {}
    headers      = {"User-Agent": "weatherbot/1.0"}

    # Step 1: real observations for hours already past today
    try:
        obs_url = f"https://api.weather.gov/stations/{station_id}/observations?limit=48"
        r = requests.get(obs_url, timeout=10, headers=headers)
        r.raise_for_status()
        for obs in r.json().get("features", []):
            props    = obs["properties"]
            date_str = props.get("timestamp", "")[:10]
            temp_c   = props.get("temperature", {}).get("value")
            if temp_c is not None:
                temp_f = round(temp_c * 9 / 5 + 32)
                if date_str not in daily_max or temp_f > daily_max[date_str]:
                    daily_max[date_str] = temp_f
    except Exception as e:
        print(f"  [WARN] Observations error for {city_slug}: {e}")

    # Step 2: hourly forecast for upcoming hours
    try:
        r = requests.get(forecast_url, timeout=10, headers=headers)
        r.raise_for_status()
        periods = r.json()["properties"]["periods"]
        for p in periods:
            date_str = p["startTime"][:10]
            temp     = p["temperature"]
            if p.get("temperatureUnit") == "C":
                temp = round(temp * 9 / 5 + 32)
            if date_str not in daily_max or temp > daily_max[date_str]:
                daily_max[date_str] = temp
    except Exception as e:
        print(f"  [WARN] Forecast error for {city_slug}: {e}")

    return daily_max


# ---------------------------------------------------------------------------
# PART 3 — Polymarket event lookup
# ---------------------------------------------------------------------------

def get_polymarket_event(city_slug: str, month: str, day: int, year: int):
    """Fetches the Polymarket event for a city/date. Returns event dict or None."""
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    url  = f"https://gamma-api.polymarket.com/events?slug={slug}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    except Exception as e:
        print(f"  [WARN] Polymarket error ({city_slug} {month} {day}): {e}")
    return None


# ---------------------------------------------------------------------------
# PART 4 — Temperature bucket parser
# ---------------------------------------------------------------------------

def parse_temp_range(question: str):
    """
    Converts a Polymarket question string into a (low, high) numeric tuple.
      "between 44-45°F"   → (44, 45)
      "48°F or higher"    → (48, 999)
      "40°F or below"     → (-999, 40)
    Returns None if format is unrecognised.
    """
    q = question.lower()
    if "or below" in q:
        m = re.search(r'(\d+)°f or below', question, re.IGNORECASE)
        if m:
            return (-999, int(m.group(1)))
    if "or higher" in q:
        m = re.search(r'(\d+)°f or higher', question, re.IGNORECASE)
        if m:
            return (int(m.group(1)), 999)
    m = re.search(r'between (\d+)-(\d+)°f', question, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


# ---------------------------------------------------------------------------
# PART 5 — Simulation state
# ---------------------------------------------------------------------------

SIM_FILE    = "simulation.json"
SIM_BALANCE = 1000.0


def load_sim() -> dict:
    try:
        with open(SIM_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "balance":          SIM_BALANCE,
            "starting_balance": SIM_BALANCE,
            "positions":        {},
            "trades":           [],
            "total_trades":     0,
            "wins":             0,
            "losses":           0,
        }


def save_sim(sim: dict):
    with open(SIM_FILE, "w") as f:
        json.dump(sim, f, indent=2)


def reset_sim():
    save_sim({
        "balance":          SIM_BALANCE,
        "starting_balance": SIM_BALANCE,
        "positions":        {},
        "trades":           [],
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
    })
    print(f"Simulation reset. Starting balance: ${SIM_BALANCE:.2f}")


def print_status():
    sim       = load_sim()
    positions = sim.get("positions", {})
    balance   = sim.get("balance", SIM_BALANCE)
    starting  = sim.get("starting_balance", SIM_BALANCE)

    print("\n" + "=" * 60)
    print("  SIMULATION STATUS")
    print("=" * 60)
    print(f"  Balance:        ${balance:.2f}")
    print(f"  Starting:       ${starting:.2f}")
    print(f"  P&L:            ${balance - starting:+.2f}")
    print(f"  Total trades:   {sim.get('total_trades', 0)}")
    print(f"  Wins / Losses:  {sim.get('wins', 0)} / {sim.get('losses', 0)}")
    print(f"  Open positions: {len(positions)}")

    if positions:
        print("\n  Open Positions:")
        for market_id, pos in positions.items():
            print(f"\n    [{pos['date']}] {pos['location'].upper()} — {pos['question'][:55]}")
            print(f"      Entry: ${pos['entry_price']:.3f}  |  Shares: {pos['shares']:.1f}  |  Cost: ${pos['cost']:.2f}")
            print(f"      Forecast at entry: {pos['forecast_temp']}°F  |  Opened: {pos['opened_at'][:16]}")
    else:
        print("\n  No open positions.")
    print()


# ---------------------------------------------------------------------------
# PART 6 — Main strategy loop
# ---------------------------------------------------------------------------

def run(dry_run: bool = True):
    sim             = load_sim()
    balance         = sim["balance"]
    positions       = sim["positions"]
    trades_executed = 0

    mode = "DRY RUN (signals only)" if dry_run else "LIVE SIMULATION"
    print(f"\n{'=' * 60}")
    print(f"  Polymarket Weather Bot — {mode}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Balance: ${balance:.2f}")
    print(f"{'=' * 60}\n")

    for city_slug, city_info in LOCATIONS.items():
        print(f"🔍 Scanning {city_info['name']}...")
        forecast = get_forecast(city_slug)

        if not forecast:
            print("  No forecast data. Skipping.\n")
            continue

        for i in range(0, 4):  # today + next 3 days
            date      = datetime.now(timezone.utc) + timedelta(days=i)
            date_str  = date.strftime("%Y-%m-%d")
            month     = MONTHS[date.month - 1]

            forecast_temp = forecast.get(date_str)
            if forecast_temp is None:
                continue

            event = get_polymarket_event(city_slug, month, date.day, date.year)
            if not event:
                continue

            print(f"  📍 {date_str} | Forecast max: {forecast_temp}°F")

            for market in event.get("markets", []):
                question = market.get("question", "")
                rng      = parse_temp_range(question)
                if not rng:
                    continue

                if not (rng[0] <= forecast_temp <= rng[1]):
                    continue

                try:
                    prices    = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
                    yes_price = float(prices[0])
                except Exception:
                    continue

                # Skip dead markets (priced at zero — market considers outcome impossible)
                if yes_price <= 0:
                    continue

                print(f"     Bucket : {question[:65]}")
                print(f"     Price  : ${yes_price:.3f}", end="")

                if yes_price < ENTRY_THRESHOLD:
                    market_id     = market.get("id", "")
                    position_size = round(balance * POSITION_PCT, 2)
                    shares        = position_size / yes_price

                    print(f"  ✅ SIGNAL")
                    print(f"     → {shares:.1f} shares @ ${yes_price:.3f} = ${position_size:.2f}")

                    if (
                        not dry_run
                        and market_id not in positions
                        and trades_executed < MAX_TRADES_PER_RUN
                    ):
                        balance -= position_size
                        positions[market_id] = {
                            "question":      question,
                            "entry_price":   yes_price,
                            "shares":        shares,
                            "cost":          position_size,
                            "date":          date_str,
                            "location":      city_slug,
                            "forecast_temp": forecast_temp,
                            "opened_at":     datetime.now().isoformat(),
                        }
                        sim["total_trades"] += 1
                        trades_executed     += 1
                        print(f"     → Trade recorded. New balance: ${balance:.2f}")
                else:
                    print()  # close the price line

                break  # one matching bucket per city/date is enough

        print()

    sim["balance"]   = round(balance, 2)
    sim["positions"] = positions
    save_sim(sim)

    print(f"{'=' * 60}")
    print(f"  Done. Balance: ${balance:.2f} | Trades this run: {trades_executed}")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket Weather Trading Bot")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--live",   action="store_true", help="Record simulated trades")
    group.add_argument("--status", action="store_true", help="Show open positions and P&L")
    group.add_argument("--reset",  action="store_true", help="Reset simulation to $1000")
    args = parser.parse_args()

    if args.status:
        print_status()
    elif args.reset:
        reset_sim()
    else:
        run(dry_run=not args.live)