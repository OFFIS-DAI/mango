"""Write data/profiles.csv: synthetic, deterministic 15-minute profiles for one week.

- ``h0``: household load in kW per 1000 kWh of annual consumption (a standard-load-profile
  shape: low at night, a morning and a larger evening peak, a bit more at weekends);
- ``pv``: PV feed-in in kW per kWp (a summer day from about 5:00 to 21:30, peak 0.75,
  a different cloudiness on every day).

The file starts on 2024-06-01 00:00 (a Saturday) and has no random numbers, so running
this script again gives the same bytes.  Run it from the example folder:
``python data/make_profiles.py``.
"""

import csv
import math
from datetime import datetime, timedelta
from pathlib import Path

START = datetime(2024, 6, 1)
DAYS = 7
STEP = timedelta(minutes=15)
MEAN_KW_PER_1000_KWH = 1000.0 / 8760.0
CLEAR_SKY = [0.95, 0.80, 1.00, 0.60, 0.90, 0.85, 0.70]  # per day, 1.0 = clear sky


def bump(hour: float, centre: float, width: float) -> float:
    return math.exp(-0.5 * ((hour - centre) / width) ** 2)


def load_shape(hour: float) -> float:
    return (
        0.45
        + 0.55 * bump(hour, 7.5, 1.2)
        + 0.60 * bump(hour, 12.5, 1.5)
        + 1.20 * bump(hour, 19.0, 1.8)
    )


def pv_shape(hour: float) -> float:
    if not 5.0 < hour < 21.5:
        return 0.0
    return math.sin(math.pi * (hour - 5.0) / 16.5) ** 1.5


def main(path: Path = Path(__file__).with_name("profiles.csv")) -> Path:
    samples_per_day = int(timedelta(days=1) / STEP)
    day_mean = (
        sum(load_shape(i * 0.25) for i in range(samples_per_day)) / samples_per_day
    )
    rows = []
    for i in range(DAYS * samples_per_day):
        when = START + i * STEP
        day, hour = divmod(i, samples_per_day)
        hour = hour * STEP.total_seconds() / 3600.0
        weekend = 1.08 if when.weekday() >= 5 else 1.0
        h0 = MEAN_KW_PER_1000_KWH * weekend * load_shape(hour) / day_mean
        clouds = 1.0 - 0.15 * (1.0 - CLEAR_SKY[day]) * (1.0 + math.sin(3.0 * hour))
        pv = 0.75 * CLEAR_SKY[day] * clouds * pv_shape(hour)
        rows.append((when.isoformat(timespec="minutes"), f"{h0:.5f}", f"{pv:.5f}"))
    with path.open("w", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["time", "h0", "pv"])
        writer.writerows(rows)
    return path


if __name__ == "__main__":
    print(f"wrote {main()}")
