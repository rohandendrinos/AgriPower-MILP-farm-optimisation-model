"""
Builds and solves the AgriPower mixed-integer linear optimisation model.
including the cost and emissions objectives and then runs through and 
returns the pareto front.
"""

import numpy as np
import pulp

from config import *
from inputs import *

scale = horizon * timestep / 8760 # mainly for testing

def build_model(pv_profile, eps=None, minimise="cost"):
 
    model = pulp.LpProblem("agri_power_optimisation_model", pulp.LpMinimize)

 
    ############ decision variables ##################
    # hourly energy flows
    x_G_to_SN = pulp.LpVariable.dicts("grid_to_node", time_intervals, lowBound=0)
    x_SN_to_G = pulp.LpVariable.dicts("node_to_grid", time_intervals, lowBound=0)
    x_PV_to_SN = pulp.LpVariable.dicts("solar_to_node", time_intervals, lowBound=0)
    x_SN_to_B = pulp.LpVariable.dicts("node_to_battery", time_intervals, lowBound=0)
    x_B_to_SN = pulp.LpVariable.dicts("battery_to_node", time_intervals, lowBound=0)
    x_M_to_SN = pulp.LpVariable.dicts("generator_to_node", time_intervals, lowBound=0)
    x_SN_to_LC = pulp.LpVariable.dicts("node_to_load_crit", time_intervals, lowBound=0) 
    x_SN_to_LNC = pulp.LpVariable.dicts("node_to_load_noncrit", time_intervals, lowBound=0)  # incorperate later
    #x_SN_to_LNCNF = pulp.lpVariable.dicts("node_to_load_noncrit_nonfulfilled," time_intervals, lowBound=0) # noncritical 


    # binaries
    a_g = pulp.LpVariable.dicts("grid_binary", time_intervals, cat="Binary")
    a_b = pulp.LpVariable.dicts("battery_binary", time_intervals, cat="Binary")
    a_gen = pulp.LpVariable.dicts("generator_on/off_binary", time_intervals, cat="Binary")

    # sizing
    n_panels = pulp.LpVariable.dicts("number_of_pannels", orientations, lowBound=0, cat="Integer") # 1 for each mounting option
    S_b = pulp.LpVariable("battery_size",   lowBound=0)
    S_inv_b  = pulp.LpVariable("S_inv_b",  lowBound=0, upBound=inv_max_capacity)
    # size the generator to what the farmer already has
    if gen_existing_capacity is not None:
        S_m = pulp.LpVariable("generator_size", lowBound=gen_existing_capacity, upBound=gen_existing_capacity)
        gen_upper = gen_existing_capacity
    # let the model choose the generator size
    else:
        S_m = pulp.LpVariable("generator_size", lowBound=0, upBound=gen_max_output)
        gen_upper = gen_max_output
 
    # battery state of charge, kWh
    E_b = pulp.LpVariable.dicts("battery_charge_each_hour", time_intervals, lowBound=0)



    ########### constraints ################
    # system node energy balance
    for t in time_intervals:
        model += (
            0 == x_G_to_SN[t] + x_B_to_SN[t] + x_PV_to_SN[t] + x_M_to_SN[t]
                 - x_SN_to_G[t] - x_SN_to_B[t] - x_SN_to_LC[t]
        )
 
    # critical load requirement
    # energy flow to the farm load equals the load needed each hour
    for t in time_intervals:
        model += (x_SN_to_LC[t] == float(load_kwh[t])) 

    # non critical load requirement
    # do later
 
    # grid connection
    for t in time_intervals:
        model += (x_G_to_SN[t] <= grid_import_lim * timestep * a_g[t] * float(grid_available[t]))
        model += (x_SN_to_G[t] <= grid_export_lim * timestep * (1 - a_g[t]) * float(grid_available[t]))
        model += (a_g[t] <= float(grid_available[t])) #prevent grid use in a blaackout


    # solar constraints
    # solar capacity is limited by the avaiilable space and by the max amount of solar energy available at each timestep
    for i in orientations: 
        model += (n_panels[i] <= max_panels_by_direction[i]) #max panels limited by space
    for t in time_intervals:
        pv_available = 0
        for i in orientations:
            pv_available += (panel_capacity * n_panels[i] * float(pv_profile[i][t]) * timestep) # total solar energy available at each timestep
        model += (x_PV_to_SN[t] <= pv_available)

 
    # battery inverter
    for t in time_intervals:
        # inverter limit
        model += (x_SN_to_B[t]  <= S_inv_b  * timestep)
        model += (x_B_to_SN[t]  <= S_inv_b  * timestep)
        # battery charge rate
        model += (x_SN_to_B[t] <= charge_rate * S_b * timestep)
        model += (x_B_to_SN[t] <= charge_rate * S_b * timestep)
        # one way flow per timestep
        model += (x_SN_to_B[t] <= inv_max_capacity * timestep * a_b[t])
        model += (x_B_to_SN[t] <= inv_max_capacity * timestep * (1 - a_b[t]))


    # generator output
    for t in time_intervals:
        model += (x_M_to_SN[t] <= S_m * timestep)
        model += (x_M_to_SN[t] <= gen_upper * timestep * a_gen[t])
        model += (x_M_to_SN[t] >= gen_min_fraction * S_m * timestep - gen_upper * timestep * (1-a_gen[t]))
 
 
    # battery SOC range
    for t in time_intervals:
        model += (E_b[t] <= S_b) #battery energy must be less than its total capacity
        model += (E_b[t] >= SOC_min * S_b)
 
    # battery state of charge at first timestep
    model += (E_b[0] == SOC_initial * S_b
                        + x_SN_to_B[0] * battery_efficiency_charge 
                        - x_B_to_SN[0] / battery_efficiency_discharge)

    # battery state of charge for all other timesteps
    for t in time_intervals[1:]:
        model += (E_b[t] == E_b[t - 1]
                            + x_SN_to_B[t] * battery_efficiency_charge
                            - x_B_to_SN[t] / battery_efficiency_discharge)

    # battery must have at least the same charge at the start of the simulation at the end
    model += (E_b[time_intervals[-1]] >= SOC_initial * S_b)


 
    ################# expressions #################
    # sum costs and revenue over each timestep
    grid_import_cost = 0
    generator_cost = 0
    export_revenue = 0
    for t in time_intervals:
        grid_import_cost += float(grid_import_price[t]) * x_G_to_SN[t]
        generator_cost += float(generator_price[t]) * x_M_to_SN[t]
        export_revenue += float(grid_export_price[t]) * x_SN_to_G[t]
    # operation costs
    C_op = grid_import_cost + generator_cost
    # revenue 
    R_op = export_revenue

    # solar
    total_panels = 0
    for i in orientations:
        total_panels += n_panels[i]
    pv_capacity = panel_capacity * total_panels
    pv_capital_cost = crf_pv * capex_pv * pv_capacity
    pv_maintenance_cost = maint_pv * capex_pv * pv_capacity
    # battery
    battery_capex = battery_cost_per_kWh * S_b # + battery_cost_per_kW * S_inv_b
    battery_capital_cost = crf_battery * battery_capex
    battery_maintenance_cost = maint_battery * battery_capex
    # inverter
    inverter_capex = inverter_cost_per_kW * S_inv_b
    inverter_capital_cost = crf_inv * inverter_capex
    inverter_maintenance_cost = maint_inv * inverter_capex
    # generator
    if gen_existing_capacity is not None:
        generator_capital_cost = 0
        generator_maintenance_cost = 0
    else:
        generator_capital_cost = crf_gen * capex_gen * S_m
        generator_maintenance_cost = maint_gen * capex_gen * S_m
    # upfront capital cost (one-off lump sum at install, not annualised)
    capex_upfront = battery_capex + inverter_capex + capex_pv * pv_capacity
    if gen_existing_capacity is None:
        capex_upfront += capex_gen * S_m

    # annual equiptment costs
    C_inst = (
        battery_capital_cost
        + battery_maintenance_cost
        + inverter_capital_cost
        + inverter_maintenance_cost
        + pv_capital_cost
        + pv_maintenance_cost
        + generator_capital_cost
        + generator_maintenance_cost
    )
 
    # cost for modelled time horizon
    Cost_expr = C_op - R_op + C_inst * (horizon * timestep / 8760)

    # total operation emissions
    GHG_op = 0
    for t in time_intervals:
        GHG_op += (
            float(GHG_grid_in[t]) * x_G_to_SN[t]
            + GHG_generator * x_M_to_SN[t]
            + GHG_pv * x_PV_to_SN[t]
            - float(GHG_grid_out[t]) * x_SN_to_G[t]
        )

    # annualised installation emissions
    GHG_inst = (
        GHG_battery_embodied / lifetime["battery"] * S_b
        + GHG_pv_embodied / lifetime["pv"] * pv_capacity
        + GHG_inv_embodied / lifetime["inv"] * S_inv_b
        + GHG_gen_embodied / lifetime["generator"] * S_m
    )
    # total embodied emissions from manufacturing
    GHG_upfront = GHG_battery_embodied * S_b + GHG_pv_embodied * pv_capacity + GHG_inv_embodied * S_inv_b
    if gen_existing_capacity is None:
        GHG_upfront += GHG_gen_embodied * S_m
 
    # emissions for moddeled time horizon
    Emis_expr = GHG_op + GHG_inst * (horizon * timestep / 8760) 


 
    ################# objective ##################
    if minimise == "cost":
        model += Cost_expr
    elif minimise == "emis":
        model += Emis_expr
    else:
        raise ValueError("minimise must be 'cost' or 'emis'")

 
    ############## epsilon constraint ##################
    # eps = the emission limit
    if eps is not None:
        model += (Emis_expr <= eps, "eps_emissions")

 
    ################ return model variables ##################
    # all of the solved optimisation variables
    variables = {
        "x_G_to_SN": x_G_to_SN, "x_SN_to_G": x_SN_to_G, "x_PV_to_SN": x_PV_to_SN,
        "x_SN_to_B": x_SN_to_B, "x_B_to_SN": x_B_to_SN, "x_M_to_SN": x_M_to_SN, "x_SN_to_L": x_SN_to_LC,
        "E_B": E_b, "a_g": a_g, "a_b": a_b, "a_gen": a_gen, 
        "n_panels": n_panels, "S_b": S_b, "S_inv_b": S_inv_b, "S_m": S_m,
        "Cost_expr": Cost_expr, "Emis_expr": Emis_expr,
        "C_op": C_op, "R_op": R_op, "C_inst": C_inst * scale,
        "GHG_op": GHG_op, "GHG_inst": GHG_inst * scale, "capex_upfront": capex_upfront, "GHG_upfront": GHG_upfront,
    }
    return model, variables



