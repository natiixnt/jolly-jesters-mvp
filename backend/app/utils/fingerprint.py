from __future__ import annotations

import hashlib
import os
import random
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SeleniumFingerprint:
    preset_id: str
    user_agent: str
    accept_language: str
    lang: str
    viewport: Tuple[int, int]
    timezone: Optional[str]
    profile_dir: Optional[str]
    profile_mode: str
    rotated: bool
    ua_hash: str
    ua_version: Optional[str]
    ua_source: str


@dataclass(frozen=True)
class SeleniumProxyConfig:
    proxy_url: str
    proxy_id: str
    rotated: bool
    source: str


@dataclass(frozen=True)
class HttpHeaderPreset:
    preset_id: str
    headers: Dict[str, str]
    user_agent: str
    rotated: bool
    ua_hash: str
    ua_version: Optional[str]


@dataclass(frozen=True)
class _BasePreset:
    preset_id: str
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    accept_language: str
    intl_accept_languages: str
    lang: str
    viewport: Tuple[int, int]
    timezone: Optional[str]


_BASE_PRESETS: List[_BasePreset] = [
    _BasePreset(
        preset_id="win_pl_122",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        sec_ch_ua_platform='"Windows"',
        accept_language="pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
        intl_accept_languages="pl-PL,pl,en-US,en",
        lang="pl-PL",
        viewport=(1366, 768),
        timezone="Europe/Warsaw",
    ),
    _BasePreset(
        preset_id="win_de_123",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="123", "Not(A:Brand";v="24", "Google Chrome";v="123"',
        sec_ch_ua_platform='"Windows"',
        accept_language="de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        intl_accept_languages="de-DE,de,en-US,en",
        lang="de-DE",
        viewport=(1920, 1080),
        timezone="Europe/Berlin",
    ),
    _BasePreset(
        preset_id="mac_en_124",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_4) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="124", "Not(A:Brand";v="24", "Google Chrome";v="124"',
        sec_ch_ua_platform='"macOS"',
        accept_language="en-US,en;q=0.9",
        intl_accept_languages="en-US,en",
        lang="en-US",
        viewport=(1440, 900),
        timezone="America/New_York",
    ),
    _BasePreset(
        preset_id="win_en_125",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="125", "Not(A:Brand";v="24", "Google Chrome";v="125"',
        sec_ch_ua_platform='"Windows"',
        accept_language="en-US,en;q=0.9",
        intl_accept_languages="en-US,en",
        lang="en-US",
        viewport=(1536, 864),
        timezone="America/New_York",
    ),
]


def ua_hash(user_agent: Optional[str]) -> Optional[str]:
    if not user_agent:
        return None
    digest = hashlib.sha256(user_agent.encode("utf-8")).hexdigest()
    return digest[:10]


def ua_version(user_agent: Optional[str]) -> Optional[str]:
    if not user_agent:
        return None
    match = re.search(r"Chrome/([0-9]+)", user_agent)
    if not match:
        return None
    return f"Chrome/{match.group(1)}"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _rotation_bounds() -> Tuple[int, int]:
    min_val = max(1, _env_int("FINGERPRINT_ROTATION_EVERY_MIN", 2))
    max_val = max(min_val, _env_int("FINGERPRINT_ROTATION_EVERY_MAX", 3))
    return min_val, max_val


def _rotation_enabled() -> bool:
    return _env_bool("FINGERPRINT_ROTATION_ENABLED", True)


def _http_rotation_enabled() -> bool:
    return _env_bool("HTTP_HEADER_ROTATION_ENABLED", True)


def _seeded_random(name: str) -> random.Random:
    seed = os.getenv("FINGERPRINT_PRESET_SEED")
    if seed:
        return random.Random(f"{seed}:{name}")
    return random.Random()


def _jitter_viewport(viewport: Tuple[int, int], rng: random.Random) -> Tuple[int, int]:
    width, height = viewport
    jitter_w = rng.randint(-20, 20)
    jitter_h = rng.randint(-20, 20)
    width = max(800, width + jitter_w)
    height = max(600, height + jitter_h)
    return width, height


