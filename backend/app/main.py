import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import get_db
from app.utils.local_scraper_client import check_local_scraper_health

logger = logging.getLogger(__name__)

app = FastAPI(title="Jolly Jesters MVP", version="1.0.0")

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
def _log_local_scraper_config() -> None:
    logger.info(
        "LOCAL_SCRAPER_CONFIG enabled=%s url=%s",
        settings.LOCAL_SCRAPER_ENABLED,
        settings.LOCAL_SCRAPER_URL,
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def healthcheck(db: Session = Depends(get_db)):
    db.execute("SELECT 1")
    local_scraper = check_local_scraper_health()
    return {"status": "ok", "local_scraper": local_scraper}
