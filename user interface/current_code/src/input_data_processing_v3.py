"""
input_data_processing_v3.py

AgriPower Input Data Processing Module - V3

Purpose:
    Convert farmer-uploaded NEM12 / 5-minute meter data into clean model-ready inputs.

Key update in V3:
    - Supports NEM12-style data saved as .xlsx as well as .csv
    - Parses 200/300 records from Excel sheets
    - Handles multiple registers such as B1, E1, K1, Q1
    - Uses KWH registers for active energy demand and ignores KVARH by default
    - Treats zero values as valid data, not missing data
    - Adds zero-demand and register-level quality summaries
    - Adds long zero-period detection for possible meter gaps/outages

Outputs:
    1. Original-resolution clean demand table, usually 5-minute
    2. Hourly or 30-minute aggregated table for optimisation
    3. Validation summary for missing timestamps, duplicate timestamps,
       negative demand, missing values, zero intervals, and peak demand
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional, Literal, Dict, Tuple, List

import pandas as pd


CleanInputMode = Literal["power_kW", "energy_kWh"]
IntervalLabel = Literal["start", "end"]


# ---------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------

def _read_first_nonempty_line(file_path: str | Path) -> str:
    path = Path(file_path)
    with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            if line.strip():
                return line.strip()
    return ""


def _looks_like_nem_record(value: object) -> bool:
    try:
        return int(float(value)) in {100, 200, 300, 400, 500, 900}
    except (ValueError, TypeError):
        return False


def detect_input_type(file_path: str | Path) -> str:
    """
    Detect whether the input looks like NEM12 or a normal CSV/Excel time series.

    Returns:
        "nem12_csv", "nem12_excel", or "tabular"
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in [".xlsx", ".xls"]:
        preview = pd.read_excel(path, header=None, nrows=10)
        first_col = preview.iloc[:, 0].dropna().tolist()
        if any(_looks_like_nem_record(v) for v in first_col):
            return "nem12_excel"
        return "tabular"

    first_line = _read_first_nonempty_line(path)
    first_token = first_line.split(",")[0].strip().strip('"')
    if _looks_like_nem_record(first_token):
        return "nem12_csv"

    return "tabular"


def _convert_energy_to_kwh(values: pd.Series, unit: str) -> pd.Series:
    """Convert interval energy values to kWh."""
    unit_clean = str(unit).strip().upper()

    if unit_clean == "KWH":
        return values.astype(float)
    if unit_clean == "MWH":
        return values.astype(float) * 1000.0
    if unit_clean == "WH":
        return values.astype(float) / 1000.0

    # KVARH is reactive energy, not active electricity consumption.
    # Keep the numeric value in register summaries, but it should not be used
    # as demand_kWh for optimisation.
    return values.astype(float)


# ---------------------------------------------------------------------
# NEM12 parsers
# ---------------------------------------------------------------------

