"""Data dashboard API: manual grid + csv/xlsx upload for products, customers, packages."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import ImportLog
from app.services import importer
from app.services.tables import SPECS, CATALOG_LABELS

router = APIRouter(prefix="/api/data", dependencies=[Depends(require_admin)])
MAX_UPLOAD = 5 * 1024 * 1024


def spec_or_404(table: str):
    if table not in SPECS:
        raise HTTPException(404, f"unknown table '{table}'")
    return SPECS[table]


def _json_row(row: dict) -> dict:
    return {k: (float(v) if hasattr(v, "quantize") else v) for k, v in row.items()}


@router.get("/tables")
def tables():
    return [{"name": s.name, "title": s.title,
             "columns": [{"key": c.key, "label": c.label, "required": c.required, "kind": c.kind,
                          "choices": list(c.choices)} for c in s.columns]} for s in SPECS.values()]


@router.get("/history")
def history(db: Session = Depends(get_db)):
    rows = db.scalars(select(ImportLog).order_by(ImportLog.id.desc()).limit(30))
    return [{"table": r.table_name, "filename": r.filename, "ok": r.rows_ok, "failed": r.rows_failed,
             "at": r.at.isoformat(sep=" ", timespec="minutes")} for r in rows]


@router.get("/{table}")
def list_rows(table: str, db: Session = Depends(get_db)):
    return spec_or_404(table).rows(db)


def _save(db: Session, spec, body: dict, row_id: int | None):
    clean, errors = spec.clean(db, body, row_id, is_new=row_id is None)
    if errors:
        raise HTTPException(422, {"errors": errors})
    new_id = spec.upsert(db, clean, row_id)
    db.commit()
    return next((r for r in spec.rows(db) if r["id"] == new_id), None)


@router.post("/{table}")
def create_row(table: str, body: dict, db: Session = Depends(get_db)):
    return _save(db, spec_or_404(table), body, None)  # the grid never silently overwrites an existing row


@router.put("/{table}/{row_id}")
def update_row(table: str, row_id: int, body: dict, db: Session = Depends(get_db)):
    return _save(db, spec_or_404(table), body, row_id)


@router.delete("/{table}/{row_id}")
def delete_row(table: str, row_id: int, db: Session = Depends(get_db)):
    spec_or_404(table).delete(db, row_id)
    db.commit()
    return {"ok": True}


@router.post("/{table}/upload")
async def upload(table: str, file: UploadFile = File(...), commit: bool = False, db: Session = Depends(get_db)):
    spec = spec_or_404(table)
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "file is larger than 5 MB")
    try:
        prev = importer.preview(db, spec, file.filename or "upload", content)
    except importer.ImportError_ as e:
        raise HTTPException(422, {"message": str(e)})
    result = {"filename": prev.filename, "columns": prev.columns, "ignored_columns": prev.ignored_columns,
              "ok_count": prev.ok_count, "failed_count": prev.failed_count,
              "rows": [{"n": r.n, "data": _json_row(r.data), "errors": r.errors} for r in prev.rows[:500]],
              "committed": False}
    if commit:
        ok, failed = importer.commit(db, spec, prev)
        result.update(committed=True, imported=ok, skipped=failed)
    return result


@router.get("/{table}/template.csv")
def template(table: str):
    spec = spec_or_404(table)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c.label for c in spec.columns])
    w.writerow([c.example for c in spec.columns])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{table}_template.csv"'})


@router.get("/{table}/export.{fmt}")
def export(table: str, fmt: str, db: Session = Depends(get_db)):
    spec = spec_or_404(table)
    rows = spec.rows(db)
    header = [c.label for c in spec.columns]

    def cells(r):
        return ["Y" if (c.kind == "bool" and r.get(c.key)) else "N" if c.kind == "bool" else r.get(c.key, "")
                for c in spec.columns]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(cells(r) for r in rows)
        return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})
    if fmt == "xlsx":
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = table
        ws.append(header)
        for r in rows:
            ws.append(cells(r))
        out = io.BytesIO()
        wb.save(out)
        return Response(out.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{table}.xlsx"'})
    raise HTTPException(404, "format must be csv or xlsx")
