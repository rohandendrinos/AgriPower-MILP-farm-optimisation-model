"""
Hourly site data for each time varying data inputs for the whole
run duration, specified by horizon (up to 8760 timesteps)

currently just has mock data but will import processed data for actual tests 

"""
import math
import numpy as np
from config import *
from solar_generation import get_pv_profiles
import pandas as pd


######## time ######
time_intervals = range(horizon)   # timestep index


####### solar #######
pv_profile, times, pv_annual_yield = get_pv_profiles() # kw per kw installed


###### load #######
demand = pd.read_csv(load_data, parse_dates=["timestamp"]).set_index("timestamp")["demand_kWh"]
load_kwh = demand.reindex(times.tz_localize(None)).to_numpy()
# use the highest hourly demand
if grid_import_lim is None:
    peak_load_kw = demand[demand.index.year == model_year].max() / timestep
    grid_import_lim = math.ceil(peak_load_kw)
    print(f"grid import limit: {grid_import_lim} kW (from measured peak demand)")
else:
    # set in config: use that value as given
    print(f"grid import limit: {grid_import_lim} kW (from config)")
assert not np.isnan(load_kwh).any(), "meter data doesn't cover every hour of model_year"


# load splitting -> not implimented yet
#load_data = pd.read_csv("kerang_2023_load.csv", usecols=["Date", "D_crit", "D_noncrit"], parse_dates=["Date"], dayfirst=True)
#load_data = load_data.iloc[:horizon]
#load_crit = load_data["D_crit"].to_numpy()
#load_def = 
#load_ignore = 


# load splitting with percentages
load_crit  = load_kwh * load_split["critical"]
load_unmet  = load_kwh * load_split["unmet"]
load_defer = load_kwh * load_split["defer"]



###### blackouts #######
meter = pd.read_csv(raw_load_data, parse_dates=["timestamp"]).set_index("timestamp")
zero_minutes = ((meter["demand_kWh"] <= 0) * meter["interval_minutes"]).resample("1h").sum()
zero_minutes = zero_minutes.reindex(times.tz_localize(None), fill_value=0).to_numpy()
# 1 = grid available, 0 = blackout
grid_available = (zero_minutes < blackout_threshold_minutes).astype(int)
print(f"blackouts: {(grid_available == 0).sum()} hours without grid")
# during blackout load = median load 
metered = pd.Series(load_kwh, index=times.tz_localize(None)).where(zero_minutes == 0)
typical = metered.groupby(metered.index.hour).transform(lambda s: s.rolling(15, center=True, min_periods=1).median())
load_kwh = np.where(zero_minutes > 0, typical, load_kwh)
assert not np.isnan(load_kwh).any(), "an outage is too long to fill from the surrounding days"


###### prices, $/kWh #######
gst = 1 + bill["gst"] / 100
additional_energy_charges = (
        (bill["LRET"] + bill["SRES"] + bill["VEEC"] + bill["other_charges"]) * bill["other_loss_factor"]
        + bill["network_charge"]) * gst # c/kWh
# spot prices: used by the spot price case, and always by the third party
if use_price_forecast and not spot_price_case:
    raise ValueError("Price forecast is only available for the spot case: set spot_price_case = True")
if spot_price_case and use_price_forecast:
# forecast spot prices for forecast_year (real 2025 $/kWh, excl. GST)
# the import file already includes network, environmental and market charges and a 1 c/kWh retailer fee,
# so nothing is added here (do not add additional_energy_charges)
    col = f"{forecast_scenario}|{forecast_year}"
    idx = times.tz_localize(None)
    imp = pd.read_csv(forecast_dir / f"spot_import_per_kWh_{forecast_version}.csv.gz", index_col=0, parse_dates=True)
    exp = pd.read_csv(forecast_dir / f"spot_export_per_kWh_{forecast_version}.csv.gz", index_col=0, parse_dates=True)
    if col not in imp.columns:
        raise ValueError(f"No forecast column '{col}': check forecast_scenario and forecast_year (2026-2050)")
    spot_import_price = imp[col].reindex(idx).to_numpy()[:horizon]
    spot_export_price = exp[col].reindex(idx).to_numpy()[:horizon]
    assert not np.isnan(spot_import_price).any(), "forecast import prices don't cover every model hour"
    assert not np.isnan(spot_export_price).any(), "forecast export prices don't cover every model hour"
    print(f"prices: {forecast_scenario} {forecast_year} forecast ({forecast_version})")

elif spot_price_case or ownership == "third_party":
    from spot_prices import get_spot_price_profile
    spot_price_profile = get_spot_price_profile().reindex(times.tz_localize(None)).to_numpy()
    spot_import_price = spot_price_profile * bill["energy_loss_factor"] * gst + additional_energy_charges/100
    spot_export_price = spot_price_profile.copy()

# the farm's own tariff
if spot_price_case:
    grid_import_price = spot_import_price
    grid_export_price = spot_export_price
else:
    # retail tarif from energy bill
    # price = energy rate + additional fees
    peak_energy_price = bill["peak"] * bill["energy_loss_factor"] * gst
    off_peak_energy_price = bill["off_peak"] * bill["energy_loss_factor"] * gst
    
    # find the peak hours in local time
    hours = np.asarray(times.hour)
    is_peak = np.zeros(horizon, dtype=bool)
    for start, end in peak_hours:
        is_peak |= (hours >= start) & (hours < end)

    grid_import_price = np.where(is_peak, peak_energy_price, off_peak_energy_price) + additional_energy_charges
    grid_import_price = grid_import_price / 100 # c/kWh -> $/kWh
    grid_export_price = np.full(horizon, retail_export_price)

