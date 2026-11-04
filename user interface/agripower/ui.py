"""Shared UI building blocks for the AgriPower multi-page app.

Everything in here is presentation only: the brand palette, the injected
CSS theme, the shared Plotly chart chrome, the stat-tile component, the
page-header component, and the "upload a NEM12 file" helper that more than
one page needs.

No parsing or calculation logic lives here: this module just calls
parse_nem12_file and formats what comes back.
"""

import pandas as pd
import streamlit as st

from agripower.constraints import ACCURACY_CAVEAT, group1_fields_on_default

# --- Brand colours ---
# Blue = grid / current-state numbers. Gold = solar-specific numbers, so
# colour alone tells the two apart. The gold is a deep amber rather than a
# pale lemon, so it still clears WCAG AA as bold text on a light card.
ACCENT_BLUE = "#0b5fff"
ACCENT_BLUE_DARK = "#0a53e0"
ACCENT_GOLD = "#a6790a"
ACCENT_GOLD_TRACK = "#f1e4c6"  # pale tint of the gold, for the gauge's unfilled track

BG_PAGE = "#f7f4ee"     # soft off-white page background, not stark white or Streamlit's grey
BG_CARD = "#ffffff"     # panel/tile surface, lifted slightly off the page
BG_SIDEBAR = "#ffffff"
INK_PRIMARY = "#211e17"
INK_SECONDARY = "#5b564a"
INK_MUTED = "#8a8471"
GRIDLINE = "#e8e3d6"
CHART_SURFACE = "#ffffff"

FONT_STACK = "'Inter', -apple-system, 'Segoe UI', sans-serif"


# ----------------------------------------------------------------------------
# Theme
# ----------------------------------------------------------------------------

