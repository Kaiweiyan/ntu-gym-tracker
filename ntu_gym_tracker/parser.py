"""Parse occupancy numbers out of the venue page HTML.

Page structure: a `.CMCList` wraps one `.CMCItem` per venue. (The `.CMCList`
class is reused in empty blocks elsewhere on the page, so we anchor on
`.CMCItem` instead.)::

    <div class="CMCList">
        <div class="CMCItem">
            <div class="IT">健身中心</div>
            <div class="IC">
                <div class="ICI"><span>87</span> 現在人數 </div>
                <div class="ICI"><span>80</span> 最適人數 </div>
                <div class="ICI"><span>161</span> 最大乘載人數 </div>
            </div>
        </div>
        <div class="CMCItem"> ... 室內游泳池 ... </div>
    </div>

We locate each `.CMCItem`, read its `.IT` name, then read the three
`.ICI > span` numbers in order: current / optimal / max capacity.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .config import VENUE_ID_BY_NAME
from .models import Observation


def _slugify(name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in name.strip().lower()).strip("-")


def _to_int(text: str) -> int | None:
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def parse_observations(html: str, scraped_at: str) -> list[Observation]:
    """Extract one Observation per venue block found in `html`.

    Raises ValueError if no venue blocks are found at all (signals the page
    layout changed and the scraper needs attention).
    """
    soup = BeautifulSoup(html, "html.parser")
    # Only items that actually carry a name + numbers; skips empty .CMCItem
    # placeholders rendered elsewhere on the page.
    blocks = [
        item
        for item in soup.select("div.CMCItem")
        if item.select_one(".IT") and item.select(".IC .ICI span")
    ]
    if not blocks:
        raise ValueError("no populated .CMCItem venue blocks found — page layout may have changed")

    observations: list[Observation] = []
    for block in blocks:
        name_el = block.select_one(".IT")
        venue_name = name_el.get_text(strip=True) if name_el else ""
        venue_id = VENUE_ID_BY_NAME.get(venue_name, _slugify(venue_name) or "unknown")

        spans = block.select(".IC .ICI span")
        numbers = [_to_int(s.get_text()) for s in spans]
        # Pad to 3 so a missing field becomes None rather than an IndexError.
        numbers += [None] * (3 - len(numbers))
        current, optimal, max_capacity = numbers[0], numbers[1], numbers[2]

        status = "ok" if current is not None else "parse_error"
        observations.append(
            Observation(
                venue_id=venue_id,
                venue_name=venue_name,
                scraped_at=scraped_at,
                current_count=current,
                optimal_count=optimal,
                max_capacity=max_capacity,
                source_status=status,
            )
        )
    return observations
