import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from src.input_data_processing_v3 import (
    prepare_model_inputs,
    save_processed_outputs
)

DATASET_NAME = "piggery_data"
DATASET_TYPE = "real"
DATASET_EXTENSION = "xlsx"

file_path = f"data/raw/{DATASET_TYPE}/{DATASET_NAME}.{DATASET_EXTENSION}"

df_5min, df_hourly, validation, register_level, register_summary, long_zero_periods = prepare_model_inputs(
    file_path=file_path,
    optimisation_freq="1h",
    active_energy_registers=["E1"],
    return_register_level=True,
    return_long_zero_periods=True,
    long_zero_min_duration_minutes=60,
)

print("Validation summary:")
print(validation)

print("\n5-min data preview:")
print(df_5min.head())

print("\nHourly optimisation data preview:")
print(df_hourly.head())

print("\nRegister summary:")
print(register_summary)

print("\nLong zero periods:")
print(long_zero_periods)



save_processed_outputs(
    df_5min,
    df_hourly,
    validation,
    output_dir=f"data/processed/{DATASET_TYPE}/{DATASET_NAME}",
    register_summary=register_summary,
    long_zero_periods=long_zero_periods
)