"""AgriPower, multi-page Streamlit entry point.

This file wires up navigation only. Each page lives in views/, and the
shared code lives in the agripower package. The model itself lives in
current_code/, wrapped by agripower/optimiser.py; the app is a front end to it.

Navigation: New analysis (meter data, farm details, scenario, year, Start)
hands off to Results (the job's PIN and status flow and, once done, the
model's own figure and results table, with the report and CSV downloads).
"""

import streamlit as st

from agripower.ui import apply_theme, render_sidebar_footer

LOGO_PATH = "assets/agripower_logo.svg"
LOGO_MARK_PATH = "assets/agripower_mark.svg"


def main():
    st.set_page_config(page_title="AgriPower", page_icon="\U0001F33E", layout="wide")

    # Wordmark at the very top of the sidebar, above the nav links.
    st.logo(LOGO_PATH, icon_image=LOGO_MARK_PATH, size="large")
    apply_theme()

    pages = [
        st.Page("views/home.py", title="Home", icon=":material/home:", default=True),
        st.Page("views/new_analysis.py", title="New analysis", icon=":material/add_circle:"),
        st.Page("views/results.py", title="Results", icon=":material/insights:"),
        st.Page("views/about.py", title="About this tool", icon=":material/info:"),
    ]

    navigation = st.navigation(pages)
    render_sidebar_footer()
    navigation.run()


if __name__ == "__main__":
    main()