####### solve the model ########
def solve_model(pv_profile, eps=None, minimise="cost"):
    """ build the optimisation model, solve it, checks the validity
    of the solve, and then returns the result"""
    model, variables = build_model(pv_profile, eps=eps, minimise=minimise)
    model.solve(pulp.GUROBI_CMD(path="/Library/gurobi1303/macos_universal2/bin/gurobi_cl", msg=1, options=[("MIPGap", 0.01)]))    
    #model.solve(pulp.PULP_CBC_CMD(msg=1, gapRel=0.01)) # solver stops at 1% accuracy
    status = pulp.LpStatus[model.status]
    # check if solution is optimal
    if status != "Optimal":
        print(f"solve returned {status} (eps={eps}, minimise={minimise})")
        return None
    else:
        # cost and emissions broken into their parts, with readable labels
        labels = {
            "C_op": "Operating cost over period",
            "R_op": "Export revenue over period",
            "C_inst": "Equipment cost for period",
            "GHG_op": "Operating emissions over period (kgCO2e)",
            "GHG_inst": "asset emissions over period (kgCO2e)",
            "capex_upfront": "Upfront capital cost of whole system",
            "GHG_upfront": "Upfront embodied emissions, one-off (kgCO2e)",
        }
        breakdown = {}
        for k in labels:
            breakdown[labels[k]] = pulp.value(variables[k])

        return {
            "status": status,
            "cost": pulp.value(variables["Cost_expr"]),
            "emissions": pulp.value(variables["Emis_expr"]),
            "breakdown": breakdown,
            "vars": variables,
        }


######## generate pareto front via epsilon constraints ########
def pareto_front(pv_profile, n_steps=n_epsilon_steps):
    """ generate the perato front by repeatedly solving the model
    with different emission limits that are fed to the model by eps.
    eps has the same number of different values as the number of pereto points chosen"""
    edge_case_cost = solve_model(pv_profile, minimise="cost") # minimise cost with no emissions constraint
    edge_case_emis = solve_model(pv_profile, minimise="emis") # minimise emissions with no cost constraint
    if edge_case_cost is None or edge_case_emis is None:
        raise RuntimeError("edge case solves failed, the model is not fesable")

    GHG_max = edge_case_cost["emissions"] #chepaest
    GHG_min = edge_case_emis["emissions"] #most expensive

    # for each run find the cheapest solution whos emissions are less than or equal to eps emissions
    results = []
    for eps in np.linspace(GHG_min + 0.01 * (GHG_max - GHG_min), GHG_max, n_steps):
        run = solve_model(pv_profile, eps=eps, minimise="cost")
        if run is not None:
            results.append(run)
    return results