# what the farm would pay over the period with no assets at all, at its own tariff
farm_bill_grid_only = float(np.sum(grid_import_price * load_kwh))

# the third party buys and sells all grid energy at spot, so the model optimises against spot prices
if ownership == "third_party":
    grid_import_price = spot_import_price
    grid_export_price = spot_export_price



###### cost of running the generator ($/kWh) ######
diesel_cost_per_kWh = (diesel_cost / 10.7) / elec_generation_efficiency  # $/kWh
generator_price = np.full(horizon, diesel_cost_per_kWh)



######## grid emission each hour (kgCO2e per kWh)############
def get_seaoson(month):
    "converts a month number into its specific season"
    if month in (12, 1, 2):
        return "Summer"
    elif month in (3, 4, 5):
        return "Autumn"
    elif month in (6, 7, 8):
        return "Winter"
    else:
        return "Spring"
# turn the data into a pandas dataframe indexed by season and hour
# one row for each season hour combination
grid_emission_factor = pd.read_csv(grid_ef_data).set_index(["Season", "Hour"])

GHG_grid_out = []
GHG_grid_in = []
for t in times:
    season = get_seaoson(t.month)
    hour = t.hour
    row = grid_emission_factor.loc[(season, hour)] #corresponding to the season and the hour

    GHG_grid_in.append(row["Avg_Import_Intensity_kgCO2e_kWh"])
    GHG_grid_out.append(row["Avg_Export_Intensity_kgCO2e_kWh"])

GHG_grid_in = np.array(GHG_grid_in)
GHG_grid_out = np.array(GHG_grid_out)


# kgCO2e per kWh imported
#GHG_grid_import = 0.68 
#GHG_grid_in = np.full(horizon, GHG_grid_import)
# kgCO2e per kWh exported (credited)
#GHG_grid_export = 0.2 # feed out - (LCA data pending)
#GHG_grid_out = np.full(horizon, GHG_grid_export)




###### max solar pannels per roof orientation #######
def max_panels(roof_area):
    """ the maximum number of solar panels that can fit on each 
        roof direction """
    result = {}
    for direction, area in roof_area.items():
        result[direction] = math.floor(area * usable_fraction[direction] / panel_area)
    return result

max_panels_by_direction = max_panels(roof_area)




###### annualised capital costs #######
def crf(discount_rate, lifetime):
    """Calculates the capital recovery factor over the asset lifetime."""
    return (discount_rate * (1 + discount_rate) ** lifetime) / ((1 + discount_rate) ** lifetime - 1)
# lifecycle costs
# whoever owns the assets pays for them at their own cost of capital
owner_discount_rate = tpo_discount_rate if ownership == "third_party" else discount_rate
crf_battery = crf(owner_discount_rate, lifetime["battery"])
crf_inv = crf(owner_discount_rate, lifetime["inv"])
crf_pv = crf(owner_discount_rate, lifetime["pv"])
crf_gen = crf(owner_discount_rate, lifetime["generator"])
# upfront costs

#capex_battery = capex["battery"]
#capex_inv = capex["inv"]
capex_pv = capex["pv"]
capex_gen = capex["generator"]
# maintenence costs
maint_battery = maintinence["battery"]
maint_inv = maintinence["inv"]
maint_pv = maintinence["pv"]
maint_gen = maintinence["generator"]



##### lifecycle emissions (kgCO2e/kWh) ########
# lifetime outputs
pv_lifetime_output = pv_annual_yield["North"] * lifetime["pv"] # kWh per kW
battery_lifetime_discharge = battery_cycles_per_year * (1 - SOC_min) * lifetime["battery"]  # kWh per kWh capacity
# lifecycle emissions
GHG_pv_embodied = (LCA["pv"]["upstream"] + LCA["pv"]["downstream"]) / 1000 * pv_lifetime_output
GHG_battery_embodied  = (LCA["battery"]["upstream"] + LCA["battery"]["downstream"]) / 1000 * battery_lifetime_discharge
GHG_inv_embodied = GHG_inv_lifecycle
GHG_gen_embodied = GHG_gen_lifecycle


######### operation emissions ########
#solar
GHG_pv = LCA["pv"]["ongoing"] / 1000
# generator
diesel_kWh_per_litre = (diesel_energy_content * elec_generation_efficiency / 3.6) # kW/L
GHG_generator = diesel_emissions_factor / diesel_kWh_per_litre


######### checks ##########
assert len(times) == horizon
for direction in orientations:
    assert len(pv_profile[direction]) == horizon
assert len(load_kwh) == horizon
assert len(grid_available) == horizon
assert len(grid_import_price) == horizon
assert len(grid_export_price) == horizon
assert len(generator_price) == horizon
assert len(GHG_grid_in) == horizon
assert len(GHG_grid_out) == horizon
assert ownership in ("farm", "third_party"), "ownership must be 'farm' or 'third_party'"
assert not np.isnan(grid_import_price).any(), "prices don't cover every modelled hour"