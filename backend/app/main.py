import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.v1.router import api_router
from app.db.session import get_db
from app.core.config import settings
from app.utils.local_scraper_client import check_local_scraper_health

app = FastAPI(title="Jolly Jesters MVP", version="1.0.0")
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
async def log_local_scraper_health():
    if not (settings.LOCAL_SCRAPER_ENABLED and settings.LOCAL_SCRAPER_URL):
        return
    try:
        payload = check_local_scraper_health(timeout=1.0)
        logger.info("Local scraper health ok: %s", payload)
    except Exception as exc:  # pragma: no cover - best effort log
        logger.warning("Local scraper health check failed (startup): %s", exc)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def healthcheck(db: Session = Depends(get_db)):
    db.execute("SELECT 1")
    return {"status": "ok"}