def _parse_nem12_rows(
    rows_iter,
    interval_label: IntervalLabel = "start",
) -> pd.DataFrame:
    """
    Shared parser for NEM12-style rows.

    Each row should be list-like:
        200,NMI,NMIConfiguration,RegisterID,NMISuffix,...,UOM,IntervalLength,...
        300,YYYYMMDD,v1,v2,...,v288,Quality,...
    """
    rows = []

    current_meta = {
        "nmi": None,
        "register_id": None,
        "nmi_suffix": None,
        "source_uom": "KWH",
        "interval_minutes": 5,
    }

    for row in rows_iter:
        if row is None or len(row) == 0:
            continue

        record_raw = row[0]
        if pd.isna(record_raw):
            continue

        try:
            record_type = int(float(record_raw))
        except (ValueError, TypeError):
            continue

        if record_type == 200:
            current_meta["nmi"] = str(row[1]).replace(".0", "") if len(row) > 1 and not pd.isna(row[1]) else None
            current_meta["register_id"] = str(row[3]).strip() if len(row) > 3 and not pd.isna(row[3]) else None
            current_meta["nmi_suffix"] = str(row[4]).strip() if len(row) > 4 and not pd.isna(row[4]) else None
            current_meta["source_uom"] = str(row[7]).strip().upper() if len(row) > 7 and not pd.isna(row[7]) else "KWH"

            try:
                current_meta["interval_minutes"] = int(float(row[8])) if len(row) > 8 and not pd.isna(row[8]) else 5
            except (ValueError, TypeError):
                current_meta["interval_minutes"] = 5

        elif record_type == 300:
            if len(row) < 3:
                continue

            if current_meta["register_id"] is None:
                # Cannot safely interpret a 300 row before a 200 metadata row.
                continue

            interval_minutes = int(current_meta["interval_minutes"])
            intervals_per_day = int(24 * 60 / interval_minutes)

            interval_date_raw = row[1]
            try:
                # Common NEM12 date format is YYYYMMDD.
                date = pd.to_datetime(str(int(float(interval_date_raw))), format="%Y%m%d")
            except Exception:
                date = pd.to_datetime(interval_date_raw, errors="coerce")

            if pd.isna(date):
                continue

            value_fields = row[2 : 2 + intervals_per_day]

            for i, value in enumerate(value_fields):
                energy_value = pd.to_numeric(value, errors="coerce")

                if interval_label == "start":
                    timestamp = date + pd.Timedelta(minutes=i * interval_minutes)
                else:
                    timestamp = date + pd.Timedelta(minutes=(i + 1) * interval_minutes)

                rows.append(
                    {
                        "timestamp": timestamp,
                        "nmi": current_meta["nmi"],
                        "register_id": current_meta["register_id"],
                        "nmi_suffix": current_meta["nmi_suffix"],
                        "source_uom": current_meta["source_uom"],
                        "interval_minutes": interval_minutes,
                        "interval_energy_value": energy_value,
                    }
                )

    df = pd.DataFrame(rows)

    if df.empty:
        raise ValueError("No NEM12 interval data found. Check the file format.")

    df["interval_energy_kWh_equivalent"] = _convert_energy_to_kwh(
        df["interval_energy_value"],
        df["source_uom"].iloc[0] if len(df["source_uom"].unique()) == 1 else "KWH",
    )

    df = df.sort_values(["register_id", "timestamp"]).reset_index(drop=True)
    return df


def parse_nem12_csv(
    file_path: str | Path,
    interval_label: IntervalLabel = "start",
) -> pd.DataFrame:
    """Parse a NEM12 CSV file into a long register-level table."""
    path = Path(file_path)
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        return _parse_nem12_rows(reader, interval_label=interval_label)


def parse_nem12_excel(
    file_path: str | Path,
    sheet_name: str | int | None = 0,
    interval_label: IntervalLabel = "start",
) -> pd.DataFrame:
    """Parse a NEM12-style Excel sheet into a long register-level table."""
    path = Path(file_path)
    df = pd.read_excel(path, sheet_name=sheet_name, header=None)
    return _parse_nem12_rows(df.itertuples(index=False, name=None), interval_label=interval_label)


def summarise_nem12_registers(df_register_level: pd.DataFrame) -> pd.DataFrame:
    """
    Summarise all registers in the NEM12 file.

    This is useful because many NEM12 files contain:
        - active import/export registers in KWH
        - reactive registers in KVARH
        - all-zero registers
    """
    data = df_register_level.copy()
    data["is_zero"] = data["interval_energy_value"].fillna(0).eq(0)

    summary = (
        data.groupby(["register_id", "source_uom"], dropna=False)
        .agg(
            start=("timestamp", "min"),
            end=("timestamp", "max"),
            records=("timestamp", "count"),
            total_value=("interval_energy_value", "sum"),
            zero_count=("is_zero", "sum"),
            missing_count=("interval_energy_value", lambda x: int(x.isna().sum())),
            max_interval=("interval_energy_value", "max"),
        )
        .reset_index()
    )

    summary["zero_pct"] = summary["zero_count"] / summary["records"]
    return summary