class _PresetRotator:
    def __init__(self, presets: List[_BasePreset], name: str, jitter: bool = False) -> None:
        self._presets = list(presets)
        self._name = name
        self._jitter = jitter
        self._lock = threading.Lock()
        self._rng: Optional[random.Random] = None
        self._current: Optional[_BasePreset] = None
        self._count = 0
        self._threshold = 0

    def _rng_instance(self) -> random.Random:
        if self._rng is None:
            self._rng = _seeded_random(self._name)
        return self._rng

    def _next_threshold(self, rng: random.Random) -> int:
        min_val, max_val = _rotation_bounds()
        if min_val == max_val:
            return min_val
        return rng.randint(min_val, max_val)

    def _choose_preset(self, rng: random.Random) -> _BasePreset:
        if not self._current or len(self._presets) <= 1:
            return rng.choice(self._presets)
        current_id = self._current.preset_id
        choices = [preset for preset in self._presets if preset.preset_id != current_id]
        return rng.choice(choices) if choices else rng.choice(self._presets)

    def _materialize(self, preset: _BasePreset, rng: random.Random) -> _BasePreset:
        if not self._jitter:
            return preset
        return _BasePreset(
            preset_id=preset.preset_id,
            user_agent=preset.user_agent,
            sec_ch_ua=preset.sec_ch_ua,
            sec_ch_ua_platform=preset.sec_ch_ua_platform,
            accept_language=preset.accept_language,
            intl_accept_languages=preset.intl_accept_languages,
            lang=preset.lang,
            viewport=_jitter_viewport(preset.viewport, rng),
            timezone=preset.timezone,
        )

    def next_preset(self) -> Tuple[_BasePreset, bool, int, int]:
        rng = self._rng_instance()
        with self._lock:
            rotated = False
            if self._current is None or self._count >= self._threshold:
                rotated = self._current is not None
                base = self._choose_preset(rng)
                self._current = self._materialize(base, rng)
                self._count = 0
                self._threshold = self._next_threshold(rng)
            self._count += 1
            return self._current, rotated, self._count, self._threshold


_SELENIUM_ROTATOR = _PresetRotator(_BASE_PRESETS, "selenium", jitter=True)
_HTTP_ROTATOR = _PresetRotator(_BASE_PRESETS, "http", jitter=False)
_PROXY_LOCK = threading.Lock()
_PROXY_STATE: Dict[str, Optional[str]] = {"proxy_url": None, "proxy_id": None, "source": None}
_PROXY_RNG: Optional[random.Random] = None


def _rotating_profile_dir(preset_id: str) -> str:
    base = Path(os.getenv("SELENIUM_TEMP_PROFILE_DIR", "/tmp")) / "selenium_profiles"
    base.mkdir(parents=True, exist_ok=True)
    profile_dir = base / preset_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    return str(profile_dir)


def _proxy_rotation_enabled() -> bool:
    return _env_bool("SELENIUM_PROXY_ROTATION_ENABLED", True)


