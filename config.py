""" 
All of the flat model inputs can be adjusted from this file, 

All of the other files imports the entire contents of this file 
so the names of each input are global variables.

All time-varying inputs are processed and then imported into inputs.py

"""

import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# grid hourly emissions intensity
grid_ef_data = REPO_ROOT / "data" / "grid_ef_data.csv"

# hourly site load from processed nem12 meter data
load_data = REPO_ROOT / "data" / "processed" / "real" / "piggery_data" / "demand_for_optimisation.csv"

# simulation settings
timestep = 1.0 # hourly timesteps
horizon = 8760
model_year = 2025
selected_date = "2025-05-04"
# epsilon constraints
n_epsilon_steps = 10 # number of points on the Pareto front

# location
lat = -35.73
lon = 143.92
timezone = 'Etc/GMT-10'
location_name = 'east_kerang_piggery'
altitude = 77
roof_tilt = 20    
ground_tilt = 30  

# energy data settings
future_case = False
spot_price_case = True
#spot_price_case = False
nem_region = "VIC1"
spot_price_dir = REPO_ROOT / "data" / "raw" / "spot_price"
spot_price_start_year = model_year
spot_price_end_year = model_year

# price forecast settings (only used when spot_price_case = True)
use_price_forecast = True # True = use forecast prices for forecast_year; False = AEMO 2025 actual spot prices
forecast_year = 2036 # any year from 2026 to 2050
forecast_scenario = "Step Change" # "Step Change", "Accelerated Transition" or "Slower Growth"
forecast_version = "central_gradual" # "central_gradual" (main case) or "sensitivity_equilibrium"
forecast_dir = REPO_ROOT / "data" / "forecast"


# ownership scenario
#ownership = "farm"   
ownership = "third_party"  
tpo_fixed_rate = 0.15     # $/kWh the farm pays the third party
tpo_discount_rate = 0.07  # third party's cost of capital


# battery
battery_efficiency = 0.95 
battery_efficiency_charge = math.sqrt(battery_efficiency)
battery_efficiency_discharge = math.sqrt(battery_efficiency)
SOC_min = 0.1 # min state of charge = 10% of capacity
SOC_initial = 0.5 # battery starts of at 50%
charge_rate = 0.5 # charge/discharge rate normalised by capacity

# solar parameters
panel_area = 2.0  # area in m^2 per panel
panel_capacity = 0.45  # pannel capacity in kW per panel
usable_fraction = {
    "North": 0.8, "South": 0.8, "East": 0.8, "West": 0.8, "Ground": 1.0,}

# grid limits
grid_import_lim = 200 # kw
grid_export_lim = 30 # kw
# inverter limits
inv_max_capacity = 100 # kW
# generator limits
gen_existing_capacity = 0
gen_min_fraction = 0.3 # %
gen_max_output = 30 # kw 
diesel_cost = 2.5 # dollars per litre
elec_generation_efficiency = 0.35 

# blackouts from meter data
raw_load_data = load_data.with_name("demand_clean_raw_resolution.csv")
blackout_threshold_minutes = 30 

# load split (nnot actually implemented yet)
load_split = {"critical": 0.6, "unmet": 0.1, "defer": 0.3}
defer_max_hours = 6
defer_max_rate = 20 # kW

# costing
discount_rate = 0.07 # i in the crf
lifetime = {"battery": 15, "inv": 15, "pv": 25, "generator": 15} # years

battery_cost_per_kWh = 232     # $/kWh of storage (slope)
#battery_cost_per_kW = 71.9     # $/kW of power rating (intercept)
inverter_cost_per_kW = 464     # $/kW



capex = {"pv": 1000.0, "generator": 700} # $/kW $/kW
maintinence = {"battery": 0.01, "inv": 0.01, "pv": 0.015, "generator": 0.03} # fraction of CAPEX per year

# emissions
# NREL emissions in gCO2e/kWh for each installed unit
LCA = {
    "pv":        {"upstream": 28, "ongoing": 10, "downstream": 5},
    "battery":   {"upstream": 32, "ongoing": 0,  "downstream": 3.4}
}
# lifecycle emissions of the inverter and generator
GHG_inv_lifecycle = 0
GHG_gen_lifecycle = 0
battery_cycles_per_year = 365  # 1 cycle per day

# diesel fuel emissions
diesel_energy_content = 38.6 # MJ/L
diesel_EF_scope1 = 70.20 # kg CO2-e/GJ 
diesel_EF_scope3 = 17.3 # kg CO2-e/GJ
diesel_emissions_factor = diesel_energy_content * (diesel_EF_scope1 + diesel_EF_scope3) / 1000  # kg CO2-e/L
gen_fuel_use = elec_generation_efficiency


# roof space in each direction
roof_area = {
    "North": 500, "South": 500, "East": 500, "West": 500, "Ground": 1000,
}

# weather and solar settings
start_year = model_year
end_year = model_year
orientations = ["North", "South", "East", "West", "Ground"]


# retail energy bill
bill = {
    "peak": 8.789,
    "off_peak": 6.004,
    "LRET": 0.64476, # environmental charge
    "SRES": 0.5556, # environmental charge
    "VEEC": 1.15714, # environmental charge
    "network_charge": 10.62,
    "other_charges": 0.01262 + 0.05438,   
    "gst": 0, # %
    "energy_loss_factor": 1.059671, # energy charges
    "other_loss_factor": 1.0522, # environmental and market charges
}
peak_hours = [(15, 21)] # 3-9pm

retail_export_price = 0.03



# 2.6 MW ground space capacity