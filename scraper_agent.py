"""
Lokalny agent do zarządzania liczbą okien scrapera.
- Odczytuje z Redis klucz "scraper:desired_instances" (domyslnie 1, max z SCRAPER_AGENT_MAX_INSTANCES, domyslnie 5).
- Uruchamia/zamyka procesy `run_scraper_local.bat`, każde to jedno widoczne okno.
"""

import os
import subprocess
import time
import signal

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CHECK_INTERVAL = int(os.getenv("AGENT_INTERVAL_SECONDS", "5"))


def _max_instances() -> int:
    try:
        return max(1, int(os.getenv("SCRAPER_AGENT_MAX_INSTANCES", "5")))
    except Exception:
        return 5


MAX_INSTANCES = _max_instances()


def clamp_instances(val: int) -> int:
    return max(1, min(MAX_INSTANCES, val))


def read_desired(r: redis.Redis) -> int:
    try:
        raw = r.get("scraper:desired_instances")
        if raw is None:
            return 1
        return clamp_instances(int(raw))
    except Exception:
        return 1


def spawn_worker() -> subprocess.Popen:
    # Uruchamiamy run_scraper_local.bat w nowym oknie
    creationflags = 0
    if hasattr(subprocess, "CREATE_NEW_CONSOLE"):
        creationflags = subprocess.CREATE_NEW_CONSOLE
    return subprocess.Popen(["cmd", "/c", "run_scraper_local.bat"], creationflags=creationflags)


def main():
    r = redis.Redis.from_url(REDIS_URL)
    procs: list[subprocess.Popen] = []

    while True:
        desired = read_desired(r)

        # Oczyść listę z martwych procesów
        alive = []
        for p in procs:
            if p.poll() is None:
                alive.append(p)
        procs = alive

        # Dołóż brakujące
        while len(procs) < desired:
            p = spawn_worker()
            procs.append(p)
            time.sleep(1)

        # Usuń nadmiarowe
        while len(procs) > desired:
            p = procs.pop()
            try:
                p.terminate()
                time.sleep(1)
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
