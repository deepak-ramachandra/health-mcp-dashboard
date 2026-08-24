"""Shared UI helpers used by more than one page of the dashboard."""

# Semi-ring gauge geometry (shared by every progress ring in the app).
# st.html() strips <svg> tags, so the ring is drawn with a conic-gradient
# masked down to a stroke, clipped to its top half - no SVG involved.
RING_SIZE = 140  # circle diameter, px
RING_THICKNESS = 14  # stroke width, px


def progress_ring(
    value: float,
    goal: float,
    unit: str,
    label: str,
    accent: str,
    track: str,
    ink: str,
    muted_ink: str,
) -> str:
    fraction = max(0.0, min(value / goal, 1.0)) if goal else 0.0
    progress_deg = fraction * 180
    display_value = f"{value:,.0f}"
    radius = RING_SIZE // 2
    return f"""
    <div style="flex:0 0 auto; text-align:center; font-family:inherit;">
      <div style="position:relative; width:{RING_SIZE}px; height:{radius + 8}px; margin:0 auto; overflow:hidden;">
        <div style="position:absolute; top:0; left:0; width:{RING_SIZE}px; height:{RING_SIZE}px; border-radius:50%;
                    background:conic-gradient(from -90deg, {accent} 0deg {progress_deg:.1f}deg, {track} {progress_deg:.1f}deg 180deg, transparent 180deg 360deg);
                    -webkit-mask:radial-gradient(farthest-side, transparent calc(50% - {RING_THICKNESS}px), #000 calc(50% - {RING_THICKNESS}px));
                    mask:radial-gradient(farthest-side, transparent calc(50% - {RING_THICKNESS}px), #000 calc(50% - {RING_THICKNESS}px));">
        </div>
        <div style="position:absolute; left:0; right:0; bottom:0; text-align:center;">
          <div style="font-size:1.5rem; font-weight:600; color:{ink}; line-height:1.1;">{display_value}{unit}</div>
          <div style="font-size:0.72rem; color:{muted_ink};">of {goal:,.0f}{unit} {label}</div>
        </div>
      </div>
    </div>
    """


def ring_theme(is_dark: bool) -> tuple[str, str, str]:
    """Shared ring color tokens: (track, ink, muted_ink)."""
    if is_dark:
        return "#383835", "#ffffff", "#c3c2b7"
    return "#e1e0d9", "#0b0b0b", "#52514e"


# Hides the hamburger menu, "Deploy" button, and the header's own visible
# bar - while keeping the header ELEMENT itself in the render tree (not
# display:none), because that's also where the ">>" button lives that
# re-expands the sidebar/page-nav after you collapse it. An earlier version
# of this hid header[data-testid="stHeader"] outright, which took that
# button down with it - display:none on an ancestor removes descendants
# from the render tree entirely, no per-element override can bring them
# back, so a collapsed sidebar had no way to reopen.
#
# The header's own background is opaque white by default (measured via
# Playwright: rgb(255, 255, 255), a solid ~60px strip across the top,
# unchanged in dark mode too - it never adapted to the app's theme) - that's
# the visible "bar" this hides now, by making the header transparent
# instead of removing it. The expand button still renders inside it (only
# appears when the sidebar's collapsed) and stays clickable - it just no
# longer sits on a visible colored strip. padding-top still clears the
# header's ~60px footprint so page content doesn't sit underneath it.
HIDE_MENU_STYLE = """
        <style>
        #MainMenu {visibility: hidden;}
        [data-testid="stAppDeployButton"] {display: none;}
        header[data-testid="stHeader"] {background: transparent; box-shadow: none;}
        [data-testid="stMainBlockContainer"] {padding-top: 4.5rem;}
        </style>
        """
