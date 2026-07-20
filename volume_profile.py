"""Removed legacy volume-profile engine.

Use ``modules.volume_analysis`` for profile calculations and
``modules.vrvp_levels`` for structural trade levels. The old Streamlit client
handles this ImportError and disables its non-production compatibility panel.
"""

raise ImportError(
    "Legacy volume_profile was removed; use modules.volume_analysis and "
    "modules.vrvp_levels"
)
