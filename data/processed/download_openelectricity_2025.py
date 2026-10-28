import requests
import json
import time
import calendar
from datetime import datetime
from pathlib import Path


# =========================
# SETTINGS
# =========================

API_KEY = "oe_8cnM6xVUCsXpToYRYf6T3Y"

NETWORK = "NEM"
REGION = "NSW1"
YEAR = 2025

URL = f"https://api.openelectricity.org.au/v4/data/network/{NETWORK}"


# =========================
# OUTPUT FOLDER
# =========================

output_folder = Path("data/open_electricity_raw")
output_folder.mkdir(parents=True, exist_ok=True)


# =========================
# HEADERS
# =========================

headers = {
    "Authorization": f"Bearer {API_KEY}"
}


# =========================
# DOWNLOAD MONTH BY MONTH
# =========================

for month in range(1, 13):

    # Start of current month
    start_date = datetime(YEAR, month, 1)

    # Start of next month
    if month == 12:
        end_date = datetime(YEAR + 1, 1, 1)
    else:
        end_date = datetime(YEAR, month + 1, 1)

    month_name = calendar.month_name[month]

    print(
        f"Downloading {month_name} {YEAR}: "
        f"{start_date.strftime('%Y-%m-%d')} "
        f"to {end_date.strftime('%Y-%m-%d')}"
    )

    params = [
        ("metrics", "energy"),
        ("metrics", "emissions"),
        ("interval", "1h"),
        (
            "date_start",
            start_date.strftime("%Y-%m-%dT%H:%M:%S")
        ),
        (
            "date_end",
            end_date.strftime("%Y-%m-%dT%H:%M:%S")
        ),
        ("network_region", REGION),
        ("primary_grouping", "network_region"),
    ]

    response = requests.get(
        URL,
        headers=headers,
        params=params,
        timeout=60
    )

    print("Status code:", response.status_code)

    response.raise_for_status()

    data = response.json()

    filename = (
        output_folder /
        f"{REGION}_{YEAR}_{month:02d}.json"
    )

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Saved: {filename}")
    print()

    # Small pause between requests
    time.sleep(1)


print("Finished downloading all 12 months of 2025.")