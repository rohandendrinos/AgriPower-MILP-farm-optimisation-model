import pandas as pd

from config import *
from inputs import *
from milp import pareto_front
from results import summarise, plot_all

pd.set_option("display.max_columns", None)


def main():
    results = pareto_front(pv_profile)
    summary = summarise(results, times)
    print(summary.round(1).T)   # transposed: one column per pareto point
    summary.to_csv(f"pareto_front_{ownership}.csv", index=False)

    # two extremes
    plot_all(summary, results, times, fname=f"results_{ownership}.png")
    

if __name__ == "__main__":
    main()