def build_active_energy_demand(
    df_register_level: pd.DataFrame,
    active_energy_registers: Optional[List[str]] = None,
    aggregate_kwh_registers: bool = True,
) -> pd.DataFrame:
    """
    Convert register-level NEM12 data into active demand data for the optimisation model.

    Default behaviour:
        - Use KWH registers only
        - Ignore KVARH reactive energy registers
        - Aggregate all selected KWH registers by timestamp

    For this piggery data:
        B1 is KWH but all zero
        E1 is KWH and contains the active energy values
        K1 and Q1 are KVARH and should not be used as demand_kWh
    """
    data = df_register_level.copy()
    data["source_uom"] = data["source_uom"].astype(str).str.upper()

    active = data[data["source_uom"] == "KWH"].copy()

    if active_energy_registers is not None:
        active_energy_registers = [str(r) for r in active_energy_registers]
        active = active[active["register_id"].astype(str).isin(active_energy_registers)].copy()

    if active.empty:
        raise ValueError(
            "No active KWH registers found. Check register IDs or NEM12 file structure."
        )

    if aggregate_kwh_registers:
        out = (
            active.groupby("timestamp", as_index=False)
            .agg(
                demand_kWh=("interval_energy_value", "sum"),
                interval_minutes=("interval_minutes", "first"),
            )
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        out["demand_kW"] = out["demand_kWh"] / (out["interval_minutes"] / 60.0)
        out["selected_registers"] = ",".join(sorted(active["register_id"].dropna().astype(str).unique()))
        return out

    active = active.rename(columns={"interval_energy_value": "demand_kWh"})
    active["demand_kW"] = active["demand_kWh"] / (active["interval_minutes"] / 60.0)
    return active


# ---------------------------------------------------------------------
# Tabular parser
# ---------------------------------------------------------------------

def parse_tabular_timeseries(
    file_path: str | Path,
    timestamp_col: str = "timestamp",
    value_col: str = "demand_kW",
    input_mode: CleanInputMode = "power_kW",
    interval_minutes: Optional[int] = None,
) -> pd.DataFrame:
    """
    Parse a normal CSV or Excel table with timestamp and demand/power/energy column.

    input_mode:
        "power_kW": value_col is average power during interval, converted to kWh.
        "energy_kWh": value_col is already interval energy in kWh.
    """
    path = Path(file_path)

    if path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    if timestamp_col not in df.columns:
        raise ValueError(f"timestamp_col '{timestamp_col}' not found. Available columns: {list(df.columns)}")
    if value_col not in df.columns:
        raise ValueError(f"value_col '{value_col}' not found. Available columns: {list(df.columns)}")

    out = pd.DataFrame()
    out["timestamp"] = pd.to_datetime(df[timestamp_col], errors="coerce")
    out = out.dropna(subset=["timestamp"])
    out["timestamp"] = out["timestamp"].dt.tz_localize(None)

    values = pd.to_numeric(df.loc[out.index, value_col], errors="coerce")
    out["raw_value"] = values
    out = out.sort_values("timestamp").reset_index(drop=True)

    if interval_minutes is None:
        diffs = out["timestamp"].diff().dropna()
        interval_minutes = int(diffs.mode().iloc[0].total_seconds() / 60) if not diffs.empty else 5

    out["interval_minutes"] = interval_minutes

    if input_mode == "power_kW":
        out["demand_kW"] = out["raw_value"]
        out["demand_kWh"] = out["demand_kW"] * (interval_minutes / 60.0)
    else:
        out["demand_kWh"] = out["raw_value"]
        out["demand_kW"] = out["demand_kWh"] / (interval_minutes / 60.0)

    out = out.drop(columns=["raw_value"])
    return out


# ---------------------------------------------------------------------
# Validation and resampling
# ---------------------------------------------------------------------



def detect_zero_demand_periods(
    df: pd.DataFrame,
    min_duration_minutes: int = 60,
    zero_tolerance_kWh: float = 0.0,
) -> pd.DataFrame:
    """
    Detect consecutive zero-demand periods.

    Purpose:
        In NEM12 files, individual zero intervals may be valid. However, long
        consecutive zero periods can indicate a meter issue, register problem,
        site outage, or a period requiring manual review.

    Args:
        df:
            Clean demand table containing timestamp, demand_kWh, and preferably
            interval_minutes.
        min_duration_minutes:
            Minimum duration for a zero period to be reported.
        zero_tolerance_kWh:
            Values less than or equal to this threshold are treated as zero.
            Keep 0.0 for exact-zero detection.

    Returns:
        DataFrame with long zero periods:
            zero_start
            zero_end_inclusive
            zero_end_exclusive
            zero_intervals
            duration_minutes
            duration_hours
    """
    if "timestamp" not in df.columns or "demand_kWh" not in df.columns:
        raise ValueError("DataFrame must contain 'timestamp' and 'demand_kWh'.")

    data = df.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data = data.sort_values("timestamp").reset_index(drop=True)

    if "interval_minutes" in data.columns and data["interval_minutes"].notna().any():
        interval_minutes = int(data["interval_minutes"].mode().iloc[0])
    else:
        diffs = data["timestamp"].diff().dropna()
        interval_minutes = int(diffs.mode().iloc[0].total_seconds() / 60) if not diffs.empty else 5

    is_zero = data["demand_kWh"].notna() & (data["demand_kWh"] <= zero_tolerance_kWh)

    if not is_zero.any():
        return pd.DataFrame(
            columns=[
                "zero_start",
                "zero_end_inclusive",
                "zero_end_exclusive",
                "zero_intervals",
                "duration_minutes",
                "duration_hours",
            ]
        )

    group_id = (is_zero != is_zero.shift(fill_value=False)).cumsum()
    rows = []

    for _, group in data[is_zero].groupby(group_id[is_zero]):
        zero_intervals = len(group)
        duration_minutes = zero_intervals * interval_minutes

        if duration_minutes >= min_duration_minutes:
            zero_start = group["timestamp"].iloc[0]
            zero_end_inclusive = group["timestamp"].iloc[-1]
            zero_end_exclusive = zero_end_inclusive + pd.Timedelta(minutes=interval_minutes)

            rows.append(
                {
                    "zero_start": zero_start,
                    "zero_end_inclusive": zero_end_inclusive,
                    "zero_end_exclusive": zero_end_exclusive,
                    "zero_intervals": zero_intervals,
                    "duration_minutes": duration_minutes,
                    "duration_hours": duration_minutes / 60.0,
                }
            )

    return pd.DataFrame(rows).sort_values("duration_minutes", ascending=False).reset_index(drop=True)


def summarise_zero_demand_periods(
    zero_periods: pd.DataFrame,
) -> Dict[str, object]:
    """Create a compact summary of long zero-demand periods."""
    if zero_periods is None or zero_periods.empty:
        return {
            "long_zero_period_count": 0,
            "longest_zero_duration_minutes": 0,
            "longest_zero_duration_hours": 0,
            "longest_zero_start": None,
            "longest_zero_end_exclusive": None,
        }

    longest = zero_periods.sort_values("duration_minutes", ascending=False).iloc[0]
    return {
        "long_zero_period_count": int(len(zero_periods)),
        "longest_zero_duration_minutes": int(longest["duration_minutes"]),
        "longest_zero_duration_hours": float(longest["duration_hours"]),
        "longest_zero_start": longest["zero_start"],
        "longest_zero_end_exclusive": longest["zero_end_exclusive"],
    }

def validate_demand_data(
    df: pd.DataFrame,
    expected_interval_minutes: Optional[int] = None,
    long_zero_min_duration_minutes: int = 60,
) -> Dict[str, object]:
    """
    Validate timestamp continuity and demand values.

    Important:
        Zero demand values are not automatically errors.
        They are counted separately because some NEM12 registers can be all-zero.
    """
    if "timestamp" not in df.columns or "demand_kWh" not in df.columns:
        raise ValueError("DataFrame must contain 'timestamp' and 'demand_kWh'.")

    data = df.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data = data.sort_values("timestamp")

    if expected_interval_minutes is None:
        if "interval_minutes" in data.columns and data["interval_minutes"].notna().any():
            expected_interval_minutes = int(data["interval_minutes"].mode().iloc[0])
        else:
            diffs = data["timestamp"].diff().dropna()
            expected_interval_minutes = int(diffs.mode().iloc[0].total_seconds() / 60) if not diffs.empty else 5

    full_index = pd.date_range(
        start=data["timestamp"].min(),
        end=data["timestamp"].max(),
        freq=f"{expected_interval_minutes}min",
    )

    actual_index = pd.DatetimeIndex(data["timestamp"])
    missing_timestamps = full_index.difference(actual_index)
    duplicate_count = int(data["timestamp"].duplicated().sum())
    negative_count = int((data["demand_kWh"] < 0).sum())
    missing_value_count = int(data["demand_kWh"].isna().sum())
    zero_count = int((data["demand_kWh"].fillna(0) == 0).sum())

    total_records = len(data)
    interval_hours = expected_interval_minutes / 60.0
    total_energy = float(data["demand_kWh"].sum(skipna=True))
    peak_interval = float(data["demand_kWh"].max(skipna=True))
    peak_demand_kw = peak_interval / interval_hours if interval_hours > 0 else None

    zero_periods = detect_zero_demand_periods(
        data,
        min_duration_minutes=long_zero_min_duration_minutes,
    )
    zero_period_summary = summarise_zero_demand_periods(zero_periods)

    return {
        "start": data["timestamp"].min(),
        "end": data["timestamp"].max(),
        "records": total_records,
        "expected_interval_minutes": expected_interval_minutes,
        "missing_timestamp_count": len(missing_timestamps),
        "duplicate_timestamp_count": duplicate_count,
        "negative_demand_count": negative_count,
        "missing_value_count": missing_value_count,
        "zero_demand_count": zero_count,
        "zero_demand_pct": zero_count / total_records if total_records else None,
        "long_zero_min_duration_minutes": long_zero_min_duration_minutes,
        **zero_period_summary,
        "total_energy_kWh": total_energy,
        "peak_interval_kWh": peak_interval,
        "peak_demand_kW_est": float(peak_demand_kw) if peak_demand_kw is not None else None,
    }


def resample_for_optimisation(
    df: pd.DataFrame,
    freq: str = "1h",
) -> pd.DataFrame:
    """
    Aggregate demand data to a modelling timestep, usually 1h or 30min.

    Input:
        timestamp, demand_kWh

    Output:
        timestamp, demand_kWh, demand_kW_avg
    """
    if "timestamp" not in df.columns or "demand_kWh" not in df.columns:
        raise ValueError("DataFrame must contain 'timestamp' and 'demand_kWh'.")

    data = df.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data = data.sort_values("timestamp").set_index("timestamp")

    out = data.resample(freq).agg({"demand_kWh": "sum"}).dropna().reset_index()

    interval_hours = pd.to_timedelta(freq).total_seconds() / 3600
    out["demand_kW_avg"] = out["demand_kWh"] / interval_hours

    return out


# ---------------------------------------------------------------------
# Main interface
# ---------------------------------------------------------------------

def prepare_model_inputs(
    file_path: str | Path,
    optimisation_freq: str = "1h",
    timestamp_col: str = "timestamp",
    value_col: str = "demand_kW",
    input_mode: CleanInputMode = "power_kW",
    active_energy_registers: Optional[List[str]] = None,
    long_zero_min_duration_minutes: int = 60,
    return_register_level: bool = False,
    return_long_zero_periods: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, object]]:
    """
    Main function for the input data processing module.

    Returns:
        df_raw_resolution:
            Cleaned original-resolution active demand data, usually 5-minute.
        df_optimisation:
            Aggregated data for optimisation, usually hourly.
        validation_summary:
            Dictionary of data quality checks.

    If return_register_level=True and/or return_long_zero_periods=True, extra outputs are appended:
        df_register_level, register_summary, long_zero_periods
    """
    input_type = detect_input_type(file_path)

    df_register_level = None
    register_summary = None

    if input_type == "nem12_csv":
        df_register_level = parse_nem12_csv(file_path)
        register_summary = summarise_nem12_registers(df_register_level)
        df_raw = build_active_energy_demand(
            df_register_level,
            active_energy_registers=active_energy_registers,
            aggregate_kwh_registers=True,
        )

    elif input_type == "nem12_excel":
        df_register_level = parse_nem12_excel(file_path)
        register_summary = summarise_nem12_registers(df_register_level)
        df_raw = build_active_energy_demand(
            df_register_level,
            active_energy_registers=active_energy_registers,
            aggregate_kwh_registers=True,
        )

    else:
        df_raw = parse_tabular_timeseries(
            file_path=file_path,
            timestamp_col=timestamp_col,
            value_col=value_col,
            input_mode=input_mode,
        )

    validation = validate_demand_data(
        df_raw,
        long_zero_min_duration_minutes=long_zero_min_duration_minutes,
    )
    long_zero_periods = detect_zero_demand_periods(
        df_raw,
        min_duration_minutes=long_zero_min_duration_minutes,
    )
    df_optimisation = resample_for_optimisation(df_raw, freq=optimisation_freq)

    if register_summary is not None:
        validation["register_summary"] = register_summary.to_dict(orient="records")
        validation["selected_registers"] = (
            df_raw["selected_registers"].iloc[0]
            if "selected_registers" in df_raw.columns and not df_raw.empty
            else None
        )

    outputs = [df_raw, df_optimisation, validation]

    if return_register_level:
        outputs.extend([df_register_level, register_summary])

    if return_long_zero_periods:
        outputs.append(long_zero_periods)

    return tuple(outputs)


