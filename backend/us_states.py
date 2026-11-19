"""U.S. state names: a page or sentence about one state is about the U.S. market (the U.S. section may use it, naming the
state). Georgia and Washington are left out on their own, since each also names a country, a city or a person; "Washington
State" is kept."""
import re

US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida", "Hawaii",
    "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington State", "West Virginia", "Wisconsin", "Wyoming",
)

STATE_RE = re.compile(r"(?<![A-Za-z])(?:" + "|".join(re.escape(s) for s in sorted(US_STATES, key=len, reverse=True)) + r")(?![A-Za-z])")

# The states the U.S. section's extra queries ask about when U.S.-wide news is thin: the largest state economies first.
QUERY_STATE_GROUPS = (
    ("Texas", "California", "Florida", '"New York"'),
    ("Illinois", "Ohio", "Pennsylvania", "Georgia", '"North Carolina"'),
)
