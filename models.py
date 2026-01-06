"""
Database models for the Fraternity Management System
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import json

db = SQLAlchemy()

# Association table for many-to-many relationship between Users and Roles
user_roles = db.Table(
    'user_roles',
    db.Column('id', db.Integer, primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), nullable=False),
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id'), nullable=False),
    db.Column('granted_by_user_id', db.Integer, db.ForeignKey('users.id'), nullable=True),
    db.Column('granted_at', db.DateTime, default=datetime.utcnow, nullable=False),
    db.Column('revoked_at', db.DateTime, nullable=True),
    db.UniqueConstraint('user_id', 'role_id', 'revoked_at', name='uq_user_roles_active')
)

class User(UserMixin, db.Model):
    """User accounts for authentication and role management"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    status = db.Column(db.String(20), default='pending')  # pending, active, suspended
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    roles = db.relationship(
        'Role',
        secondary=user_roles,
        primaryjoin=id == user_roles.c.user_id,
        secondaryjoin=lambda: Role.id == user_roles.c.role_id,
        foreign_keys=[user_roles.c.user_id, user_roles.c.role_id],
        lazy='subquery',
        backref=db.backref(
            'users',
            lazy=True,
            primaryjoin=lambda: Role.id == user_roles.c.role_id,
            secondaryjoin=lambda: User.id == user_roles.c.user_id,
            foreign_keys=[user_roles.c.user_id, user_roles.c.role_id],
        ),
    )
    member_record = db.relationship('Member', backref='user_account', uselist=False)
    
    # Requests and submissions
    payment_plan_suggestions = db.relationship('PaymentPlanSuggestion', 
                                             foreign_keys='PaymentPlanSuggestion.suggested_by',
                                             backref='suggester', lazy=True)
    reimbursement_requests = db.relationship('ReimbursementRequest', 
                                           foreign_keys='ReimbursementRequest.requested_by',
                                           backref='requester', lazy=True)
    spending_plans = db.relationship('SpendingPlan', 
                                   foreign_keys='SpendingPlan.created_by',
                                   backref='creator', lazy=True)
    transactions = db.relationship('Transaction', 
                                 foreign_keys='Transaction.created_by',
                                 backref='creator', lazy=True)

    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if provided password matches hash"""
        return check_password_hash(self.password_hash, password)
    
    def has_role(self, role_name):
        """Check if user has a specific role"""
        return any(role.name == role_name for role in self.get_active_roles())

    def get_active_roles(self):
        """Return roles that are currently active (not revoked)."""
        return (
            Role.query.join(user_roles, Role.id == user_roles.c.role_id)
            .filter(user_roles.c.user_id == self.id, user_roles.c.revoked_at.is_(None))
            .all()
        )
    
    def get_primary_role(self):
        """Get the user's primary/highest role"""
        active_roles = self.get_active_roles()
        if not active_roles:
            return None
        
        # Role hierarchy (highest to lowest)
        role_hierarchy = {
            'president': 7,
            'vice_president': 6,
            'treasurer': 5,
            'chair_brotherhood': 4,
            'chair_social': 4,
            'chair_recruitment': 4,
            'brother': 1
        }
        
        return max(active_roles, key=lambda r: role_hierarchy.get(r.name, 0))
    
    @property
    def full_name(self):
        """Get user's full name"""
        return f"{self.first_name} {self.last_name}"
    
    def __repr__(self):
        return f'<User {self.phone}>'

class Role(db.Model):
    """User roles with permissions"""
    __tablename__ = 'roles'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    permissions = db.Column(db.Text, nullable=True)  # JSON string of permissions
    
    def get_permissions(self):
        """Get permissions as a Python object"""
        if self.permissions:
            return json.loads(self.permissions)
        return {}
    
    def set_permissions(self, permissions_dict):
        """Set permissions from a Python dict"""
        self.permissions = json.dumps(permissions_dict)
    
    def __repr__(self):
        return f'<Role {self.name}>'

