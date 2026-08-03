from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from corp_os.models.audit import AuditLog


def write_audit(
    db: Session,
    *,
    actor: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    detail: dict | str | None = None,
    run_id: str | None = None,
) -> AuditLog:
    if isinstance(detail, dict):
        detail_text = json.dumps(detail, ensure_ascii=False)
    else:
        detail_text = detail
    row = AuditLog(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail_text,
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row
