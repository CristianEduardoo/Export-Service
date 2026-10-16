from datetime import datetime, timezone
from pathlib import Path
import json
import time

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

app = FastAPI(title="Source API Mock", version="1.0.0")
DATA_FILE = Path(__file__).parent / "data.json"


def load_items() -> list[dict]:
    return json.loads(DATA_FILE.read_text())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/orders")
def orders(
    updated_since: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    scenario: str | None = None,
):
    if scenario == "temporary_error":
        return JSONResponse(status_code=503, content={"detail": "temporary source failure"})
    if scenario == "slow":
        time.sleep(4)

    items = load_items()
    if updated_since is not None:
        items = [
            item for item in items
            if datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")) >= updated_since
        ]

    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    return {
        "items": page_items,
        "page": page,
        "page_size": page_size,
        "total": len(items),
        "has_next": start + page_size < len(items),
    }
