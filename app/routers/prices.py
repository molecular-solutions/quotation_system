"""Prices by number of users (admin only): the Prices page, upload and export."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Product
from app.services import prices as svc

router = APIRouter(prefix="/api/prices", dependencies=[Depends(require_admin)])
MAX_UPLOAD = 5 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def product_or_404(db: Session, product_id: int) -> Product:
    p = db.get(Product, product_id)
    if p is None or not svc.priced(p):
        raise HTTPException(404, "no such product")
    return p


@router.get("")
def list_products(db: Session = Depends(get_db)):
    return {"max_users": svc.MAX_USERS, "products": svc.summary(db)}


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db)):
    return Response(svc.export_csv(db), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="prices.csv"'})


@router.get("/export.xlsx")
def export_xlsx(db: Session = Depends(get_db)):
    return Response(svc.export_xlsx(db), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="prices.xlsx"'})


@router.post("/upload")
async def upload(file: UploadFile = File(...), commit: bool = False, db: Session = Depends(get_db)):
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "file is larger than 5 MB")
    try:
        prev = svc.preview(db, file.filename or "upload", content)
    except svc.PriceError as e:
        raise HTTPException(422, {"message": str(e)})
    result = {"filename": prev.filename, "total": len(prev.rows), "ok_count": len(prev.good),
              "failed_count": len(prev.bad), "products": len({r.product_id for r in prev.good}),
              "problems": [{"n": r.n, "product": r.product, "tier": r.tier, "users": r.users, "error": "; ".join(r.errors)}
                           for r in prev.bad[:200]],
              "committed": False}
    if commit:
        ok, failed = svc.commit(db, prev)
        result.update(committed=True, imported=ok, skipped=failed)
    return result


@router.get("/{product_id}")
def read(product_id: int, db: Session = Depends(get_db)):
    return svc.detail(product_or_404(db, product_id))


@router.put("/{product_id}/{tier}")
def save(product_id: int, tier: str, body: dict, db: Session = Depends(get_db)):
    """Replace one bundle's price list: {"prices": {"1": 42000, "2": 78000, "3": ""}} (blank removes that count);
    optional "prices3" is the same for the 3-year prices (left alone when absent)."""
    p = product_or_404(db, product_id)
    raw = body.get("prices")
    if not isinstance(raw, dict):
        raise HTTPException(422, {"message": "prices must be an object of users → price"})
    try:
        raw3 = body.get("prices3")
        if raw3 is not None and not isinstance(raw3, dict):
            raise HTTPException(422, {"message": "prices3 must be an object of users → price"})
        errors = svc.set_tier(db, p, tier, raw, raw3)
    except svc.PriceError as e:
        raise HTTPException(422, {"message": str(e)})
    if errors:
        db.rollback()
        raise HTTPException(422, {"errors": errors, "message": "fix the highlighted prices"})
    db.commit()
    db.refresh(p)
    return svc.detail(p)