class Semester(db.Model):
    """Semester/term management"""
    __tablename__ = 'semesters'
    
    id = db.Column(db.String(50), primary_key=True)  # e.g., "fall_2024"
    name = db.Column(db.String(50), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    season = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=True)
    starts_on = db.Column(db.DateTime, nullable=True)
    ends_on = db.Column(db.DateTime, nullable=True)
    is_current = db.Column(db.Boolean, default=False)
    archived = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    members = db.relationship('Member', backref='semester', lazy=True)
    transactions = db.relationship('Transaction', backref='semester', lazy=True)
    spending_plans = db.relationship('SpendingPlan', backref='semester', lazy=True)
    
    def __repr__(self):
        return f'<Semester {self.name}>'

class Member(db.Model):
    """Member information and dues tracking"""
    __tablename__ = 'members'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # Nullable for legacy data
    name = db.Column(db.String(100), nullable=False)
    contact = db.Column(db.String(100), nullable=False)
    contact_type = db.Column(db.String(10), default='phone')  # phone or email
    dues_amount = db.Column(db.Float, nullable=False)
    payment_plan = db.Column(db.String(20), nullable=False)  # semester, monthly, bimonthly, custom
    custom_schedule = db.Column(db.Text, nullable=True)  # JSON string of custom payment schedule
    semester_id = db.Column(db.String(50), db.ForeignKey('semesters.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    payments = db.relationship('Payment', backref='member', lazy=True)
    payment_plan_suggestions = db.relationship('PaymentPlanSuggestion', backref='member', lazy=True)
    
    def get_custom_schedule(self):
        """Get custom schedule as Python object"""
        if self.custom_schedule:
            return json.loads(self.custom_schedule)
        return []
    
    def set_custom_schedule(self, schedule_list):
        """Set custom schedule from Python list"""
        self.custom_schedule = json.dumps(schedule_list)
    
    def get_total_paid(self):
        """Calculate total amount paid by this member"""
        return sum(payment.amount for payment in self.payments)
    
    def get_balance(self):
        """Calculate remaining balance"""
        return self.dues_amount - self.get_total_paid()
    
    def is_paid_up(self):
        """Check if member has paid all dues"""
        return self.get_balance() <= 0
    
    @property
    def full_name(self):
        """Get member's full name for compatibility with templates"""
        return self.name
    
    def __repr__(self):
        return f'<Member {self.name}>'

class Payment(db.Model):
    """Individual payments made by members"""
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False)  # Zelle, Venmo, Cash, Check
    date = db.Column(db.DateTime, nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Payment ${self.amount} by {self.member.name}>'


class AuditLog(db.Model):
    """Audit trail for sensitive actions."""
    __tablename__ = 'audit_log'

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    target_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(db.Integer, nullable=True)
    details_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Committee(db.Model):
    """Committees with fixed names and active flags."""
    __tablename__ = 'committees'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)


class CommitteeBudgetAllocation(db.Model):
    """Semester-based committee allocations."""
    __tablename__ = 'committee_budget_allocations'

    id = db.Column(db.Integer, primary_key=True)
    semester_id = db.Column(db.String(50), db.ForeignKey('semesters.id'), nullable=False)
    committee_id = db.Column(db.Integer, db.ForeignKey('committees.id'), nullable=False)
    allocated_cents = db.Column(db.Integer, nullable=False)
    allocated_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    allocated_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)


class CommitteeTransaction(db.Model):
    """Committee transactions tied to a semester and optional event."""
    __tablename__ = 'committee_transactions'

    id = db.Column(db.Integer, primary_key=True)
    semester_id = db.Column(db.String(50), db.ForeignKey('semesters.id'), nullable=False)
    committee_id = db.Column(db.Integer, db.ForeignKey('committees.id'), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    vendor = db.Column(db.String(120), nullable=True)
    category = db.Column(db.String(120), nullable=True)
    memo = db.Column(db.Text, nullable=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)


class MasterLedger(db.Model):
    """Master ledger that does not reset by semester."""
    __tablename__ = 'master_ledger'

    id = db.Column(db.Integer, primary_key=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(120), nullable=True)
    memo = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)


class DuesCharge(db.Model):
    """Dues charges per semester and member."""
    __tablename__ = 'dues_charges'

    id = db.Column(db.Integer, primary_key=True)
    semester_id = db.Column(db.String(50), db.ForeignKey('semesters.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    charge_cents = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    issued_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    due_date = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)


class DuesPayment(db.Model):
    """Payments recorded against dues charges."""
    __tablename__ = 'dues_payments'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)
    method = db.Column(db.String(30), nullable=False)
    external_ref = db.Column(db.String(120), nullable=True)
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)


