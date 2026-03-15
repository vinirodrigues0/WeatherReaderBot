# Polymarket Weather Trading Bot

A Python bot that scans [Polymarket](https://polymarket.com) temperature markets and looks for mispriced outcomes by comparing market prices against NWS (National Weather Service) forecasts.

**Core idea:** Polymarket runs markets like "Will the highest temperature in Chicago be between 34-35°F on March 16?" If the NWS forecast says 34°F but the market is pricing that bucket at 10¢, one of them is wrong. The bot bets on the forecast.

Weather data comes directly from NWS airport stations (the same source Polymarket uses to resolve these markets), so the forecasts are as close to ground truth as possible.

---

## Requirements

- Python 3.10+
- `requests` library

```bash
pip install requests
```

---

## Usage

```bash
# Scan for signals only — no trades recorded (safe to test)
python bot_v1.py

# Scan + record simulated trades against a virtual $1,000 balance
python bot_v1.py --live

# Show open positions and running P&L
python bot_v1.py --status

# Reset simulation back to $1,000
python bot_v1.py --reset
```

Simulation state is saved in `simulation.json` in the same folder.

---

## How it works

1. Pulls hourly forecasts + real observations from NWS airport stations for 6 US cities
2. Finds the corresponding Polymarket event for each city/date via the Gamma API
3. Identifies which temperature bucket the forecast falls into
4. Flags a buy signal if the market prices that bucket below **15¢**
5. In `--live` mode, records the trade and deducts from the virtual balance

---

## Cities covered

| City | Station |
|---|---|
| New York City | KLGA (LaGuardia) |
| Chicago | KORD (O'Hare) |
| Miami | KMIA |
| Dallas | KDAL (Love Field) |
| Seattle | KSEA (Sea-Tac) |
| Atlanta | KATL (Hartsfield) |

---

## Key settings

Located at the top of `bot_v1.py`:

| Variable | Default | Meaning |
|---|---|---|
| `ENTRY_THRESHOLD` | `0.15` | Only buy if bucket is priced below 15¢ |
| `EXIT_THRESHOLD` | `0.45` | Target exit price (not yet automated) |
| `POSITION_PCT` | `0.05` | Spend 5% of balance per trade |
| `MAX_TRADES_PER_RUN` | `5` | Cap on trades per execution |

---

## Notes

- Run it manually once or twice a day, or schedule it with Task Scheduler (Windows) / cron (Mac/Linux)
- The bot does **not** run continuously — it scans, records, and exits
- Spend at least a week in dry-run mode before running `--live`
- This is a paper trading simulation only — no real money is involved