def apply_theme():
    """Inject the shared stylesheet once, from the entry script.

    st.navigation reruns the entry script on every interaction before it
    runs the active page, so styling injected here applies to every page.
    """
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        /* Streamlit sets its own font-family on generated wrappers with
           enough specificity that a plain html/body rule loses the cascade,
           so force it through every descendant. Material icon glyphs render
           as ligature text in an icon font, so exclude them or they show as
           literal words. */
        .stApp, .stApp *:not([data-testid="stIconMaterial"]) {{
            font-family: {FONT_STACK} !important;
        }}
        .stApp {{ background-color: {BG_PAGE}; }}

        .block-container {{
            padding-top: 3.75rem;
            padding-bottom: 3rem;
            max-width: 1180px;
        }}

        h1, h2, h3, h4, .stMarkdown, p {{ color: {INK_PRIMARY}; }}

        /* --- Sidebar --- */
        [data-testid="stSidebar"] {{
            background-color: {BG_SIDEBAR};
            border-right: 1px solid {GRIDLINE};
        }}
        [data-testid="stSidebarNav"] {{ padding-top: 0.25rem; }}
        [data-testid="stSidebarNav"] a {{
            border-radius: 9px;
            margin: 1px 0.25rem;
        }}
        [data-testid="stSidebarNav"] a:hover {{
            background-color: rgba(11, 95, 255, 0.06);
        }}
        [data-testid="stSidebarNav"] a[aria-current="page"] {{
            background-color: rgba(11, 95, 255, 0.10);
        }}
        [data-testid="stSidebarNav"] a[aria-current="page"] span,
        [data-testid="stSidebarNav"] a[aria-current="page"] p {{
            color: {ACCENT_BLUE};
            font-weight: 600;
        }}

        /* --- Page header (page_header()) --- */
        .page-head {{
            padding-bottom: 0.9rem;
            margin-bottom: 1.5rem;
            border-bottom: 1px solid {GRIDLINE};
        }}
        .page-title {{
            font-size: 1.9rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            line-height: 1.15;
            margin: 0 0 0.4rem 0;
            color: {INK_PRIMARY};
        }}
        .page-subtitle {{
            font-size: 1rem;
            line-height: 1.5;
            color: {INK_SECONDARY};
            margin: 0;
            max-width: 62ch;
        }}

        /* --- Home page: hero and step cards --- */
        .hero-eyebrow {{
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: {ACCENT_BLUE};
            margin: 0.2rem 0 0.7rem 0;
        }}
        .hero-title {{
            font-size: clamp(1.9rem, 3.4vw, 2.7rem);
            font-weight: 800;
            letter-spacing: -0.025em;
            line-height: 1.1;
            margin: 0 0 0.9rem 0;
            color: {INK_PRIMARY};
            max-width: 22ch;
        }}
        .hero-sub {{
            font-size: 1.05rem;
            line-height: 1.55;
            color: {INK_SECONDARY};
            margin: 0 0 1.2rem 0;
            max-width: 62ch;
        }}
        .section-label {{
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: {INK_MUTED};
            margin: 1.8rem 0 0.7rem 0;
        }}
        .step-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
            gap: 1rem;
        }}
        .step-card {{
            background-color: {BG_CARD};
            border: 1px solid rgba(33, 30, 23, 0.06);
            border-radius: 16px;
            padding: 1.15rem 1.3rem 1.2rem 1.3rem;
            box-shadow: 0 1px 2px rgba(33, 30, 23, 0.04), 0 6px 20px rgba(33, 30, 23, 0.04);
        }}
        .step-num {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 1.9rem;
            height: 1.9rem;
            border-radius: 50%;
            background-color: {ACCENT_BLUE};
            color: #ffffff;
            font-weight: 700;
            font-size: 0.95rem;
            margin-bottom: 0.7rem;
        }}
        .step-title {{
            font-size: 1.08rem;
            font-weight: 700;
            color: {INK_PRIMARY};
            margin-bottom: 0.4rem;
        }}
        .step-body {{
            font-size: 0.93rem;
            line-height: 1.5;
            color: {INK_SECONDARY};
        }}

        /* --- Summary / side stat tiles --- */
        .stat-tile {{
            background-color: {BG_CARD};
            border: 1px solid rgba(33, 30, 23, 0.06);
            border-top: 3px solid transparent;
            border-radius: 14px;
            padding: 1rem 1.2rem;
            box-shadow: 0 1px 2px rgba(33, 30, 23, 0.04), 0 4px 14px rgba(33, 30, 23, 0.03);
        }}
        .stat-tile--blue {{ border-top-color: {ACCENT_BLUE}; }}
        .stat-tile--gold {{ border-top-color: {ACCENT_GOLD}; }}
        .stat-label {{
            font-size: 0.82rem;
            font-weight: 600;
            color: {INK_SECONDARY};
            margin-bottom: 0.3rem;
        }}
        .stat-grid {{
            /* Tiles share a row when there's room and wrap onto further rows
               when there isn't, sized from the content area rather than the
               browser window (the sidebar takes a share of the window). */
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(12.5rem, 1fr));
            gap: 1rem;
            margin-bottom: 0.75rem;
        }}
        .stat-value {{
            font-size: clamp(1.5rem, 2.2vw, 2rem);
            font-weight: 800;
            letter-spacing: -0.02em;
            line-height: 1.1;
            font-variant-numeric: proportional-nums;
        }}
        .stat-number {{ white-space: nowrap; }}
        .stat-unit {{
            font-size: 0.95rem;
            font-weight: 700;
            letter-spacing: 0;
            margin-left: 0.2rem;
        }}
        .stat-tile--blue .stat-value {{ color: {ACCENT_BLUE}; }}
        .stat-tile--gold .stat-value {{ color: {ACCENT_GOLD}; }}

        /* --- Bordered panels / cards (st.container(border=True)) --- */
        div[data-testid="stVerticalBlockBorderWrapper"] > div {{
            border-radius: 16px !important;
            border: 1px solid rgba(33, 30, 23, 0.06) !important;
            box-shadow: 0 1px 2px rgba(33, 30, 23, 0.04), 0 6px 20px rgba(33, 30, 23, 0.04);
            padding: 1.15rem 1.3rem;
            background-color: {BG_CARD};
        }}

        /* --- Buttons --- */
        .stButton > button {{
            border-radius: 10px;
            font-weight: 600;
            padding: 0.55rem 1rem;
        }}
        .stButton > button[kind="primary"] {{
            background-color: {ACCENT_BLUE};
            border-color: {ACCENT_BLUE};
        }}
        .stButton > button[kind="primary"]:hover {{
            background-color: {ACCENT_BLUE_DARK};
            border-color: {ACCENT_BLUE_DARK};
        }}
        /* The blanket `p {{ color }}` rule above would otherwise turn a
           primary button's label dark on the blue fill. */
        .stButton > button[kind="primary"] p {{ color: #ffffff; }}
        .stButton > button[kind="secondary"] {{
            color: {ACCENT_BLUE};
            border-color: rgba(11, 95, 255, 0.4);
        }}
        .stDownloadButton > button {{
            border-radius: 10px;
            font-weight: 600;
            padding: 0.55rem 1rem;
            border-color: rgba(11, 95, 255, 0.4);
        }}

        [data-testid="stMetricValue"] {{ color: {ACCENT_BLUE}; font-weight: 800; }}
        [data-testid="stMetricLabel"] {{ color: {INK_SECONDARY}; }}

        /* Inline code (file names, field names) as a quiet neutral chip,
           not Streamlit's default green-on-grey. */
        [data-testid="stMarkdownContainer"] code {{
            color: {INK_PRIMARY};
            background-color: rgba(33, 30, 23, 0.07);
            border-radius: 5px;
            padding: 0.1em 0.35em;
            font-size: 0.88em;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_footer():
    """A small persistent note under the nav, on every page."""
    with st.sidebar:
        st.write("")
        st.caption(
            "A university capstone project. Every result comes from the project's "
            "optimisation model; this app only collects the inputs and displays what it produces."
        )


def page_header(title: str, subtitle: str):
    """The standard title block every page opens with."""
    st.markdown(
        f"""
        <div class="page-head">
            <div class="page-title">{title}</div>
            <p class="page-subtitle">{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def format_window_label(start, end) -> str:
    """A solved window in plain words: "1 January 2025" for a single day,
    "1 January 2025 to 31 December 2025" for a longer one."""
    if start.date() == end.date():
        return f"{start:%d %B %Y}".lstrip("0")
    return f"{start:%d %B %Y}".lstrip("0") + " to " + f"{end:%d %B %Y}".lstrip("0")


# Measured on the development machine with real full-year runs of the sample
# farm (12 solves each: two anchors plus the 10-point trade-off sweep): 544 s for
# farmer ownership, 809 s for third-party ownership, both including their first
# downloads. Update here, and only here, if the model or the machine changes.
MEASURED_RUN_TIME_TEXT = (
    "In testing on the development machine, a complete full-year run (12 solves) took about 9 minutes "
    "for farmer ownership and about 14 minutes for third-party ownership. The first run for a new "
    "location also downloads weather data, and the first third-party run for a year downloads that "
    "year's spot prices; both add time, and a slower computer will take longer."
)


def render_group1_caveat(constraints) -> None:
    """The accuracy caveat, listing the Group 1 fields (the ones that change the
    optimisation result) that are still on default values. A visible note on the
    page, never a pop-up, and it never blocks the run. Used where an
    optimisation is started and where its results are shown."""
    on_default = group1_fields_on_default(constraints)
    if not on_default:
        st.success("Every Group 1 field (the ones that change the optimisation result) contains your own data.")
        return
    # A pair of "$" would be read as maths by Streamlit's markdown, so escape them.
    items = "\n".join(f"- {label}: {value}".replace("$", "\\$") for label, value in on_default)
    st.warning(f"{ACCURACY_CAVEAT}\n\n{items}")


def load_optimiser():
    """Import optimiser.py (and with it pvlib, pulp and the solver stack)
    behind a visible message.

    The first import in a server's lifetime takes several seconds, and a
    page that imports it at the top would otherwise sit on a blank screen
    for that long. Later calls return the already-imported module at once.
    """
    with st.spinner("Loading the optimiser (the first load takes a few seconds)..."):
        from agripower import optimiser
    return optimiser


# ----------------------------------------------------------------------------
# Shared NEM12 upload
# ----------------------------------------------------------------------------

# What the "Use sample farm" shortcut actually loads. Only facts checked against
# piggery_data.xlsx and constraints.FIELD_DEFAULTS are stated here: the file
# carries no herd size, production system or site description (only a meter ID
# and a file name), so none of those are claimed.
SAMPLE_FARM_DESCRIPTION = (
    "The sample farm is real meter data from a piggery (about two years of readings), run with "
    "the optimisation model's own default farm settings: the roof areas, costs and location "
    "(a reference point in north-west Victoria) it ships with, not that piggery's own details."
)


def load_shared_meter(uploader_key: str, recommendation: str = None):
    """Render the NEM12 upload widget and return the meter data prepared by the
    model's own input processing (optimiser.prepare_meter_data).

    The bytes of the file in use are kept in st.session_state, and preparing them
    is cached on those bytes, so the 20 seconds it takes happens once per file.

    Returns {"raw": ..., "hourly": ..., "validation": ...}, or None if nothing has
    been uploaded yet or the file could not be read.
    """
    uploaded_file = st.file_uploader(
        "Upload a NEM12 meter data file (.csv or .xlsx)",
        type=["csv", "xlsx", "xls"],
        key=uploader_key,
    )
    if recommendation:
        st.caption(recommendation)

    # A newly uploaded file only replaces the one already in use once it has
    # been read successfully, so a bad upload never throws away a good file.
    rejected_upload = False
    if uploaded_file is not None:
        new_bytes = uploaded_file.getvalue()
        try:
            _prepare_meter(new_bytes, uploaded_file.name)
        except Exception as error:
            rejected_upload = True
            st.error(
                f"We couldn't read **{uploaded_file.name}** as a NEM12 meter file. Check it's the "
                "standard NEM12 export (.csv or .xlsx) from your electricity retailer or "
                "distributor, or use the sample farm to see an example."
            )
            with st.expander("Technical details"):
                st.code(f"{type(error).__name__}: {error}", language=None)
        else:
            st.session_state["nem12_bytes"] = new_bytes
            st.session_state["nem12_name"] = uploaded_file.name

    meter = get_meter_data()
    if meter is None:
        return None
    file_name = st.session_state.get("nem12_name")
    if rejected_upload:
        st.caption(f"Still using **{file_name}**, the file you had loaded before.")
    elif uploaded_file is None:
        st.caption(f"Currently using **{file_name}**.")
    return meter


def get_meter_data():
    """The meter data already prepared this session, without a widget, or None."""
    file_bytes = st.session_state.get("nem12_bytes")
    file_name = st.session_state.get("nem12_name")
    if not file_bytes:
        return None
    try:
        return _prepare_meter(file_bytes, file_name)
    except Exception:
        return None


def uploaded_file_name():
    """Name of the NEM12 file uploaded this session, or None."""
    return st.session_state.get("nem12_name")


@st.cache_data(show_spinner="Reading the meter data with the model's own input processing (about 20 seconds)...")
def _prepare_meter(file_bytes: bytes, file_name: str) -> dict:
    """Cached wrapper around optimiser.prepare_meter_data, keyed on the file's bytes."""
    from agripower import optimiser

    return optimiser.prepare_meter_data(file_bytes, file_name)


# ----------------------------------------------------------------------------
# Shared farm constraints (the second main input, alongside the NEM12 file)
# ----------------------------------------------------------------------------

def get_shared_constraints():
    """The raw FarmConstraints saved on New analysis, or None if the
    farmer hasn't saved any this session. May still have
    None fields - callers that feed a solve or a calculation should use
    get_resolved_constraints() instead, which never has missing fields."""
    return st.session_state.get("farm_constraints")


def set_shared_constraints(constraints) -> None:
    st.session_state["farm_constraints"] = constraints


def get_resolved_constraints():
    """A fully-populated FarmConstraints, ready to feed a calculation.

    A blank field falls back to a default (see constraints.py), so this never
    fails: with no farm constraints saved at all, it resolves an empty FarmConstraints()
    to an entirely placeholder-driven one. `resolved.defaulted_fields`
    names exactly which values (if any) came from a placeholder rather than
    the farmer, for labelling on screen.
    """
    from agripower.constraints import FarmConstraints

    constraints = get_shared_constraints() or FarmConstraints()
    return constraints.resolve()