class DuesPaymentAllocation(db.Model):
    """Allocations from a payment to specific charges."""
    __tablename__ = 'dues_payment_allocations'

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('dues_payments.id'), nullable=False)
    charge_id = db.Column(db.Integer, db.ForeignKey('dues_charges.id'), nullable=False)
    allocated_cents = db.Column(db.Integer, nullable=False)


class GoogleEventLink(db.Model):
    """Google Calendar linkage for events."""
    __tablename__ = 'google_event_links'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    google_calendar_id = db.Column(db.String(255), nullable=False)
    google_event_id = db.Column(db.String(255), nullable=False)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    sync_status = db.Column(db.String(20), nullable=False, default='pending')
    last_error = db.Column(db.Text, nullable=True)


class PasswordResetToken(db.Model):
    """Password reset tokens with expiry and usage tracking."""
    __tablename__ = 'password_reset_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    requested_from_ip = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Transaction(db.Model):
    """Financial transactions"""
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)  # income or expense
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    semester_id = db.Column(db.String(50), db.ForeignKey('semesters.id'), nullable=False)
    related_request_id = db.Column(db.Integer, db.ForeignKey('reimbursement_requests.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Transaction {self.type} ${self.amount} - {self.description}>'

class BudgetLimit(db.Model):
    """Budget limits by category and semester"""
    __tablename__ = 'budget_limits'
    
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    semester_id = db.Column(db.String(50), db.ForeignKey('semesters.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Unique constraint on category + semester
    __table_args__ = (db.UniqueConstraint('category', 'semester_id', name='_category_semester_uc'),)
    
    def __repr__(self):
        return f'<BudgetLimit {self.category}: ${self.amount}>'

class PaymentPlanSuggestion(db.Model):
    """Payment plan suggestions from brothers"""
    __tablename__ = 'payment_plan_suggestions'
    
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    suggested_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    original_plan = db.Column(db.Text, nullable=False)  # JSON of current plan
    suggested_plan = db.Column(db.Text, nullable=False)  # JSON of suggested plan
    treasurer_modified_plan = db.Column(db.Text, nullable=True)  # JSON of treasurer's counter-proposal
    status = db.Column(db.String(20), default='pending')  # pending, approved, modified, rejected, accepted
    notes = db.Column(db.Text, nullable=True)
    treasurer_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    
    def get_original_plan(self):
        return json.loads(self.original_plan) if self.original_plan else {}
    
    def set_original_plan(self, plan_dict):
        self.original_plan = json.dumps(plan_dict)
    
    def get_suggested_plan(self):
        return json.loads(self.suggested_plan) if self.suggested_plan else {}
    
    def set_suggested_plan(self, plan_dict):
        self.suggested_plan = json.dumps(plan_dict)
    
    def get_treasurer_modified_plan(self):
        return json.loads(self.treasurer_modified_plan) if self.treasurer_modified_plan else {}
    
    def set_treasurer_modified_plan(self, plan_dict):
        self.treasurer_modified_plan = json.dumps(plan_dict)
    
    def __repr__(self):
        return f'<PaymentPlanSuggestion {self.status} for {self.member.name}>'

class ReimbursementRequest(db.Model):
    """Reimbursement requests from officers"""
    __tablename__ = 'reimbursement_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    requested_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    purpose = db.Column(db.Text, nullable=False)
    receipt_data = db.Column(db.Text, nullable=True)  # Base64 encoded receipt image
    receipt_filename = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected, paid
    reviewer_notes = db.Column(db.Text, nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to reviewer
    reviewer = db.relationship('User', foreign_keys=[reviewed_by], backref='reviewed_requests')
    
    def __repr__(self):
        return f'<ReimbursementRequest ${self.amount} - {self.purpose}>'

class Event(db.Model):
    """Events planned by chairs"""
    __tablename__ = 'events'
    
    id = db.Column(db.Integer, primary_key=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    category = db.Column(db.String(50), nullable=False)  # Social, Recruitment, Brotherhood
    committee_id = db.Column(db.Integer, db.ForeignKey('committees.id'), nullable=True)
    semester_id = db.Column(db.String(50), db.ForeignKey('semesters.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    date = db.Column(db.DateTime, nullable=True)
    starts_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)
    location = db.Column(db.String(200), nullable=True)
    estimated_cost = db.Column(db.Float, nullable=False, default=0.0)
    actual_cost = db.Column(db.Float, nullable=True)
    max_attendees = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='planned')  # planned, approved, cancelled, completed
    notes = db.Column(db.Text, nullable=True)
    spending_plan_id = db.Column(db.Integer, db.ForeignKey('spending_plans.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_archived = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    
    # Relationships
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_events')
    
    def __repr__(self):
        return f'<Event {self.title} - {self.category}>'

class SpendingPlan(db.Model):
    """Semester spending plans submitted by officers"""
    __tablename__ = 'spending_plans'
    
    id = db.Column(db.Integer, primary_key=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    semester_id = db.Column(db.String(50), db.ForeignKey('semesters.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    total_budget = db.Column(db.Float, nullable=False, default=0.0)
    plan_data = db.Column(db.Text, nullable=False)  # JSON with events, amounts, timeline
    version = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    treasurer_approved = db.Column(db.Boolean, default=False)
    president_approved = db.Column(db.Boolean, default=False)
    vp_approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    events = db.relationship('Event', backref='spending_plan', lazy=True)
    
    def get_plan_data(self):
        return json.loads(self.plan_data) if self.plan_data else {}
    
    def set_plan_data(self, data_dict):
        self.plan_data = json.dumps(data_dict)
    
    def __repr__(self):
        return f'<SpendingPlan {self.title} - {self.category}>'

class TreasurerConfig(db.Model):
    """Treasurer configuration settings"""
    __tablename__ = 'treasurer_config'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    smtp_username = db.Column(db.String(120), nullable=True)
    smtp_password = db.Column(db.String(255), nullable=True)
    twilio_sid = db.Column(db.String(100), nullable=True)
    twilio_token = db.Column(db.String(100), nullable=True)
    twilio_phone = db.Column(db.String(20), nullable=True)
    google_credentials_path = db.Column(db.String(255), nullable=True)
    google_sheets_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<TreasurerConfig {self.name}>'

# Default roles and permissions
DEFAULT_ROLES = {
    'admin': {
        'description': 'System administrator (legacy)',
        'permissions': {
            'manage_roles': True,
            'manage_semesters': True,
            'manage_master_budget': True,
            'manage_committee_allocations': True,
            'manage_dues': True,
            'manage_events': True
        }
    },
    'brother': {
        'description': 'Default fraternity member access',
        'permissions': {
            'view_calendar': True,
            'view_dues': True
        }
    },
    'treasurer': {
        'description': 'Master budget and dues administration',
        'permissions': {
            'manage_master_budget': True,
            'manage_committee_allocations': True,
            'manage_dues': True,
            'manage_events': True
        }
    },
    'president': {
        'description': 'Admin-level access with oversight tools',
        'permissions': {
            'manage_roles': True,
            'manage_semesters': True,
            'manage_master_budget': True,
            'manage_committee_allocations': True,
            'manage_dues': True,
            'manage_events': True
        }
    },
    'vice_president': {
        'description': 'Admin access for roles and semester rollover',
        'permissions': {
            'manage_roles': True,
            'manage_semesters': True,
            'manage_events': True
        }
    },
    'chair_brotherhood': {
        'description': 'Manage brotherhood committee budget and events',
        'permissions': {
            'manage_committee_budget': True,
            'manage_committee_events': True
        }
    },
    'chair_social': {
        'description': 'Manage social committee budget and events',
        'permissions': {
            'manage_committee_budget': True,
            'manage_committee_events': True
        }
    },
    'chair_recruitment': {
        'description': 'Manage recruitment committee budget and events',
        'permissions': {
            'manage_committee_budget': True,
            'manage_committee_events': True
        }
    }
}

def init_default_roles():
    """Initialize default roles in the database"""
    for role_name, role_data in DEFAULT_ROLES.items():
        existing_role = Role.query.filter_by(name=role_name).first()
        if not existing_role:
            role = Role(
                name=role_name,
                description=role_data['description']
            )
            role.set_permissions(role_data['permissions'])
            db.session.add(role)
    
    db.session.commit()
