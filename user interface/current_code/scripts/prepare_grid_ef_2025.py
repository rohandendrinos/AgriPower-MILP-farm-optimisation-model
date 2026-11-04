from pathlib import Path
import pandas as pd


# ============================================================
# SETTINGS
# ============================================================

INPUT_FILE = Path(
    "data/processed/openelectricity_NSW1_2025_hourly.csv"
)

OUTPUT_FILE = Path(
    "data/processed/grid_ef_NSW1_2025_hourly.csv"
)

YEAR = 2025


# ============================================================
# 1. READ FULL PROCESSED DATA
# ============================================================

print("=" * 60)
print("Preparing model-ready NSW grid emission factor data")
print("=" * 60)

df = pd.read_csv(INPUT_FILE)

print(f"\nInput file: {INPUT_FILE}")
print(f"Input rows: {len(df)}")


# ============================================================
# CHECK REQUIRED COLUMNS
# ============================================================

required_columns = [
    "timestamp",
    "emission_factor_kgCO2e_per_kWh",
]

for column in required_columns:
    if column not in df.columns:
        raise ValueError(
            f"Required column not found: {column}"
        )


# ============================================================
# CONVERT TIMESTAMP
# ============================================================

df["timestamp"] = pd.to_datetime(
    df["timestamp"],
    errors="raise"
)

# OpenElectricity timestamps may contain timezone information.
# Remove timezone for easier matching with the optimisation model.
# The hourly sequence itself is preserved.
if df["timestamp"].dt.tz is not None:
    df["timestamp"] = (
        df["timestamp"]
        .dt.tz_localize(None)
    )


# ============================================================
# CONVERT EMISSION FACTOR TO NUMERIC
# ============================================================

df["emission_factor_kgCO2e_per_kWh"] = pd.to_numeric(
    df["emission_factor_kgCO2e_per_kWh"],
    errors="coerce"
)


# ============================================================
# KEEP ONLY 2025
# ============================================================

df = df[
    df["timestamp"].dt.year == YEAR
].copy()


# ============================================================
# SORT BY TIME
# ============================================================

df = df.sort_values(
    "timestamp"
).reset_index(drop=True)


# ============================================================
# 2. VALIDATION
# ============================================================

print("\n" + "=" * 60)
print("DATA VALIDATION")
print("=" * 60)

# ------------------------------------------------------------
# Row count
# ------------------------------------------------------------

expected_rows = 365 * 24
actual_rows = len(df)

print(f"Expected hourly rows: {expected_rows}")
print(f"Actual hourly rows:   {actual_rows}")


# ------------------------------------------------------------
# Missing values
# ------------------------------------------------------------

missing_timestamp = df["timestamp"].isna().sum()

missing_ef = (
    df["emission_factor_kgCO2e_per_kWh"]
    .isna()
    .sum()
)

print(f"\nMissing timestamps:       {missing_timestamp}")
print(f"Missing emission factors: {missing_ef}")


# ------------------------------------------------------------
# Duplicate timestamps
# ------------------------------------------------------------

duplicate_count = (
    df["timestamp"]
    .duplicated()
    .sum()
)

print(f"Duplicate timestamps:     {duplicate_count}")


# ------------------------------------------------------------
# Check start and end
# ------------------------------------------------------------

actual_start = df["timestamp"].min()
actual_end = df["timestamp"].max()

expected_start = pd.Timestamp(
    "2025-01-01 00:00:00"
)

expected_end = pd.Timestamp(
    "2025-12-31 23:00:00"
)

print(f"\nStart timestamp: {actual_start}")
print(f"End timestamp:   {actual_end}")


# ------------------------------------------------------------
# Check for missing hours
# ------------------------------------------------------------

expected_timestamps = pd.date_range(
    start=expected_start,
    end=expected_end,
    freq="h"
)

actual_timestamps = pd.DatetimeIndex(
    df["timestamp"]
)

missing_hours = expected_timestamps.difference(
    actual_timestamps
)

extra_hours = actual_timestamps.difference(
    expected_timestamps
)

print(f"\nMissing hours: {len(missing_hours)}")
print(f"Extra hours:   {len(extra_hours)}")


# ------------------------------------------------------------
# EF statistics
# ------------------------------------------------------------

ef = df[
    "emission_factor_kgCO2e_per_kWh"
]

print("\nGrid EF statistics:")
print(
    f"Mean: {ef.mean():.4f} kgCO2e/kWh"
)
print(
    f"Min:  {ef.min():.4f} kgCO2e/kWh"
)
print(
    f"Max:  {ef.max():.4f} kgCO2e/kWh"
)


# ============================================================
# FAIL IF IMPORTANT DATA PROBLEMS EXIST
# ============================================================

errors = []

if actual_rows != expected_rows:
    errors.append(
        f"Expected {expected_rows} rows, "
        f"found {actual_rows}."
    )

if missing_timestamp > 0:
    errors.append(
        "Missing timestamps detected."
    )

if missing_ef > 0:
    errors.append(
        "Missing emission factors detected."
    )

if duplicate_count > 0:
    errors.append(
        "Duplicate timestamps detected."
    )

if len(missing_hours) > 0:
    errors.append(
        f"{len(missing_hours)} hourly timestamps are missing."
    )

if len(extra_hours) > 0:
    errors.append(
        f"{len(extra_hours)} unexpected timestamps found."
    )


if errors:
    print("\nVALIDATION FAILED:")

    for error in errors:
        print(f" - {error}")

    raise ValueError(
        "Grid emission factor data failed validation."
    )


# ============================================================
# 3. CREATE MODEL-READY CSV
# ============================================================

model_df = df[
    [
        "timestamp",
        "emission_factor_kgCO2e_per_kWh",
    ]
].copy()

model_df = model_df.rename(
    columns={
        "emission_factor_kgCO2e_per_kWh":
        "grid_ef_kgCO2e_per_kWh"
    }
)


# ============================================================
# SAVE MODEL-READY FILE
# ============================================================

OUTPUT_FILE.parent.mkdir(
    parents=True,
    exist_ok=True
)

model_df.to_csv(
    OUTPUT_FILE,
    index=False,
    float_format="%.6f"
)


# ============================================================
# FINAL SUMMARY
# ============================================================

print("\n" + "=" * 60)
print("VALIDATION PASSED")
print("=" * 60)

print(f"Rows:       {len(model_df)}")
print(f"Start:      {model_df['timestamp'].min()}")
print(f"End:        {model_df['timestamp'].max()}")
print("Resolution: 1 hour")
print("Region:     NSW1")
print("EF unit:    kgCO2e/kWh")

print(
    f"\nModel-ready file saved to:\n{OUTPUT_FILE}"
)

print("\nFirst 5 rows:")
print(model_df.head())