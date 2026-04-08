import logging
from datetime import datetime, timezone

audit_logger = logging.getLogger("audit")


def log_event(action: str, user_id: str = None, tenant_id: str = None,
              ip: str = None, details: dict = None):
    """Log a security-relevant event."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "ip": ip,
        "details": details or {},
    }
    audit_logger.info("AUDIT: %s user=%s tenant=%s ip=%s details=%s",
                      action, user_id, tenant_id, ip, details)
    return event
