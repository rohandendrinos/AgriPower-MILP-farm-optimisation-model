"""
AEMO NEM spot price download and processing, from EaaS groups code,
resamples to hourly instead of 5-minute timesteps.
"""
import os
import pandas as pd
import requests
from config import *
from solar_generation import file_exists 

spot_price_dir.mkdir(parents=True, exist_ok=True)
REQUEST_TIMEOUT = (10, 60)

def download_spot_prices(year, month, state=nem_region):
    """Download AEMO spot market prices for a given year and month."""
    filename = spot_price_dir / f'spot_prices_{year}_{month}_{state}.csv'

    if file_exists(filename):
        return filename

    url = f'https://www.aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{year}{month}_{state}.csv'

    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return filename
    except Exception as e:
        print(f"Error downloading spot prices: {e}")
        return None


def download_spot_prices_batch(years, months, state=nem_region, combine_per_year=True):
    """Download multiple months of spot price data and combine by year."""
    if isinstance(years, int):
        years = [years]

    if not combine_per_year:
        downloaded_files = []
        for year in years:
            for month in months:
                file_path = download_spot_prices(year, month, state)
                if file_path:
                    downloaded_files.append(file_path)
        return downloaded_files

    combined_files = []
    for year in years:
        combined_filename = spot_price_dir / f'spot_prices_{year}_{state}.csv'

        if file_exists(combined_filename):
            combined_files.append(combined_filename)
            continue

        all_data = []
        for month in months:
            file_path = download_spot_prices(year, month, state)
            if file_path:
                df = pd.read_csv(file_path)
                all_data.append(df)
                os.remove(file_path)

        if all_data:
            combined_df = pd.concat(all_data, ignore_index=True)
            combined_df.to_csv(combined_filename, index=False)
            print(f"Created: {combined_filename} ({len(combined_df)} records)")
            combined_files.append(combined_filename)

    return combined_files


def get_spot_price_profile():
    """Hourly spot price ($/kWh) for the configured region and year range.
    New wrapper (they called the batch download directly in a notebook cell) —
    ties their two pieces together into one function inputs.py can call."""
    years = list(range(spot_price_start_year, spot_price_end_year + 1))
    months = [f'{i:02d}' for i in range(1, 13)]
    combined_files = download_spot_prices_batch(years=years, months=months, state=nem_region)

    spot_df = pd.concat([pd.read_csv(f) for f in combined_files], ignore_index=True)
    spot_df['timestamp'] = pd.to_datetime(spot_df['SETTLEMENTDATE'])

    # RRP $/MWh -> $/kWh, same conversion as their align_dataframes_by_year
    spot_df['spot_price_kwh'] = spot_df['RRP'] / 1000
    spot_df = spot_df.sort_values('timestamp').set_index('timestamp')

    hourly = spot_df['spot_price_kwh'].resample('1h', closed='right', label='left').mean()

    local_start = pd.Timestamp(f"{spot_price_start_year}-01-01")
    local_end = pd.Timestamp(f"{spot_price_end_year + 1}-01-01")
    hourly = hourly[hourly.index >= local_start]

    expected_hours = int((local_end - local_start) / pd.Timedelta("1h"))
    assert len(hourly) == expected_hours, f"expected {expected_hours} hourly spot prices, got {len(hourly)}"
    assert not hourly.isna().any(), "spot prices have gaps, a month probably failed to download"
    return hourly