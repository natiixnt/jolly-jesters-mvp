import re
from urllib.parse import urlparse

EAN_PATTERN = re.compile(r'^\d{8,13}$')


def validate_ean(ean: str) -> str:
    """Validate and normalize EAN code. Returns cleaned EAN or raises ValueError."""
    cleaned = ean.strip() if ean else ''
    if not EAN_PATTERN.match(cleaned):
        raise ValueError(f"Nieprawidlowy kod EAN: {ean}")
    return cleaned


def sanitize_string(s: str, max_length: int = 255) -> str:
    """Remove control characters and limit length."""
    if not s:
        return s
    # Remove null bytes and control chars except newline/tab
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)
    return cleaned[:max_length]


def validate_proxy_url(url: str) -> str:
    """Validate proxy URL format."""
    url = url.strip()
    if not url:
        raise ValueError("Pusty URL proxy")
    # Must have scheme
    if '://' not in url:
        url = 'http://' + url
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"Brak hosta w URL proxy: {url}")
    if parsed.scheme not in ('http', 'https', 'socks4', 'socks5'):
        raise ValueError(f"Nieprawidlowy schemat proxy: {parsed.scheme}")
    if parsed.port and (parsed.port < 1 or parsed.port > 65535):
        raise ValueError(f"Nieprawidlowy port: {parsed.port}")
    return url
