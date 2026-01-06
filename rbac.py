"""
Role-based access control helpers for the fraternity portal.
"""
from datetime import datetime
from functools import wraps

from flask import abort
from flask_login import current_user

from models import AuditLog, Committee, Event, Role, db, user_roles

ROLE_BROTHER = "brother"
ROLE_CHAIR_BROTHERHOOD = "chair_brotherhood"
ROLE_CHAIR_SOCIAL = "chair_social"
ROLE_CHAIR_RECRUITMENT = "chair_recruitment"
ROLE_TREASURER = "treasurer"
ROLE_VICE_PRESIDENT = "vice_president"
ROLE_PRESIDENT = "president"
ROLE_ADMIN = "admin"

COMMITTEE_ROLE_MAP = {
    "brotherhood": ROLE_CHAIR_BROTHERHOOD,
    "social": ROLE_CHAIR_SOCIAL,
    "recruitment": ROLE_CHAIR_RECRUITMENT,
}

EXECUTIVE_ROLES = {ROLE_TREASURER, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT, ROLE_ADMIN}
CHAIR_CATEGORY_MAP = {
    ROLE_CHAIR_BROTHERHOOD: "Brotherhood",
    ROLE_CHAIR_SOCIAL: "Social",
    ROLE_CHAIR_RECRUITMENT: "Recruitment",
}

CHAIR_PERMISSION_NAMES = {
    "create_events",
    "edit_own_events",
    "view_own_events",
    "create_spending_plans",
    "edit_own_spending_plans",
    "view_own_spending_plans",
    "manage_committee_budget",
    "manage_committee_events",
}

EXECUTIVE_PERMISSION_NAMES = {
    "manage_master_budget",
    "manage_committee_allocations",
    "manage_dues",
    "manage_roles",
    "manage_semesters",
    "manage_events",
}


def has_role(user, role_name):
    """Check if a user has an active role."""
    if not user or not user.is_authenticated:
        return False
    return user.has_role(role_name)


def has_any_role(user, *role_names):
    """Check if a user has any of the provided roles."""
    return any(has_role(user, role_name) for role_name in role_names)


def role_required(*role_names):
    """Decorator to require at least one of the provided roles."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not has_any_role(current_user, *role_names):
                abort(403)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def has_permission(user, permission_name):
    """Compatibility permission check for legacy routes."""
    if has_any_role(user, *EXECUTIVE_ROLES):
        return True
    if permission_name in CHAIR_PERMISSION_NAMES:
        return has_any_role(user, ROLE_CHAIR_BROTHERHOOD, ROLE_CHAIR_SOCIAL, ROLE_CHAIR_RECRUITMENT)
    if permission_name in EXECUTIVE_PERMISSION_NAMES:
        return has_any_role(user, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT, ROLE_TREASURER, ROLE_ADMIN)
    return False


def permission_required(*permission_names):
    """Decorator to require any of the provided permissions."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not any(has_permission(current_user, name) for name in permission_names):
                abort(403)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def get_primary_managed_category(user):
    """Return the first committee category managed by this user."""
    for role_name, category in CHAIR_CATEGORY_MAP.items():
        if has_role(user, role_name):
            return category
    return None


def can_manage_committee(user, committee_name):
    """Return True if user can manage a committee budget or events."""
    if has_any_role(user, *EXECUTIVE_ROLES):
        return True
    required_role = COMMITTEE_ROLE_MAP.get(committee_name)
    return required_role and has_role(user, required_role)


def can_edit_event(user, event):
    """Return True if user can edit/delete an event."""
    if has_any_role(user, *EXECUTIVE_ROLES):
        return True
    if event.created_by_user_id == user.id or event.created_by == user.id:
        return True
    if event.committee_id:
        committee = Committee.query.get(event.committee_id)
        if committee and can_manage_committee(user, committee.name.lower()):
            return True
    return False


def log_action(actor_user_id, action, target_type, target_id=None, details=None):
    """Create an audit log entry."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details_json=details,
        created_at=datetime.utcnow()
    )
    db.session.add(entry)


def grant_role(user, role_name, actor_user_id=None):
    """Grant a role to a user, recording an audit log entry."""
    role = Role.query.filter_by(name=role_name).first()
    if not role:
        return False
    existing = db.session.execute(
        user_roles.select().where(
            user_roles.c.user_id == user.id,
            user_roles.c.role_id == role.id,
            user_roles.c.revoked_at.is_(None),
        )
    ).first()
    if existing:
        return False
    db.session.execute(
        user_roles.insert().values(
            user_id=user.id,
            role_id=role.id,
            granted_by_user_id=actor_user_id,
            granted_at=datetime.utcnow(),
            revoked_at=None,
        )
    )
    if actor_user_id:
        log_action(
            actor_user_id,
            "ROLE_GRANTED",
            "user",
            user.id,
            {"role": role_name},
        )
    db.session.commit()
    return True


def revoke_role(user, role_name, actor_user_id=None):
    """Revoke an active role from a user, recording an audit log entry."""
    role = Role.query.filter_by(name=role_name).first()
    if not role:
        return False
    result = db.session.execute(
        user_roles.update()
        .where(
            user_roles.c.user_id == user.id,
            user_roles.c.role_id == role.id,
            user_roles.c.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.utcnow())
    )
    if result.rowcount == 0:
        return False
    if actor_user_id:
        log_action(
            actor_user_id,
            "ROLE_REVOKED",
            "user",
            user.id,
            {"role": role_name},
        )
    db.session.commit()
    return True
