"""
returns the plots and soved configurataions

"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from config import *
from inputs import *
import matplotlib.dates as mdates

######### extract sizing decisions ########
def get_sizing(variables):
    # number of panels by orientation
    panels = {}
    for d in orientations:
        panels[d] = variables["n_panels"][d].varValue
    pv_capacity = panel_capacity * sum(panels.values())

    return {
        "battery capacity (kWh)": variables["S_b"].varValue,
        "battery inverter (kW)": variables["S_inv_b"].varValue,
        "panels by direction": panels,
        "pv capacity": pv_capacity,
        "generator size (kW)": variables["S_m"].varValue,
    }


######### extract hourly energy flow and battery state #######
def get_dispatch(variables, times):
    flow_names = ["x_G_to_SN", "x_SN_to_G", "x_PV_to_SN", "x_SN_to_B",
                  "x_B_to_SN", "x_M_to_SN", "x_SN_to_L", "E_B"]
    hourly_flows = {}
    for i in flow_names:
        hourly_flows[i] = [variables[i][t].varValue for t in time_intervals]
    dispatch = pd.DataFrame(hourly_flows, index=times)
    pv_available = []
    for t in time_intervals:
        pv_available.append(sum(panel_capacity * variables["n_panels"][d].varValue
                                * float(pv_profile[d][t]) * timestep for d in orientations))
    dispatch["PV_curtailed"] = np.array(pv_available) - dispatch["x_PV_to_SN"]
    dispatch["grid_outage"] = [1 - float(grid_available[t]) for t in time_intervals]
    return dispatch


######### calculate totals ########
def get_dispatch_summary(variables, times):

    dispatch = get_dispatch(variables, times)

    summary = {
        "Grid import (kWh)": dispatch["x_G_to_SN"].sum(),
        "Grid export (kWh)": dispatch["x_SN_to_G"].sum(),
        "PV used (kWh)": dispatch["x_PV_to_SN"].sum(),
        "Battery charged (kWh)": dispatch["x_SN_to_B"].sum(),
        "Battery discharged (kWh)": dispatch["x_B_to_SN"].sum(),
        "Generator output (kWh)": dispatch["x_M_to_SN"].sum(),
        "Peak grid import (kW)": dispatch["x_G_to_SN"].max() / timestep,
        "Peak generator output (kW)": dispatch["x_M_to_SN"].max() / timestep,
        "Minimum battery SOC (kWh)": dispatch["E_B"].min(),
        "Maximum battery SOC (kWh)": dispatch["E_B"].max(),
        "Load (kWh)": dispatch["x_SN_to_L"].sum(),
        "PV curtailed (kWh)": dispatch["PV_curtailed"].sum(),
        "Generator run hours": (dispatch["x_M_to_SN"] > 1e-6).sum() * timestep,
    }
    return summary

######## who pays what ########
def financial_summary(run):
    out = {"Farm bill with no assets (grid only)": farm_bill_grid_only}
    if ownership == "third_party":
        # run["cost"] is the third party's cost of supplying the farm (spot trading + equipment)
        total_load = float(load_kwh.sum())
        farm_bill = tpo_fixed_rate * total_load  # the farm buys all its energy from the third party
        out["Farm bill over period"] = farm_bill
        out["Third-party profit over period"] = farm_bill - run["cost"]
        # any fixed rate between these two leaves both sides better off
        out["Third-party break-even rate (c/kWh)"] = 100 * run["cost"] / total_load
        out["Farm max acceptable rate (c/kWh)"] = 100 * farm_bill_grid_only / total_load
    else:
        farm_bill = run["cost"]  # the farm owns everything, so it pays all of it
        out["Farm bill over period"] = farm_bill
    out["Farm saving vs grid only"] = farm_bill_grid_only - farm_bill
    return out



######## collect the front into a table ########
def summarise(results, times):
    summary_rows = []
    for run in results:
        row = {"cost over modelled period": run["cost"], "emissions over modelled period": run["emissions"]}
        row.update(run["breakdown"])
        sizing = get_sizing(run["vars"])
        panels = sizing.pop("panels by direction")
        row.update(sizing)
        for d in panels:
            row[f"panels_{d}"] = panels[d]   # one column per orientation
        row.update(get_dispatch_summary(run["vars"], times))
        row.update(financial_summary(run))
        summary_rows.append(row)
    return pd.DataFrame(summary_rows)


######## F6 outputs ###############
def draw_front(ax, front):
    sorted_front = front.sort_values("emissions over modelled period") # from lowest to highest emissions
    ax.plot(sorted_front["emissions over modelled period"], sorted_front["cost over modelled period"], "o-", color="tab:blue", zorder=2)
    # labels
    hours = horizon * timestep
    period = "year" if hours == 8760 else f"{hours / 24:g} days"
    ax.set_xlabel(f"Emissions (kgCO$_2$e per {period})")
    if ownership == "third_party":
        ax.set_ylabel(f"Third-party cost of supply ($ per {period})")
    else:
        ax.set_ylabel(f"Cost ($ per {period})")
    ax.set_title("Pareto front: cost vs emissions")
    ax.grid(alpha=0.3, zorder=0)
    # label the two edge case points
    ax.annotate("min emissions", (sorted_front["emissions over modelled period"].iloc[0], sorted_front["cost over modelled period"].iloc[0]),
                textcoords="offset points", xytext=(8, 6))
    ax.annotate("min cost", (sorted_front["emissions over modelled period"].iloc[-1], sorted_front["cost over modelled period"].iloc[-1]),
                textcoords="offset points", xytext=(-8, 10), ha="right")



def draw_dispatch(subfig, variables, times, title="", n_steps=None):
    d = get_dispatch(variables, times)
    if n_steps is not None:
        d = d.iloc[:n_steps] 
    ax1, ax2 = subfig.subplots(2, 1, sharex=True)
    ax1.stackplot(d.index, d["x_PV_to_SN"], d["x_B_to_SN"], d["x_G_to_SN"], d["x_M_to_SN"],
                  labels=["PV", "Battery", "Grid", "Generator"], step="post")
    ax1.plot(d.index, d["x_SN_to_L"], "k-", drawstyle="steps-post", label="Load")
    ax1.plot(d.index, d["x_SN_to_L"] + d["x_SN_to_B"] + d["x_SN_to_G"], "k--",
             drawstyle="steps-post", label="Load + charging + export")
    # battery state of charge
    ax2.plot(d.index, d["E_B"], color="tab:green")
    for ax in (ax1, ax2):
        ax.fill_between(d.index, 0, d["grid_outage"], step="post", color="grey", alpha=0.2,
                        transform=ax.get_xaxis_transform(), label="Grid outage")
    # labels
    ax1.set_ylabel("kWh per timestep")
    ax2.set_ylabel("Battery SOC (kWh)")
    locator = mdates.AutoDateLocator(tz=d.index.tz)
    ax2.xaxis.set_major_locator(locator)
    ax2.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=d.index.tz))
    ax2.set_xlabel("Time")
    ax1.legend(ncol=3, fontsize=8)
    subfig.suptitle(title)


######## best trade-off point ########
def knee_point(front):
    cost = front["cost over modelled period"]
    emis = front["emissions over modelled period"]
    # scale both to 0-1 so neither objective dominates
    cost_norm = (cost - cost.min()) / (cost.max() - cost.min())
    emis_norm = (emis - emis.min()) / (emis.max() - emis.min())
    return int((cost_norm + emis_norm).idxmin())


def plot_all(front, results, times, fname="results.png"):
    fig = plt.figure(figsize=(13, 8), layout="constrained")
    (top_left, top_right), (bottom_left, bottom_right) = fig.subfigures(2, 2)
    draw_front(top_left.subplots(), front)
    k = knee_point(front)
    draw_dispatch(top_right, results[k]["vars"], times, title=f"Best trade-off (point {k + 1})", n_steps=168)
    draw_dispatch(bottom_left, results[0]["vars"], times, title="Lowest-emissions point", n_steps=168)
    draw_dispatch(bottom_right, results[-1]["vars"], times, title="Lowest-cost point", n_steps=168)
    fig.savefig(fname, dpi=150)
    plt.show()