from __future__ import annotations

from app.collectors.acba_bank import SOURCE_NAME as ACBA_BANK_SOURCE_NAME
from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.collectors.vtb_am import SOURCE_NAME as VTB_AM_SOURCE_NAME

# Display order for bank rate listings — not alphabetical, just a stable,
# readable order shared by the bot's /banks command and the dashboard's
# GET /banks route, so they can never silently disagree.
BANK_SOURCES: tuple[str, str, str, str] = (
    AMERIABANK_SOURCE_NAME,
    EVOCABANK_SOURCE_NAME,
    ACBA_BANK_SOURCE_NAME,
    VTB_AM_SOURCE_NAME,
)
