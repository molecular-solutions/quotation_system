"""Parse an uploaded .csv/.xlsx into rows of a TableSpec, validate, and (optionally) store."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import ImportLog
from app.services.tables import TableSpec, norm

MAX_ROWS = 5000


class ImportError_(ValueError):
    """The file itself cannot be read (not a row-level problem)."""


@dataclass
class RowResult:
    n: int  # row number as the user sees it in the sheet (header = 1)
    data: dict
    errors: dict[str, str] = field(default_factory=dict)
    action: str = ""  # 'create' | 'update' | ''


@dataclass
class Preview:
    filename: str
    columns: list[str]
    ignored_columns: list[str]
    rows: list[RowResult]

    @property
    def ok_count(self): return sum(1 for r in self.rows if not r.errors)
    @property
    def failed_count(self): return len(self.rows) - self.ok_count


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_table(filename: str, content: bytes, sheet_hint: str = "") -> list[list[str]]:
    name = filename.lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        text = content.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as e:  # corrupt / not a real xlsx
            raise ImportError_(f"cannot open this Excel file: {e}") from e
        ws = next((wb[s] for s in wb.sheetnames if norm(s) == norm(sheet_hint)), wb[wb.sheetnames[0]])
        return [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
    raise ImportError_("unsupported file type: upload a .csv or .xlsx file")


def preview(db: Session, spec: TableSpec, filename: str, content: bytes) -> Preview:
    grid = [r for r in read_table(filename, content, spec.name) if any(c for c in r)]
    if not grid:
        raise ImportError_("the file is empty")
    if len(grid) - 1 > MAX_ROWS:
        raise ImportError_(f"too many rows ({len(grid) - 1}); the limit is {MAX_ROWS} per upload")
    header, body = grid[0], grid[1:]
    mapping: dict[int, str] = {}
    ignored: list[str] = []
    for i, h in enumerate(header):
        col = spec.column_for_header(h) if h else None
        if col and col.key not in mapping.values():
            mapping[i] = col.key
        elif h:
            ignored.append(h)
    missing = [c.label for c in spec.columns if c.required and c.key not in mapping.values()]
    if missing:
        raise ImportError_("required column(s) not found in the header row: " + ", ".join(missing))

    rows, seen = [], {}
    for offset, raw_row in enumerate(body, start=2):
        raw = {mapping[i]: v for i, v in enumerate(raw_row) if i in mapping}
        clean, errors = spec.clean(db, raw)
        key = spec.key_of(clean)
        if key and key in seen and "_row" not in errors:
            errors["_row"] = f"duplicate of row {seen[key]}"
        seen.setdefault(key, offset)
        rows.append(RowResult(offset, clean, errors))
    return Preview(filename, [c.label for c in spec.columns if c.key in mapping.values()], ignored, rows)


def commit(db: Session, spec: TableSpec, prev: Preview) -> tuple[int, int]:
    ok = 0
    for r in prev.rows:
        if r.errors:
            continue
        spec.upsert(db, r.data)
        ok += 1
    db.add(ImportLog(table_name=spec.name, filename=prev.filename[:200], rows_ok=ok, rows_failed=prev.failed_count))
    db.commit()
    return ok, prev.failed_count
