from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Dict
from urllib.parse import urlparse

import anyio


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


ALLEGRO_MAX_CONCURRENCY = max(1, _env_int("ALLEGRO_MAX_CONCURRENCY", 1))
ALLEGRO_MIN_DELAY_SECONDS = max(0, _env_int("ALLEGRO_MIN_DELAY_SECONDS", 3))
ALLEGRO_MIN_DELAY_JITTER = max(0, _env_int("ALLEGRO_MIN_DELAY_JITTER", 2))
ALLEGRO_429_COOLDOWN_MINUTES = max(1, _env_int("ALLEGRO_429_COOLDOWN_MINUTES", 5))
ALLEGRO_429_COOLDOWN_MAX_MINUTES = max(1, _env_int("ALLEGRO_429_COOLDOWN_MAX_MINUTES", 30))


class RateLimited(Exception):
    def __init__(self, host: str, remaining_seconds: float):
        self.host = host
        self.remaining_seconds = remaining_seconds
        super().__init__(f"Rate limited for host {host}, wait {remaining_seconds:.1f}s")


@dataclass
class HostState:
    semaphore: asyncio.Semaphore
    last_request: float = 0.0
    cooldown_until: float = 0.0
    streak_429: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class HostRateLimiter:
    def __init__(self):
        self.hosts: Dict[str, HostState] = {}
        self.global_lock = asyncio.Lock()

    def _get_host(self, host: str) -> HostState:
        if host not in self.hosts:
            self.hosts[host] = HostState(semaphore=asyncio.Semaphore(ALLEGRO_MAX_CONCURRENCY))
        return self.hosts[host]

    async def _ensure_not_in_cooldown(self, state: HostState, host: str):
        now = time.monotonic()
        remaining = state.cooldown_until - now
        if remaining > 0:
            raise RateLimited(host, remaining)

    async def _sleep_if_needed(self, state: HostState):
        now = time.monotonic()
        wait = ALLEGRO_MIN_DELAY_SECONDS + random.uniform(0, ALLEGRO_MIN_DELAY_JITTER)
        delta = now - state.last_request
        if delta < wait:
            try:
                await asyncio.sleep(wait - delta)
            except RuntimeError:
                await anyio.sleep(wait - delta)
        state.last_request = time.monotonic()

    @asynccontextmanager
    async def throttle(self, url: str):
        parsed = urlparse(url)
        host = parsed.hostname or "unknown"
        state = self._get_host(host)

        await self._ensure_not_in_cooldown(state, host)
        await state.semaphore.acquire()
        try:
            async with state.lock:
                await self._ensure_not_in_cooldown(state, host)
                await self._sleep_if_needed(state)
            yield
        finally:
            state.semaphore.release()

    def register_429(self, host: str) -> float:
        state = self._get_host(host)
        state.streak_429 += 1
        base = ALLEGRO_429_COOLDOWN_MINUTES * (2 ** (state.streak_429 - 1))
        base = min(base, ALLEGRO_429_COOLDOWN_MAX_MINUTES)
        jitter = random.uniform(0.5, 1.5)
        minutes = base * jitter
        state.cooldown_until = time.monotonic() + minutes * 60
        return minutes

    def reset_429(self, host: str):
        state = self._get_host(host)
        state.streak_429 = 0
        state.cooldown_until = 0


rate_limiter = HostRateLimiter()


def host_from_url(url: str) -> str:
    return urlparse(url).hostname or "unknown"