def save_processed_outputs(
    df_raw: pd.DataFrame,
    df_optimisation: pd.DataFrame,
    validation: Dict[str, object],
    output_dir: str | Path = "data/processed",
    register_summary: Optional[pd.DataFrame] = None,
    long_zero_periods: Optional[pd.DataFrame] = None,
) -> None:
    """Save processed CSV files and validation summary."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    df_raw.to_csv(output_path / "demand_clean_raw_resolution.csv", index=False)
    df_optimisation.to_csv(output_path / "demand_for_optimisation.csv", index=False)

    validation_to_save = validation.copy()
    # Flatten register summary if present, because it is nested and better saved separately.
    validation_to_save.pop("register_summary", None)
    pd.DataFrame([validation_to_save]).to_csv(output_path / "validation_summary.csv", index=False)

    if register_summary is not None:
        register_summary.to_csv(output_path / "register_summary.csv", index=False)

    if long_zero_periods is not None:
        long_zero_periods.to_csv(output_path / "long_zero_periods.csv", index=False)


if __name__ == "__main__":
    example_file = "data/raw/example_nem12.xlsx"

    if Path(example_file).exists():
        raw, opt, summary, reg, reg_summary, zero_periods = prepare_model_inputs(
            example_file,
            optimisation_freq="1h",
            return_register_level=True,
            return_long_zero_periods=True,
        )
        save_processed_outputs(
            raw,
            opt,
            summary,
            register_summary=reg_summary,
            long_zero_periods=zero_periods,
        )
        print("Validation summary:")
        print(summary)
        print("\nRegister summary:")
        print(reg_summary)
        print("\nLong zero periods:")
        print(zero_periods.head())
        print("\nOptimisation input preview:")
        print(opt.head())
    else:
        print("Example file not found. Replace example_file with your actual NEM12 or CSV/XLSX file path.")
