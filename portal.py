"""
Portal blueprint implementing the new fraternity management spec.
"""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models import (
    AuditLog,
    Committee,
    CommitteeBudgetAllocation,
    CommitteeTransaction,
    DuesCharge,
    DuesPayment,
    DuesPaymentAllocation,
    Event,
    GoogleEventLink,
    MasterLedger,
    Semester,
    User,
    db,
)
from rbac import (
    ROLE_CHAIR_BROTHERHOOD,
    ROLE_CHAIR_RECRUITMENT,
    ROLE_CHAIR_SOCIAL,
    ROLE_PRESIDENT,
    ROLE_TREASURER,
    ROLE_VICE_PRESIDENT,
    can_edit_event,
    can_manage_committee,
    grant_role,
    has_any_role,
    has_role,
    log_action,
    revoke_role,
)

portal_bp = Blueprint("portal", __name__, url_prefix="/portal")

COMMITTEE_NAMES = ["Brotherhood", "Social", "Recruitment"]
ROLE_TABS = {
    "committee_brotherhood": ROLE_CHAIR_BROTHERHOOD,
    "committee_social": ROLE_CHAIR_SOCIAL,
    "committee_recruitment": ROLE_CHAIR_RECRUITMENT,
}


def get_current_semester():
    return Semester.query.filter_by(is_current=True).first()


