"""Working out where to write when the target is a Drive folder, not a sheet.

Pointing the app at one fixed spreadsheet means editing Settings whenever the
month rolls over. Pointing it at a folder instead lets it follow the structure
that already exists in Drive:

    2026 Call Logs/                 <- the folder that is configured, once
      01.) Jan 2026 Call logs/
      ...
      08.) August 2026 Call Logs/   <- chosen because it is August today
        Week 06-10 Aug/             <- chosen: most recently changed
          Mon  Tue  Wed  Thu        <- chosen: newest tab, the rightmost one

Each step is resolved fresh on every refresh, and the trail it took is recorded
so the app can show where the rows are about to go instead of writing somewhere
the user cannot see. Resolution happens once per run, in plan(), and the result
is stored on the plan - so a preview and the write that follows it can never
land on two different sheets.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from services.errors import ConfigError, NotFoundError
from services.google_drive_service import DriveItem, GoogleDriveService
from services.google_sheets_service import GoogleSheetsService
from utils.logger import get_logger

log = get_logger("target")

MONTH_NAMES = ["january", "february", "march", "april", "may", "june",
               "july", "august", "september", "october", "november", "december"]

# Some months are abbreviated in the folder names ("Apr", "Mar", "Sept"), so the
# match has to allow a shortened form - but only as a whole word, or "mar" would
# match "March" and "Marketing" alike.
_EXTRA_FORMS = {"september": ["sept", "sep"]}


@dataclass
class ResolvedTarget:
    """Where this refresh will write, and how that was decided."""
    spreadsheet_id: str
    spreadsheet_name: str
    worksheet: str
    folder_id: str = ""
    folder_name: str = ""
    trail: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return " / ".join(self.trail) if self.trail else self.spreadsheet_name


# --------------------------------------------------------------------------- #
# The individual choices, kept free of any API so they can be tested directly
# --------------------------------------------------------------------------- #

def month_forms(month: int) -> list[str]:
    """The spellings of a month that may appear in a folder name."""
    name = MONTH_NAMES[month - 1]
    return [name, *_EXTRA_FORMS.get(name, []), name[:3]]


def _mentions_month(name: str, month: int) -> bool:
    low = name.lower()
    full = MONTH_NAMES[month - 1]
    if full in low:
        return True
    # Anything shorter has to stand as its own word: "08.) Aug 2026" yes,
    # "Marketing" no.
    return any(re.search(rf"(?<![a-z]){re.escape(form)}(?![a-z])", low)
               for form in month_forms(month)[1:])


def pick_month_folder(folders: list[DriveItem], today: date) -> DriveItem | None:
    """The subfolder for the current month, or None if nobody has made it.

    A folder naming the year as well wins over one that does not, so a
    "08.) August 2025" left over from last year cannot be picked while
    "08.) August 2026" exists. Remaining ties go to the most recently changed.
    """
    named = [f for f in folders if f.is_folder and _mentions_month(f.name, today.month)]
    if not named:
        return None
    year = str(today.year)
    with_year = [f for f in named if year in f.name]
    return max(with_year or named, key=lambda f: (f.modified or "", f.name))


def pick_newest_sheet(sheets: list[DriveItem]) -> DriveItem | None:
    """The spreadsheet changed most recently, which is the one being worked in."""
    real = [s for s in sheets if s.is_spreadsheet]
    if not real:
        return None
    return max(real, key=lambda s: (s.modified or "", s.name))


def newest_tab(titles: list[str]) -> str:
    """The newest tab, which is the rightmost one.

    The Sheets API reports no creation time for a tab, and returns them in the
    order they appear in the workbook. A tab added to a workbook goes on the end,
    so the last title is the newest - unless someone has dragged the tabs into a
    different order, which no API can tell us about.
    """
    return titles[-1] if titles else ""


# --------------------------------------------------------------------------- #
# The resolver
# --------------------------------------------------------------------------- #

class TargetResolver:
    def __init__(self, drive: GoogleDriveService, sheets: GoogleSheetsService) -> None:
        self.drive = drive
        self.sheets = sheets

    def resolve(self, folder_id: str, *, today: date | None = None) -> ResolvedTarget:
        if not folder_id:
            raise ConfigError("No Google Drive folder is chosen yet. Pick one in Settings.",
                              "empty folder_id")
        today = today or date.today()

        root = self._folder_name(folder_id)
        listing = self.drive.list_folder(folder_id)
        trail = [root]

        # 1. Down into this month's folder - unless the chosen folder holds no
        #    subfolders at all, in which case it is already the month folder.
        target_folder_id, target_folder_name = folder_id, root
        if listing.folders:
            month = pick_month_folder(listing.folders, today)
            if month is None:
                raise NotFoundError(
                    f'No folder for {MONTH_NAMES[today.month - 1].title()} {today.year} was found '
                    f'inside "{root}". Create this month\'s folder in Google Drive - naming it the '
                    "way the others are named - then press Refresh again.",
                    f"no subfolder of {folder_id} names month {today.month}")
            target_folder_id, target_folder_name = month.id, month.name
            listing = self.drive.list_folder(month.id)
            trail.append(month.name)

        # 2. The spreadsheet inside it that was changed most recently.
        chosen = pick_newest_sheet(listing.sheets)
        if chosen is None:
            raise NotFoundError(
                f'The folder "{target_folder_name}" holds no Google Sheets, so there is nothing to '
                "update. Add the sheet for this month, or choose a different folder in Settings.",
                f"no spreadsheet in {target_folder_id}")
        trail.append(chosen.name)

        # 3. The newest tab in it.
        titles = [ws.title for ws in self.sheets.worksheets(chosen.id)]
        tab = newest_tab(titles)
        if not tab:
            raise NotFoundError(
                f'The spreadsheet "{chosen.name}" has no tabs to write to.',
                f"no worksheets in {chosen.id}")
        trail.append(tab)

        log.info("Target resolved: %s", " / ".join(trail))
        return ResolvedTarget(spreadsheet_id=chosen.id, spreadsheet_name=chosen.name,
                              worksheet=tab, folder_id=target_folder_id,
                              folder_name=target_folder_name, trail=trail)

    def _folder_name(self, folder_id: str) -> str:
        try:
            return self.drive.get(folder_id).name
        except Exception:                       # a name is a nicety, not worth failing over
            return "the chosen folder"