def _proxy_list() -> List[str]:
    raw = (os.getenv("SELENIUM_PROXY_LIST") or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _proxy_rng() -> random.Random:
    global _PROXY_RNG
    if _PROXY_RNG is None:
        _PROXY_RNG = _seeded_random("selenium_proxy")
    return _PROXY_RNG


def _proxy_template(raw: str) -> Optional[str]:
    if "{session}" in raw:
        return "{session}"
    if "{sid}" in raw:
        return "{sid}"
    return None


def _new_session_id(rng: random.Random) -> str:
    return str(rng.randint(100000, 999999))


def _apply_proxy_template(raw: str, placeholder: str, session_id: str) -> str:
    return raw.replace(placeholder, session_id)


def get_selenium_proxy(fingerprint: Optional[SeleniumFingerprint]) -> Optional[SeleniumProxyConfig]:
    raw_proxy = (os.getenv("SELENIUM_PROXY") or "").strip()
    proxy_list = _proxy_list()
    rotation_enabled = _proxy_rotation_enabled()
    should_rotate = bool(rotation_enabled and fingerprint and fingerprint.rotated)
    rng = _proxy_rng()

    if proxy_list:
        with _PROXY_LOCK:
            current = _PROXY_STATE.get("proxy_url")
            if current not in proxy_list:
                current = None
            rotated = False
            if current is None or should_rotate:
                choices = proxy_list
                if current and len(proxy_list) > 1:
                    choices = [p for p in proxy_list if p != current] or proxy_list
                current = rng.choice(choices)
                rotated = _PROXY_STATE.get("proxy_url") is not None
                _PROXY_STATE["proxy_url"] = current
                _PROXY_STATE["proxy_id"] = f"list:{proxy_list.index(current) + 1}"
                _PROXY_STATE["source"] = "list"
            return SeleniumProxyConfig(
                proxy_url=current,
                proxy_id=_PROXY_STATE.get("proxy_id") or "list",
                rotated=rotated,
                source=_PROXY_STATE.get("source") or "list",
            )

    if raw_proxy:
        placeholder = _proxy_template(raw_proxy)
        if placeholder:
            with _PROXY_LOCK:
                current = _PROXY_STATE.get("proxy_url")
                rotated = False
                if current is None or should_rotate:
                    session_id = _new_session_id(rng)
                    current = _apply_proxy_template(raw_proxy, placeholder, session_id)
                    rotated = _PROXY_STATE.get("proxy_url") is not None
                    _PROXY_STATE["proxy_url"] = current
                    _PROXY_STATE["proxy_id"] = session_id
                    _PROXY_STATE["source"] = "session_template"
                return SeleniumProxyConfig(
                    proxy_url=current,
                    proxy_id=_PROXY_STATE.get("proxy_id") or "session",
                    rotated=rotated,
                    source=_PROXY_STATE.get("source") or "session_template",
                )
        return SeleniumProxyConfig(proxy_url=raw_proxy, proxy_id="static", rotated=False, source="static")

    return None


def get_selenium_fingerprint() -> Optional[SeleniumFingerprint]:
    if not _rotation_enabled():
        return None
    preset, rotated, _, _ = _SELENIUM_ROTATOR.next_preset()
    env_user_agent = (os.getenv("SELENIUM_USER_AGENT") or "").strip()
    if env_user_agent:
        user_agent = env_user_agent
        ua_source = "env"
    else:
        user_agent = preset.user_agent
        ua_source = "preset"
    profile_dir = None
    profile_mode = "persistent"
    if _env_bool("SELENIUM_FORCE_TEMP_PROFILE", False):
        profile_dir = _rotating_profile_dir(preset.preset_id)
        profile_mode = "rotating_temp"
    elif os.getenv("SELENIUM_USER_DATA_DIR") or os.getenv("SELENIUM_PROFILE_DIR"):
        profile_mode = "persistent"
    else:
        profile_mode = "default"
    return SeleniumFingerprint(
        preset_id=preset.preset_id,
        user_agent=user_agent,
        accept_language=preset.intl_accept_languages,
        lang=preset.lang,
        viewport=preset.viewport,
        timezone=preset.timezone,
        profile_dir=profile_dir,
        profile_mode=profile_mode,
        rotated=rotated,
        ua_hash=ua_hash(user_agent) or "unknown",
        ua_version=ua_version(user_agent),
        ua_source=ua_source,
    )


def _http_headers_for_preset(preset: _BasePreset) -> Dict[str, str]:
    return {
        "User-Agent": preset.user_agent,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": preset.accept_language,
        "Accept-Encoding": "gzip, deflate",
        "Upgrade-Insecure-Requests": "1",
        "Sec-CH-UA": preset.sec_ch_ua,
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": preset.sec_ch_ua_platform,
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
    }


def get_http_header_preset() -> Optional[HttpHeaderPreset]:
    if not _http_rotation_enabled():
        return None
    preset, rotated, _, _ = _HTTP_ROTATOR.next_preset()
    headers = _http_headers_for_preset(preset)
    return HttpHeaderPreset(
        preset_id=preset.preset_id,
        headers=headers,
        user_agent=preset.user_agent,
        rotated=rotated,
        ua_hash=ua_hash(preset.user_agent) or "unknown",
        ua_version=ua_version(preset.user_agent),
    )
