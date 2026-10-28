"""
Solar generation for the five possible mounting options from PVGIS typical meteorological year weather.

Models a 1 kW array using the sandia array model, once per mounting
option (4 roof directions at roof_tilt, plus a ground mount at ground_tilt), and
returns output in kW per kW installed so the MILP can scale it by panel count.
"""

import pandas as pd
import pvlib
from pvlib import pvsystem
from pathlib import Path
from config import *  


# output directory structure
base_dir = REPO_ROOT  
data_dir = base_dir / 'data'
data_dir.mkdir(exist_ok=True)

# Create year-specific folder
if start_year == end_year:
    year_dir = data_dir / str(start_year)
else:
    year_dir = data_dir / f"{start_year}_{end_year}"
year_dir.mkdir(exist_ok=True)


def file_exists(filepath):
    """Check if file exists and return boolean."""
    exists = Path(filepath).exists()
    if exists:
        print(f"File already exists: {filepath}")
    return exists


def get_weather_data(lat, lon, year, start_year=2005, end_year=2023):
    """Fetch TMY weather data from PVGIS."""
    all_data = pvlib.iotools.get_pvgis_tmy(
        float(lat), float(lon),
        startyear=start_year, endyear=end_year,
        outputformat='json', map_variables=True
    )

    data = all_data[0].reset_index()
    data['time(UTC)'] = pd.to_datetime(data['time(UTC)'])
    data['time(UTC)'] = data['time(UTC)'].apply(lambda x: x.replace(year=year))
    data = data.set_index('time(UTC)')
    return data


def calculate_pv_profile(weather_data, latitude, longitude, surface_azimuth,
                        surface_tilt, type_pv='roof_flush'):
    """Calculate PV generation profile (kW per kW installed)."""
    temperature = weather_data['temp_air'].astype(float)
    windspeed = weather_data['wind_speed'].astype(float)
    ghi = weather_data['ghi']
    dni = weather_data['dni']
    dhi = weather_data['dhi']
    pressure = weather_data['pressure'].astype(float)

    sp = pvlib.solarposition.get_solarposition(
        weather_data.index, float(latitude), float(longitude), pressure=pressure
    )

    irradiance = pvlib.irradiance.get_total_irradiance(
        surface_tilt, surface_azimuth, sp.apparent_zenith, sp.azimuth,
        dni, ghi, dhi
    )

    modules = pvsystem.retrieve_sam('SandiaMod').T
    modules_multi_si = modules[modules['Material'] == 'mc-Si']
    multi_si = modules_multi_si[
        modules_multi_si.index.str.contains('Sharp_NDQ235F4__2013_')
    ].T.iloc[:, 0]

    if type_pv == 'roof_flush':
        temp_model_params = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm']['close_mount_glass_glass']
    elif type_pv == 'roof_tilt':
        temp_model_params = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_polymer']
    else:
        raise ValueError('Invalid type of PV panel installation')

    celltemp = pvlib.temperature.sapm_cell(
        irradiance.poa_global, temperature, windspeed, **temp_model_params
    )

    airmass = pvlib.atmosphere.get_relative_airmass(sp.apparent_zenith)
    airmass_absolute = pvlib.atmosphere.get_absolute_airmass(airmass, pressure=pressure)
    aoi = pvlib.irradiance.aoi(surface_tilt, surface_azimuth, sp.zenith, sp.azimuth)

    effective_irradiance = pvlib.pvsystem.sapm_effective_irradiance(
        irradiance.poa_direct, irradiance.poa_diffuse,
        airmass_absolute, aoi, multi_si
    )

    dc = pvlib.pvsystem.sapm(effective_irradiance, celltemp, multi_si)
    kWp = (multi_si.Impo * multi_si.Vmpo) / 1000

    sapm_inverters = pvlib.pvsystem.retrieve_sam('cecinverter')
    inverter = sapm_inverters['ABB__MICRO_0_25_I_OUTD_US_208__208V_']
    ac = pvlib.inverter.sandia(dc['v_mp'], dc['p_mp'], inverter)

    ac_mod = ac.fillna(0) / 1000
    ac_mod[ac_mod < 0] = 0
    profile = (1/kWp) * ac_mod

    return profile


def generate_directional_solar_profiles(lat, lon, start_year, end_year, type_pv='roof_flush'):
    """Generate solar profiles for all four cardinal directions, plus a ground mount."""
    if start_year == end_year:
        filename = year_dir / f"solar_hourly_generation_{start_year}.csv"
    else:
        filename = year_dir / f"solar_hourly_generation_{start_year}_{end_year}.csv"

    if file_exists(filename):
        return pd.read_csv(filename)

    directions = {'north': 0, 'east': 90, 'south': 180, 'west': 270}
    tilt = roof_tilt # changable input

    all_data = []
    for year in range(start_year - 1, end_year + 1):
        year_weather_data = get_weather_data(lat, lon, year)

        year_profiles = {}
        for direction, azimuth in directions.items():
            year_profiles[direction] = calculate_pv_profile(
                year_weather_data, lat, lon, azimuth, tilt, type_pv
            )

        # ground mount, north-facing, open-rack temperature model
        year_profiles['ground'] = calculate_pv_profile(
            year_weather_data, lat, lon, 0, ground_tilt, 'roof_tilt'
        )

        year_df = pd.DataFrame({
            'timestamp': year_weather_data.index,
            'north_solar_capacity_kw_per_kw': year_profiles['north'].values,
            'east_solar_capacity_kw_per_kw': year_profiles['east'].values,
            'south_solar_capacity_kw_per_kw': year_profiles['south'].values,
            'west_solar_capacity_kw_per_kw': year_profiles['west'].values,
            'ground_solar_capacity_kw_per_kw': year_profiles['ground'].values
        })
        all_data.append(year_df)

    results_df = pd.concat(all_data, ignore_index=True)
    results_df.to_csv(filename, index=False)
    print(f"Created: {filename}")
    return results_df




def get_pv_profiles():
    """ Hourly AC output in kW per kW of panels installed, for each orientation. Because each
    step is one hour, this also equals kWh per kW. timestamps in local time, 
    from midnight on 1 January to
    the end of the horizon"""
    solar = generate_directional_solar_profiles(lat, lon, start_year, end_year)

    # convert solar profiles from UTC to the local timezone
    solar['timestamp'] = pd.to_datetime(solar['timestamp'], utc=True).dt.tz_convert(timezone)
    solar = solar.sort_values('timestamp').reset_index(drop=True)

    # Start at midnight on 1st of January (local time)
    local_start = pd.Timestamp(f"{start_year}-01-01", tz=timezone)
    solar = solar[solar['timestamp'] >= local_start]

    # map column names for use in milp
    columns = {f"{d.lower()}_solar_capacity_kw_per_kw": d for d in orientations}
    profiles = solar.set_index('timestamp').rename(columns=columns)[orientations]

    annual_yield = profiles.iloc[:8760].sum().to_dict()

    profiles = profiles.iloc[:horizon]
    assert len(profiles) == horizon, f"expected {horizon} rows, got {len(profiles)}"

    profiles_by_orientation = {}
    for orientation in orientations:
        profiles_by_orientation[orientation] = profiles[orientation].to_numpy()
    return profiles_by_orientation, profiles.index, annual_yield