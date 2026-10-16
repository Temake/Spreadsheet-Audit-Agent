"""Safe, read-only loading of CSV and XLSX files with size and volume limits.

The original file is only ever opened for reading. Cell contents are treated as data:
formulas are read as their cached values and macros are rejected outright.
"""

import csv
import io
import logging
import zipfile
from pathlib import Path

import openpyxl

from sheet_audit.config import Settings
from sheet_audit.gate import HEADER_SCAN_ROWS, SheetTable, prepare_sheet

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
MB = 1024 * 1024
MAX_COMPRESSION_RATIO = 100
# read_only openpyxl can report millions of formatted-but-empty rows; stop after this many in a row.
MAX_TRAILING_EMPTY_ROWS = 1000


class InputError(Exception):
    """The file cannot be loaded. The message is safe to show to the user."""


def load_file(path: str | Path, settings: Settings) -> list[SheetTable]:
    path = Path(path)
    if not path.is_file():
        raise InputError(f"file not found: {path.name}")
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise InputError(f"unsupported file type {ext!r}; expected one of {sorted(ALLOWED_EXTENSIONS)}")
    size = path.stat().st_size
    if size > settings.max_file_mb * MB:
        raise InputError(f"file is {size / MB:.1f} MB; the limit is {settings.max_file_mb} MB")
    if size == 0:
        raise InputError("file is empty")

    raw = _read_xlsx(path, settings) if ext == ".xlsx" else {path.stem: _read_csv(path, settings)}
    log.info("loaded %s: %d sheet(s), %d bytes", ext, len(raw), size)
    return [prepare_sheet(name, rows, settings) for name, rows in raw.items()]


def _row_limit(settings: Settings) -> int:
    return settings.max_rows + HEADER_SCAN_ROWS


def _trim(row: tuple | list) -> list:
    row = list(row)
    while row and row[-1] in (None, ""):
        row.pop()
    return row


def _read_csv(path: Path, settings: Settings) -> list[list]:
    data = path.read_bytes()
    if b"\x00" in data:
        raise InputError("file contains binary data; it does not look like a CSV")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i >= _row_limit(settings):
            raise InputError(f"file has more than {settings.max_rows} rows")
        row = _trim(row)
        if len(row) > settings.max_columns:
            raise InputError(f"file has more than {settings.max_columns} columns")
        rows.append(row)
    return rows


def _check_xlsx_container(path: Path, settings: Settings) -> None:
    """Reject non-zip files, zip bombs, and macro-enabled workbooks before parsing."""
    if not zipfile.is_zipfile(path):
        raise InputError("file is not a valid .xlsx workbook")
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        names = {i.filename for i in infos}
        if "[Content_Types].xml" not in names:
            raise InputError("file is not a valid .xlsx workbook")
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise InputError("macro-enabled workbooks are not supported")
        total = sum(i.file_size for i in infos)
        if total > settings.max_uncompressed_mb * MB:
            raise InputError(f"workbook expands to more than {settings.max_uncompressed_mb} MB")
        for i in infos:
            if i.compress_size and i.file_size > MB and i.file_size / i.compress_size > MAX_COMPRESSION_RATIO:
                raise InputError("workbook has a suspicious compression ratio")


def _read_xlsx(path: Path, settings: Settings) -> dict[str, list[list]]:
    _check_xlsx_container(path, settings)
    try:
        # data_only=True: read cached formula results, never formula text.
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for corrupt files
        raise InputError(f"could not open workbook ({type(exc).__name__})") from exc
    try:
        if len(wb.worksheets) > settings.max_sheets:
            raise InputError(f"workbook has more than {settings.max_sheets} sheets")
        sheets = {}
        for ws in wb.worksheets:
            rows, empty_run = [], 0
            for row in ws.iter_rows(values_only=True):
                row = _trim(row)
                empty_run = 0 if row else empty_run + 1
                if empty_run > MAX_TRAILING_EMPTY_ROWS:
                    break
                if len(rows) >= _row_limit(settings):
                    raise InputError(f"sheet {ws.title!r} has more than {settings.max_rows} rows")
                if len(row) > settings.max_columns:
                    raise InputError(f"sheet {ws.title!r} has more than {settings.max_columns} columns")
                rows.append(row)
            sheets[ws.title] = rows
        return sheets
    finally:
        wb.close()
