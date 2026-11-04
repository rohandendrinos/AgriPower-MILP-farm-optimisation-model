import json
from pathlib import Path

import pandas as pd


# ============================================================
# SETTINGS
# ============================================================

RAW_FOLDER = Path("data/open_electricity_raw")

OUTPUT_FILE = Path(
    "data/processed/openelectricity_NSW1_2025_hourly.csv"
)

YEAR = 2025


# ============================================================
# FIND ALL MONTHLY JSON FILES
# ============================================================

json_files = sorted(
    RAW_FOLDER.glob("NSW1_2025_*.json")
)

print(f"Found {len(json_files)} JSON files.")

if len(json_files) == 0:
    raise FileNotFoundError(
        f"No JSON files found in {RAW_FOLDER}"
    )


# ============================================================
# READ DATA
# ============================================================

records = []

for file in json_files:

    print(f"Processing: {file.name}")

    with open(file, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # Check API response
    if not raw_data.get("success", False):
        print(f"Warning: API response not successful in {file.name}")
        continue

    # raw_data["data"] contains different metrics
    # e.g. energy and emissions
    for timeseries in raw_data["data"]:

        metric = timeseries.get("metric")
        unit = timeseries.get("unit")

        if metric not in ["energy", "emissions"]:
            continue

        # Each metric may contain one or more results
        for result in timeseries.get("results", []):

            series_name = result.get("name", "unknown")

            for point in result.get("data", []):

                # OpenElectricity normally returns:
                # [
                #     "2025-01-01T00:00:00+10:00",
                #     value
                # ]

                if isinstance(point, list):

                    if len(point) < 2:
                        continue

                    timestamp = point[0]
                    value = point[1]

                # Also support dictionary format just in case
                elif isinstance(point, dict):

                    timestamp = point.get("timestamp")
                    value = point.get("value")

                else:
                    continue

                if timestamp is None or value is None:
                    continue

                records.append(
                    {
                        "timestamp": timestamp,
                        "metric": metric,
                        "value": value,
                        "unit": unit,
                        "series": series_name,
                    }
                )


# ============================================================
# CREATE LONG-FORM DATAFRAME
# ============================================================

df_long = pd.DataFrame(records)

if df_long.empty:
    raise ValueError(
        "No data points were extracted from the JSON files."
    )

print()
print(f"Total raw records extracted: {len(df_long)}")

print()
print("Metrics found:")
print(df_long["metric"].value_counts())


# ============================================================
# CONVERT TIMESTAMP
# ============================================================

df_long["timestamp"] = pd.to_datetime(
    df_long["timestamp"]
)


# ============================================================
# CONVERT VALUES TO NUMERIC
# ============================================================

df_long["value"] = pd.to_numeric(
    df_long["value"],
    errors="coerce"
)


# ============================================================
# PIVOT ENERGY AND EMISSIONS INTO COLUMNS
# ============================================================

df = (
    df_long
    .pivot_table(
        index="timestamp",
        columns="metric",
        values="value",
        aggfunc="first"
    )
    .reset_index()
)

# Remove pandas column name created by pivot
df.columns.name = None


# ============================================================
# RENAME COLUMNS
# ============================================================

df = df.rename(
    columns={
        "energy": "energy_MWh",
        "emissions": "emissions_tCO2e",
    }
)


# ============================================================
# CHECK REQUIRED COLUMNS
# ============================================================

required_columns = [
    "energy_MWh",
    "emissions_tCO2e",
]

for column in required_columns:
    if column not in df.columns:
        raise ValueError(
            f"Required column '{column}' was not found."
        )


# ============================================================
# KEEP ONLY 2025 DATA
# ============================================================

df = df[
    df["timestamp"].dt.year == YEAR
].copy()


# ============================================================
# SORT
# ============================================================

df = df.sort_values(
    "timestamp"
).reset_index(drop=True)


# ============================================================
# REMOVE DUPLICATE TIMESTAMPS
# ============================================================

duplicate_count = df["timestamp"].duplicated().sum()

if duplicate_count > 0:
    print(
        f"Warning: {duplicate_count} duplicate timestamps found."
    )

    df = df.drop_duplicates(
        subset="timestamp",
        keep="first"
    ).reset_index(drop=True)


# ============================================================
# CALCULATE HOURLY EMISSION FACTOR
# ============================================================

# emissions = tonnes CO2-e
# energy    = MWh
#
# t CO2-e / MWh has the same numerical value as
# kg CO2-e / kWh

df["emission_factor_kgCO2e_per_kWh"] = (
    df["emissions_tCO2e"]
    / df["energy_MWh"]
)


# Avoid division-by-zero issues
df.loc[
    df["energy_MWh"] <= 0,
    "emission_factor_kgCO2e_per_kWh"
] = pd.NA


# ============================================================
# CHECK FOR MISSING VALUES
# ============================================================

missing_energy = df["energy_MWh"].isna().sum()
missing_emissions = df["emissions_tCO2e"].isna().sum()
missing_factor = (
    df["emission_factor_kgCO2e_per_kWh"]
    .isna()
    .sum()
)


# ============================================================
# SAVE CSV
# ============================================================

OUTPUT_FILE.parent.mkdir(
    parents=True,
    exist_ok=True
)

df.to_csv(
    OUTPUT_FILE,
    index=False
)


# ============================================================
# SUMMARY
# ============================================================

print()
print("=" * 60)
print("PROCESSING FINISHED")
print("=" * 60)

print(f"Hourly rows: {len(df)}")

print(
    f"Start: {df['timestamp'].min()}"
)

print(
    f"End:   {df['timestamp'].max()}"
)

print()
print("Missing values:")
print(f"  Energy:          {missing_energy}")
print(f"  Emissions:       {missing_emissions}")
print(f"  Emission factor: {missing_factor}")

print()
print(
    "Average emission factor:"
)

print(
    f"{df['emission_factor_kgCO2e_per_kWh'].mean():.4f} "
    "kg CO2-e/kWh"
)

print()
print(f"Saved to: {OUTPUT_FILE}")

print()
print("First 5 rows:")
print(df.head())

print()

if len(df) == 8760:
    print("✓ 8760 hourly records found for 2025.")
else:
    print(
        f"WARNING: Expected 8760 hours, "
        f"but found {len(df)}."
    )