import re


def _ean13_checksum(ean12: str) -> int:
    digits = [int(ch) for ch in ean12]
    s = sum(digits[::2]) + 3 * sum(digits[1::2])
    return (10 - (s % 10)) % 10


def is_valid_ean13(ean: str) -> bool:
    if not re.fullmatch(r"\d{13}", ean):
        return False
    if ean == "0000000000000":
        return False
    return _ean13_checksum(ean[:-1]) == int(ean[-1])