def build_sidebar_tabs():
    tabs = [
        {"name": "Dashboard", "endpoint": "portal.dashboard"},
        {"name": "Calendar", "endpoint": "portal.calendar"},
        {"name": "My Dues", "endpoint": "portal.dues"},
    ]
    if has_role(current_user, ROLE_CHAIR_BROTHERHOOD):
        tabs.append({"name": "Brotherhood", "endpoint": "portal.committee", "args": {"committee": "Brotherhood"}})
    if has_role(current_user, ROLE_CHAIR_SOCIAL):
        tabs.append({"name": "Social", "endpoint": "portal.committee", "args": {"committee": "Social"}})
    if has_role(current_user, ROLE_CHAIR_RECRUITMENT):
        tabs.append({"name": "Recruitment", "endpoint": "portal.committee", "args": {"committee": "Recruitment"}})
    if has_any_role(current_user, ROLE_TREASURER, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        tabs.append({"name": "Treasurer", "endpoint": "portal.treasurer"})
    if has_any_role(current_user, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        tabs.append({"name": "Admin", "endpoint": "portal.admin"})
    return tabs


@portal_bp.context_processor
def inject_portal_context():
    return {
        "sidebar_tabs": build_sidebar_tabs(),
        "current_user_roles": [role.name for role in current_user.get_active_roles()]
        if current_user.is_authenticated
        else [],
    }


@portal_bp.route("/dashboard")
@login_required
def dashboard():
    semester = get_current_semester()
    return render_template("portal/dashboard.html", semester=semester)


@portal_bp.route("/calendar")
@login_required
def calendar():
    semester = get_current_semester()
    events = []
    if semester:
        events = (
            Event.query.filter_by(semester_id=semester.id, is_deleted=False)
            .order_by(Event.starts_at.asc())
            .all()
        )
    return render_template("portal/calendar.html", semester=semester, events=events)


@portal_bp.route("/events", methods=["POST"])
@login_required
def create_event():
    title = request.form.get("title", "").strip()
    if not title:
        flash("Event title is required.", "error")
        return redirect(url_for("portal.calendar"))

    semester = get_current_semester()
    if not semester:
        flash("No active semester found.", "error")
        return redirect(url_for("portal.calendar"))

    committee_name = request.form.get("committee")
    committee = Committee.query.filter_by(name=committee_name).first() if committee_name else None
    if committee and not can_manage_committee(current_user, committee.name.lower()):
        abort(403)

    try:
        starts_at = datetime.fromisoformat(request.form.get("starts_at"))
        ends_at = datetime.fromisoformat(request.form.get("ends_at"))
    except (TypeError, ValueError):
        flash("Invalid start/end times.", "error")
        return redirect(url_for("portal.calendar"))

    event = Event(
        title=title,
        description=request.form.get("description") or None,
        location=request.form.get("location") or None,
        starts_at=starts_at,
        ends_at=ends_at,
        committee_id=committee.id if committee else None,
        semester_id=semester.id,
        created_by=current_user.id,
        created_by_user_id=current_user.id,
    )
    db.session.add(event)
    log_action(current_user.id, "EVENT_CREATED", "event", None, {"title": title})
    db.session.commit()
    flash("Event created.", "success")
    return redirect(url_for("portal.calendar"))


@portal_bp.route("/events/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    if not can_edit_event(current_user, event):
        abort(403)
    event.is_deleted = True
    event.updated_by_user_id = current_user.id
    event.updated_at = datetime.utcnow()
    log_action(current_user.id, "EVENT_DELETED", "event", event.id)
    db.session.commit()
    flash("Event deleted.", "success")
    return redirect(url_for("portal.calendar"))


@portal_bp.route("/dues")
@login_required
def dues():
    semester = get_current_semester()
    charges = DuesCharge.query.filter_by(user_id=current_user.id, is_deleted=False).all()
    payments = DuesPayment.query.filter_by(user_id=current_user.id, is_deleted=False).all()
    allocations = DuesPaymentAllocation.query.join(
        DuesPayment, DuesPayment.id == DuesPaymentAllocation.payment_id
    ).filter(DuesPayment.user_id == current_user.id)
    allocation_map = {}
    for allocation in allocations:
        allocation_map.setdefault(allocation.charge_id, 0)
        allocation_map[allocation.charge_id] += allocation.allocated_cents

    total_charges = sum(charge.charge_cents for charge in charges)
    total_payments = sum(payment.amount_cents for payment in payments)
    balance_cents = total_charges - total_payments

    current_semester_balance = 0
    prior_balance = 0
    for charge in charges:
        paid = allocation_map.get(charge.id, 0)
        remaining = charge.charge_cents - paid
        if semester and charge.semester_id == semester.id:
            current_semester_balance += remaining
        else:
            prior_balance += remaining

    return render_template(
        "portal/dues.html",
        semester=semester,
        charges=charges,
        payments=payments,
        allocations=allocation_map,
        balance_cents=balance_cents,
        current_semester_balance=current_semester_balance,
        prior_balance=prior_balance,
    )


@portal_bp.route("/committee/<committee>")
@login_required
def committee(committee):
    if not can_manage_committee(current_user, committee.lower()):
        abort(403)

    semester = get_current_semester()
    committee_record = Committee.query.filter_by(name=committee).first()
    allocation = None
    transactions = []
    remaining_cents = 0

    if semester and committee_record:
        allocation = CommitteeBudgetAllocation.query.filter_by(
            semester_id=semester.id, committee_id=committee_record.id
        ).order_by(CommitteeBudgetAllocation.allocated_at.desc()).first()
        transactions = CommitteeTransaction.query.filter_by(
            semester_id=semester.id, committee_id=committee_record.id, is_deleted=False
        ).order_by(CommitteeTransaction.created_at.desc()).all()
        total_spend = sum(tx.amount_cents for tx in transactions)
        allocation_cents = allocation.allocated_cents if allocation else 0
        remaining_cents = allocation_cents + total_spend

    return render_template(
        "portal/committee.html",
        semester=semester,
        committee=committee,
        allocation=allocation,
        transactions=transactions,
        remaining_cents=remaining_cents,
    )


@portal_bp.route("/committee/<committee>/transactions", methods=["POST"])
@login_required
def create_committee_transaction(committee):
    if not can_manage_committee(current_user, committee.lower()):
        abort(403)

    semester = get_current_semester()
    if not semester:
        flash("No active semester found.", "error")
        return redirect(url_for("portal.committee", committee=committee))

    committee_record = Committee.query.filter_by(name=committee).first()
    if not committee_record:
        abort(404)

    amount = abs(int(request.form.get("amount_cents", 0)))
    direction = request.form.get("direction", "spend")
    amount_cents = -amount if direction == "spend" else amount

    transaction = CommitteeTransaction(
        semester_id=semester.id,
        committee_id=committee_record.id,
        amount_cents=amount_cents,
        vendor=request.form.get("vendor") or None,
        category=request.form.get("category") or None,
        memo=request.form.get("memo") or None,
        created_by_user_id=current_user.id,
    )
    db.session.add(transaction)
    log_action(current_user.id, "TX_CREATED", "committee_transaction", None, {"committee": committee})
    db.session.commit()
    flash("Transaction recorded.", "success")
    return redirect(url_for("portal.committee", committee=committee))


@portal_bp.route("/committee/transactions/<int:transaction_id>/delete", methods=["POST"])
@login_required
def delete_committee_transaction(transaction_id):
    transaction = CommitteeTransaction.query.get_or_404(transaction_id)
    committee_record = Committee.query.get(transaction.committee_id)
    if not committee_record or not can_manage_committee(current_user, committee_record.name.lower()):
        abort(403)
    if not has_any_role(current_user, ROLE_TREASURER, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT) and (
        transaction.created_by_user_id != current_user.id
    ):
        abort(403)

    transaction.is_deleted = True
    transaction.deleted_by_user_id = current_user.id
    transaction.deleted_at = datetime.utcnow()
    log_action(current_user.id, "TX_DELETED", "committee_transaction", transaction.id)
    db.session.commit()
    flash("Transaction deleted.", "success")
    return redirect(url_for("portal.committee", committee=committee_record.name))


@portal_bp.route("/treasurer")
@login_required
def treasurer():
    if not has_any_role(current_user, ROLE_TREASURER, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        abort(403)
    semester = get_current_semester()
    allocations = []
    if semester:
        allocations = CommitteeBudgetAllocation.query.filter_by(semester_id=semester.id).all()
    ledger = MasterLedger.query.filter_by(is_deleted=False).order_by(MasterLedger.created_at.desc()).all()
    committees = {committee.id: committee.name for committee in Committee.query.all()}
    return render_template(
        "portal/treasurer.html",
        semester=semester,
        allocations=allocations,
        ledger=ledger,
        committees=committees,
    )


@portal_bp.route("/treasurer/allocations", methods=["POST"])
@login_required
def create_allocation():
    if not has_any_role(current_user, ROLE_TREASURER, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        abort(403)
    semester = get_current_semester()
    if not semester:
        flash("No active semester found.", "error")
        return redirect(url_for("portal.treasurer"))

    committee_name = request.form.get("committee")
    committee = Committee.query.filter_by(name=committee_name).first()
    if not committee:
        flash("Committee not found.", "error")
        return redirect(url_for("portal.treasurer"))

    allocation = CommitteeBudgetAllocation(
        semester_id=semester.id,
        committee_id=committee.id,
        allocated_cents=int(request.form.get("allocated_cents", 0)),
        allocated_by_user_id=current_user.id,
        notes=request.form.get("notes") or None,
    )
    db.session.add(allocation)
    log_action(current_user.id, "TX_CREATED", "committee_allocation", None, {"committee": committee.name})
    db.session.commit()
    flash("Allocation saved.", "success")
    return redirect(url_for("portal.treasurer"))


@portal_bp.route("/treasurer/dues/charges/batch", methods=["POST"])
@login_required
def create_dues_batch():
    if not has_any_role(current_user, ROLE_TREASURER, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        abort(403)
    semester = get_current_semester()
    if not semester:
        flash("No active semester found.", "error")
        return redirect(url_for("portal.treasurer"))

    amount_cents = int(request.form.get("charge_cents", 0))
    reason = request.form.get("reason", "Semester dues")
    active_users = User.query.filter_by(is_active=True).all()
    for user in active_users:
        charge = DuesCharge(
            semester_id=semester.id,
            user_id=user.id,
            charge_cents=amount_cents,
            reason=reason,
            issued_by_user_id=current_user.id,
        )
        db.session.add(charge)
    log_action(current_user.id, "DUES_CHARGE_BATCH_CREATED", "dues_charge", None, {"count": len(active_users)})
    db.session.commit()
    flash("Dues charges created.", "success")
    return redirect(url_for("portal.treasurer"))


@portal_bp.route("/treasurer/dues/payments", methods=["POST"])
@login_required
def record_dues_payment():
    if not has_any_role(current_user, ROLE_TREASURER, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        abort(403)

    user_id = int(request.form.get("user_id"))
    amount_cents = int(request.form.get("amount_cents", 0))
    payment = DuesPayment(
        user_id=user_id,
        amount_cents=amount_cents,
        method=request.form.get("method", "other"),
        recorded_by_user_id=current_user.id,
        notes=request.form.get("notes") or None,
    )
    db.session.add(payment)
    log_action(current_user.id, "DUES_PAYMENT_RECORDED", "dues_payment", None, {"user_id": user_id})
    db.session.commit()
    flash("Payment recorded.", "success")
    return redirect(url_for("portal.treasurer"))


@portal_bp.route("/admin")
@login_required
def admin():
    if not has_any_role(current_user, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        abort(403)
    users = User.query.order_by(User.last_name.asc()).all()
    audit_entries = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(25).all()
    return render_template("portal/admin.html", users=users, audit_entries=audit_entries)


@portal_bp.route("/admin/users/<int:user_id>/roles", methods=["POST"])
@login_required
def add_role(user_id):
    if not has_any_role(current_user, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        abort(403)
    user = User.query.get_or_404(user_id)
    role_name = request.form.get("role_name")
    if not role_name:
        flash("Role name is required.", "error")
        return redirect(url_for("portal.admin"))
    if grant_role(user, role_name, actor_user_id=current_user.id):
        flash("Role granted.", "success")
    else:
        flash("Role already assigned or invalid.", "error")
    return redirect(url_for("portal.admin"))


@portal_bp.route("/admin/users/<int:user_id>/roles/<role_name>", methods=["POST"])
@login_required
def remove_role(user_id, role_name):
    if not has_any_role(current_user, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        abort(403)
    user = User.query.get_or_404(user_id)
    if revoke_role(user, role_name, actor_user_id=current_user.id):
        flash("Role revoked.", "success")
    else:
        flash("Role not active.", "error")
    return redirect(url_for("portal.admin"))


@portal_bp.route("/admin/semester/rollover", methods=["POST"])
@login_required
def semester_rollover():
    if not has_any_role(current_user, ROLE_VICE_PRESIDENT, ROLE_PRESIDENT):
        abort(403)
    confirm = request.form.get("confirm")
    if confirm != "ROLL_OVER":
        flash("Confirmation string mismatch. Type ROLL_OVER to proceed.", "error")
        return redirect(url_for("portal.admin"))

    current_semester = get_current_semester()
    name = request.form.get("name")
    season = request.form.get("season")
    year = int(request.form.get("year"))
    semester_id = f"{season.lower()}_{year}"

    if current_semester:
        current_semester.is_current = False
        Event.query.filter_by(semester_id=current_semester.id).update({"is_archived": True})

    new_semester = Semester(
        id=semester_id,
        name=name,
        year=year,
        season=season,
        start_date=datetime.utcnow(),
        is_current=True,
    )
    db.session.add(new_semester)
    log_action(current_user.id, "SEMESTER_ROLLOVER", "semester", None, {"name": name})
    db.session.commit()
    flash("Semester rollover completed.", "success")
    return redirect(url_for("portal.admin"))
