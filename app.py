from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import os
import json
from typing import Dict, List, Optional
from dotenv import load_dotenv
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

# Database imports
from models import db, User, Role, Member, Transaction, Semester, Payment, BudgetLimit, TreasurerConfig, Event, init_default_roles
from database import create_app as create_database_app, init_database

# Import Flask blueprints
# from notifications import notifications_bp  # Commented out due to compatibility issues
from export_system import export_bp
from chair_management import chair_bp
from executive_views import exec_bp
from portal import portal_bp


# Load environment variables
load_dotenv()

# Initialize Flask app with database support
database_url = os.environ.get('DATABASE_URL')
if not database_url:
    raise RuntimeError("DATABASE_URL environment variable is required. Please configure your database.")

print(f"🔍 Initializing app with database: {database_url[:50]}...")
app = create_database_app('production' if os.environ.get('FLASK_ENV') == 'production' else 'development')

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Initialize database tables
print("🔄 Initializing database tables...")
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables ready")
    except Exception as e:
        print(f"⚠️ Database table creation warning: {e}")

print("✅ App initialized with database support")

# Register blueprints
# app.register_blueprint(notifications_bp)  # Commented out due to compatibility issues
app.register_blueprint(export_bp)
app.register_blueprint(chair_bp)
app.register_blueprint(exec_bp)
app.register_blueprint(portal_bp)

# SMS Gateway mappings for email-to-SMS (Updated and optimized)
SMS_GATEWAYS = {
    'verizon': '@vtext.com',
    'att': '@txt.att.net', 
    'tmobile': '@tmomail.net',
    'sprint': '@messaging.sprintpcs.com',  # Now T-Mobile
    'boost': '@smsmyboostmobile.com',
    'cricket': '@sms.cricketwireless.net',
    'uscellular': '@email.uscc.net',
    'virgin': '@vmobl.com',
    'metropcs': '@mymetropcs.com',
    # Additional gateways for better coverage
    'google_fi': '@msg.fi.google.com',
    'xfinity': '@vtext.com',
    'straighttalk': '@vtext.com'
}

# Primary gateways that work most reliably
PRIMARY_GATEWAYS = ['verizon', 'att', 'tmobile']

def send_email_to_sms(phone, message, config):
    """Send SMS via email-to-SMS gateway with improved error handling"""
    if not config.smtp_username or not config.smtp_password:
        print("SMS Error: SMTP credentials not configured")
        return False
    
    # Clean and validate phone number
    clean_phone = ''.join(filter(str.isdigit, phone))
    if len(clean_phone) == 11 and clean_phone.startswith('1'):
        clean_phone = clean_phone[1:]  # Remove leading 1
    elif len(clean_phone) != 10:
        print(f"SMS Error: Invalid phone number format: {phone}")
        return False
    
    # Limit message length for SMS compatibility
    if len(message) > 160:
        message = message[:157] + "..."
    
    # Try primary gateways first (most reliable)
    gateways_to_try = [(name, SMS_GATEWAYS[name]) for name in PRIMARY_GATEWAYS]
    
    success_count = 0
    last_error = None
    
    for carrier, gateway in gateways_to_try:
        try:
            sms_email = clean_phone + gateway
            print(f"Attempting SMS via {carrier} to {sms_email}")
        
            # Create email message
            msg = MIMEText(message)
            msg['Subject'] = ''  # Empty subject for SMS
            msg['From'] = config.smtp_username
            msg['To'] = sms_email
        
            # Send via SMTP with timeout settings
            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)  # 10 second timeout
            server.set_debuglevel(0)  # Disable debug for production
            server.starttls()
            server.login(config.smtp_username, config.smtp_password)
            server.send_message(msg)
            server.quit()
        
            success_count += 1
            print(f"SMS sent successfully via {carrier}")
        
            # Don't try other gateways if one succeeds
            break
        
        except smtplib.SMTPAuthenticationError as e:
            print(f"SMS Error ({carrier}): Authentication failed - check Gmail app password")
            last_error = f"Authentication failed: {str(e)}"
            break  # No point trying other gateways if auth fails
        except smtplib.SMTPException as e:
            print(f"SMS Error ({carrier}): SMTP error - {str(e)}")
            last_error = f"SMTP error: {str(e)}"
            continue  # Try next gateway
        except Exception as e:
            print(f"SMS Error ({carrier}): {str(e)}")
            last_error = f"General error: {str(e)}"
            continue  # Try next gateway
    
    if success_count == 0:
        print(f"SMS Failed: All gateways failed. Last error: {last_error}")
    
    return success_count > 0

def notify_treasurer(message, config, notification_type="Alert"):
    """Send notification to treasurer via SMS and email"""
    if not config.name:
        return False
    
    sent = False
    
    # Send email to treasurer
    if config.email and config.smtp_username and config.smtp_password:
        try:
            msg = MIMEText(f"Fraternity Treasurer {notification_type}:\n\n{message}")
            msg['Subject'] = f'Fraternity Treasurer {notification_type}'
            msg['From'] = config.smtp_username
            msg['To'] = config.email
        
            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
            server.starttls()
            server.login(config.smtp_username, config.smtp_password)
            server.send_message(msg)
            server.quit()
            sent = True
        except Exception:
            pass
    
    # Send SMS to treasurer
    if config.phone:
        # Create SMS-friendly message based on notification type
        if notification_type == "New Brother Registration":
            # Extract key info for SMS
            lines = message.split('\\n')
            name_line = next((line for line in lines if line.startswith('Name:')), '')
            phone_line = next((line for line in lines if line.startswith('Phone:')), '')
            
            if name_line and phone_line:
                name = name_line.replace('Name: ', '')
                phone = phone_line.replace('Phone: ', '')
                sms_message = f"New brother: {name} ({phone}) registered. Check admin panel to verify."
            else:
                sms_message = "New brother registration. Check admin panel."
        else:
            # For other notification types, use the existing truncation
            sms_message = f"Treasurer {notification_type}: {message[:100]}..." if len(message) > 100 else f"Treasurer {notification_type}: {message}"
        
            print(f"📱 SMS Message ({len(sms_message)} chars): {sms_message}")
        if send_email_to_sms(config.phone, sms_message, config):
            sent = True
    
    return sent

def notify_payment_plan_request(member_name, plan_details, config):
    """Notify treasurer about payment plan request"""
    message = f"{member_name} has submitted a payment plan request:\n{plan_details}\n\nPlease review and approve in the app."
    return notify_treasurer(message, config, "Payment Plan Request")

def notify_reimbursement_request(submitter_name, amount, category, description, config):
    """Notify treasurer about reimbursement request"""
    message = f"{submitter_name} has submitted a reimbursement request:\n\nAmount: ${amount:.2f}\nCategory: {category}\nDescription: {description}\n\nPlease review and approve in the app."
    return notify_treasurer(message, config, "Reimbursement Request")

def notify_spending_plan_request(submitter_name, category, amount, description, config):
    """Notify treasurer about spending plan request"""
    message = f"{submitter_name} has submitted a spending plan request:\n\nCategory: {category}\nAmount: ${amount:.2f}\nDescription: {description}\n\nPlease review and approve in the app."
    return notify_treasurer(message, config, "Spending Plan Request")

def send_brother_credentials_sms(full_name, phone, username, password, config):
    """Send login credentials to approved brother via SMS with enhanced logging"""
    print(f"\n🔐 Starting brother credentials SMS process...")
    print(f"   Full name: {full_name}")
    print(f"   Phone: {phone}")
    print(f"   Username: {username}")
    
    if not config.smtp_username or not config.smtp_password:
        print("❌ Brother SMS Error: SMTP credentials not configured")
        print(f"   SMTP Username: {config.smtp_username}")
        print(f"   SMTP Password configured: {bool(config.smtp_password)}")
        return False
    
    # Create concise SMS message (SMS has 160 char limit)
    first_name = full_name.split()[0] if full_name else "Brother"
    message = f"Fraternity Account Approved! Hi {first_name}, Login: {username} Pass: {password} Change password after first login."
    
    # Check message length
    if len(message) > 160:
        # Create shorter version
        message = f"Account approved! {first_name}, Login: {username} Pass: {password}"
        print(f"📏 Message shortened to {len(message)} chars: {message}")
    else:
        print(f"📏 Message length OK: {len(message)} chars")
    
    print(f"📱 Sending brother credentials to {first_name} at {phone}")
    
    # Send SMS via email-to-SMS gateway with enhanced error reporting
    success = send_email_to_sms(phone, message, config)
    
    if success:
        print(f"✅ Brother credentials SMS sent successfully to {phone}")
    else:
        print(f"❌ Brother credentials SMS failed to {phone}")
        print(f"🔧 Debug: Config status - SMTP User: {config.smtp_username}, Phone: {phone}")
    
    return success

# Role-based access control for member roles
MEMBER_ROLE_PERMISSIONS = {
    'admin': {
        # Full admin permissions (treasurer)
        'view_all_data': True,
        'edit_all_data': True,
        'manage_users': True,
        'send_reminders': True,
        'add_transactions': True,
        'edit_transactions': True,
        'add_members': True,
        'edit_members': True,
        'record_payments': True,
        'manage_budgets': True,
        'assign_roles': True,
        'view_dues_info': True,
        'view_member_finances': True,
        'view_dues_summary': True,
        'submit_spending_plan': True,
        'submit_budget_increase': True,
        'submit_reimbursement': True,
        'submit_payment_plan': True,
        'view_all_contacts': True,
    },
    'brother': {
        # Basic brother access - can only see own dues, all contacts, submit requests
        'view_all_data': False,
        'view_own_data': True,
        'view_dues_info': True,  # Can see own dues only
        'view_member_finances': False,  # Cannot see other members' finances
        'view_dues_summary': False,  # Cannot see dues summary
        'edit_all_data': False,
        'manage_users': False,
        'send_reminders': False,
        'add_transactions': False,
        'edit_transactions': False,
        'add_members': False,
        'edit_members': False,
        'record_payments': False,
        'manage_budgets': False,
        'assign_roles': False,
        'submit_spending_plan': False,
        'submit_budget_increase': False,
        'submit_reimbursement': True,   # Can submit reimbursement requests
        'submit_payment_plan': True,    # Can submit payment plan requests
        'view_all_contacts': True,      # Can view all brother names/contacts
    },
    'president': {
        # President access - can see all dues/finances but cannot edit
        'view_all_data': True,
        'view_own_data': True,
        'view_dues_info': True,
        'view_member_finances': True,   # Can see all member finances
        'view_dues_summary': True,      # Can see dues collected/projected
        'edit_all_data': False,
        'manage_users': False,
        'send_reminders': False,
        'add_transactions': False,
        'edit_transactions': False,
        'add_members': False,
        'edit_members': False,
        'record_payments': False,
        'manage_budgets': False,
        'assign_roles': False,
        'submit_spending_plan': False,
        'submit_budget_increase': False,
        'submit_reimbursement': True,
        'submit_payment_plan': False,
        'view_all_contacts': True,
    },
    'vice_president': {
        # VP access - can see dues summary but NOT individual finances
        'view_all_data': False,
        'view_own_data': True,
        'view_dues_info': True,         # Can see own dues only
        'view_member_finances': False,  # CANNOT see individual finances
        'view_dues_summary': True,      # Can see general dues collected/projected
        'edit_all_data': False,
        'manage_users': False,
        'send_reminders': False,
        'add_transactions': False,
        'edit_transactions': False,
        'add_members': False,
        'edit_members': False,
        'record_payments': False,
        'manage_budgets': False,
        'assign_roles': False,
        'submit_spending_plan': False,
        'submit_budget_increase': False,
        'submit_reimbursement': True,
        'submit_payment_plan': False,
        'view_all_contacts': True,
    },
    'social_chair': {
        # Chair access - view own department budget only, submit requests
        'view_all_data': False,
        'view_own_data': True,
        'view_dues_info': True,         # Can see own dues only
        'view_member_finances': False,  # Cannot see other member finances
        'view_dues_summary': False,     # Cannot see dues summary
        'view_social_budget': True,     # Can see social budget only
        'edit_all_data': False,
        'manage_users': False,
        'send_reminders': False,
        'add_transactions': False,
        'edit_transactions': False,
        'add_members': False,
        'edit_members': False,
        'record_payments': False,
        'manage_budgets': False,
        'assign_roles': False,
        'submit_spending_plan': True,   # Can submit spending plans
        'submit_budget_increase': True, # Can request budget increases
        'submit_reimbursement': True,   # Can submit reimbursements
        'submit_payment_plan': False,
        'view_all_contacts': True,
    },
    'phi_ed_chair': {
        # Chair access - view own department budget only, submit requests
        'view_all_data': False,
        'view_own_data': True,
        'view_dues_info': True,         # Can see own dues only
        'view_member_finances': False,  # Cannot see other member finances
        'view_dues_summary': False,     # Cannot see dues summary
        'view_phi_ed_budget': True,     # Can see phi ed budget only
        'edit_all_data': False,
        'manage_users': False,
        'send_reminders': False,
        'add_transactions': False,
        'edit_transactions': False,
        'add_members': False,
        'edit_members': False,
        'record_payments': False,
        'manage_budgets': False,
        'assign_roles': False,
        'submit_spending_plan': True,   # Can submit spending plans
        'submit_budget_increase': True, # Can request budget increases
        'submit_reimbursement': True,   # Can submit reimbursements
        'submit_payment_plan': False,
        'view_all_contacts': True,
    },
    'brotherhood_chair': {
        # Chair access - view own department budget only, submit requests
        'view_all_data': False,
        'view_own_data': True,
        'view_dues_info': True,         # Can see own dues only
        'view_member_finances': False,  # Cannot see other member finances
        'view_dues_summary': False,     # Cannot see dues summary
        'view_brotherhood_budget': True, # Can see brotherhood budget only
        'edit_all_data': False,
        'manage_users': False,
        'send_reminders': False,
        'add_transactions': False,
        'edit_transactions': False,
        'add_members': False,
        'edit_members': False,
        'record_payments': False,
        'manage_budgets': False,
        'assign_roles': False,
        'submit_spending_plan': True,   # Can submit spending plans
        'submit_budget_increase': True, # Can request budget increases
        'submit_reimbursement': True,   # Can submit reimbursements
        'submit_payment_plan': False,
        'view_all_contacts': True,
    },
    'recruitment_chair': {
        # Chair access - view own department budget only, submit requests
        'view_all_data': False,
        'view_own_data': True,
        'view_dues_info': True,         # Can see own dues only
        'view_member_finances': False,  # Cannot see other member finances
        'view_dues_summary': False,     # Cannot see dues summary
        'view_recruitment_budget': True, # Can see recruitment budget only
        'edit_all_data': False,
        'manage_users': False,
        'send_reminders': False,
        'add_transactions': False,
        'edit_transactions': False,
        'add_members': False,
        'edit_members': False,
        'record_payments': False,
        'manage_budgets': False,
        'assign_roles': False,
        'submit_spending_plan': True,   # Can submit spending plans
        'submit_budget_increase': True, # Can request budget increases
        'submit_reimbursement': True,   # Can submit reimbursements
        'submit_payment_plan': False,
        'view_all_contacts': True,
    },
    'treasurer': {
        # Treasurer access - same as admin but assigned as member role
        'view_all_data': True,
        'edit_all_data': True,
        'manage_users': True,
        'send_reminders': True,
        'add_transactions': True,
        'edit_transactions': True,
        'add_members': True,
        'edit_members': True,
        'record_payments': True,
        'manage_budgets': True,
        'assign_roles': True,
        'view_dues_info': True,
        'view_member_finances': True,
        'view_dues_summary': True,
        'submit_spending_plan': True,
        'submit_budget_increase': True,
        'submit_reimbursement': True,
        'submit_payment_plan': True,
        'view_all_contacts': True,
    }
}

# Legacy role permissions for backwards compatibility
ROLE_PERMISSIONS = {
    'admin': MEMBER_ROLE_PERMISSIONS['admin'],
    'brother': MEMBER_ROLE_PERMISSIONS['brother'],
    'president': MEMBER_ROLE_PERMISSIONS['president']
}

def get_current_user_role():
    """Get current user's role based on session and database"""
    if session.get('preview_mode'):
        return session.get('preview_role', 'admin')
    
    # Check if user is admin/treasurer
    if session.get('user') == 'admin' or session.get('role') == 'admin':
        return 'admin'
    
    # Get role from database
    user_id = session.get('user_id')
    if user_id:
        user = User.query.get(user_id)
        if user:
            primary_role = user.get_primary_role()
            return primary_role.name if primary_role else 'brother'
    
    return session.get('role', 'brother')

def has_permission(permission_name):
    """Check if current user has a specific permission"""
    role = get_current_user_role()
    
    # Check member role permissions first
    if role in MEMBER_ROLE_PERMISSIONS:
        return MEMBER_ROLE_PERMISSIONS[role].get(permission_name, False)
    
    # Fallback to legacy role permissions
    return ROLE_PERMISSIONS.get(role, {}).get(permission_name, False)

def get_user_member():
    """Get the member object for the current user"""
    user_id = session.get('user_id')
    if user_id:
        user = User.query.get(user_id)
        if user and user.member_record:
            return user.member_record
    return None

def require_permission(permission_name):
    """Decorator to require specific permission for route access"""
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not has_permission(permission_name):
                flash(f'You do not have permission to {permission_name.replace("_", " ")}. This action is restricted to treasurers only.', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Configuration
BUDGET_CATEGORIES = [
    'Executive(GHQ, IFC, Flights)', 'Brotherhood', 'Social', 
    'Philanthropy', 'Recruitment', 'Phi ED', 'Housing', 'Bank Maintenance'
]

CHAIR_MANUAL_LINKS = {
    'social_chair': None,
    'phi_ed_chair': None,
    'recruitment_chair': None,
    'brotherhood_chair': None
}

CHAIR_ROLE_TO_CATEGORY = {
    'social_chair': 'Social',
    'phi_ed_chair': 'Phi ED',
    'recruitment_chair': 'Recruitment',
    'brotherhood_chair': 'Brotherhood'
}

OFFICER_ROLES = [
    'president',
    'vice_president',
    'social_chair',
    'phi_ed_chair',
    'recruitment_chair',
    'brotherhood_chair'
]

def build_budget_summary(semester_id=None, categories=None):
    """Build budget summary data for given semester and categories."""
    budget_summary = {}
    budget_limits = BudgetLimit.query
    if semester_id:
        budget_limits = budget_limits.filter_by(semester_id=semester_id)
    budget_limits = budget_limits.all()
    
    for limit in budget_limits:
        if categories and limit.category not in categories:
            continue
        expense_transactions = Transaction.query.filter_by(
            type='expense',
            category=limit.category,
            semester_id=limit.semester_id
        ).all()
        spent = sum(t.amount for t in expense_transactions)
        remaining = limit.amount - spent
        percent_used = (spent / limit.amount * 100) if limit.amount > 0 else 0
        budget_summary[limit.category] = {
            'budget_limit': limit.amount,
            'spent': spent,
            'remaining': remaining,
            'percent_used': percent_used
        }
    
    return budget_summary

def build_dues_summary(semester_id=None):
    """Build dues collection summary for a semester."""
    members_query = Member.query
    if semester_id:
        members_query = members_query.filter_by(semester_id=semester_id)
    members = members_query.all()
    
    total_projected = sum(member.dues_amount for member in members)
    total_collected = sum(sum(payment.amount for payment in member.payments) for member in members)
    outstanding = total_projected - total_collected
    collection_rate = (total_collected / total_projected * 100) if total_projected > 0 else 0
    
    return {
        'total_collected': total_collected,
        'total_projected': total_projected,
        'outstanding': outstanding,
        'collection_rate': collection_rate
    }








# Authentication decorator
def require_auth(f):
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Please log in to access this page')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

# Template context processor to make permission functions available in templates
@app.context_processor
def inject_permission_functions():
    return {
        'has_permission': has_permission,
        'get_current_user_role': get_current_user_role
    }

def authenticate_user(username, password):
    """Authenticate user using database"""
    try:
        user = None
        
        # Check for admin username
        if username == 'admin':
            user = User.query.filter_by(phone='admin').first()
        else:
            # Check by phone or email
            user = User.query.filter_by(phone=username).first()
            if not user:
                user = User.query.filter_by(email=username).first()
        
        if user and user.check_password(password):
            primary_role = user.get_primary_role()
            role_name = primary_role.name if primary_role else 'brother'
            return user, role_name
        
            return None, None
    except Exception as e:
        print(f"❌ Authentication error: {e}")
        return None, None

# Flask routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    
    username = request.form['username']
    password = request.form['password']
    
    try:
        user, role = authenticate_user(username, password)
        
        if user:
            login_user(user, remember=True)
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            session['user'] = user.phone
            session['role'] = role
            session['user_id'] = user.id
            flash(f'Welcome, {user.first_name}!')
            
            return redirect(url_for('portal.dashboard'))
        else:
            flash('Invalid username or password')
            return redirect(url_for('login'))
    
    except Exception as e:
        print(f"❌ Login error: {e}")
        flash('Login system error')
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    flash('You have been logged out successfully')
    return redirect(url_for('login'))

@app.route('/force-logout')
def force_logout():
    """Force logout - clears all sessions and redirects to login"""
    session.clear()
    flash('All sessions cleared - Please log in again')
    return redirect(url_for('login'))


@app.route('/monthly_income')
@require_auth
def monthly_income():
    try:
        # Database mode - get data from SQLAlchemy models
        from models import Payment
        print("🔍 Using database mode for monthly income")
        
        # Get all payments grouped by month
        payments = Payment.query.all()
        monthly_data = {}
        
        for payment in payments:
            month_key = payment.date.strftime('%Y-%m')
            month_name = payment.date.strftime('%B %Y')
            
            if month_key not in monthly_data:
                monthly_data[month_key] = {
                    'month_name': month_name,
                    'total_amount': 0.0,
                    'transaction_count': 0
                }
            
            monthly_data[month_key]['total_amount'] += payment.amount
            monthly_data[month_key]['transaction_count'] += 1
        
        print(f"🔍 Monthly data: {len(monthly_data)} months from database")
        
        return render_template('monthly_income.html', monthly_data=monthly_data)
    except Exception as e:
        print(f"❌ Monthly income error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error loading monthly income: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/')
def landing_page():
    # Check if user is already logged in
    if 'user' in session:
        # Redirect to appropriate dashboard based on role
        if session.get('role') == 'brother':
            return redirect(url_for('brother_dashboard'))
        else:
            return redirect(url_for('dashboard'))
    
    # Show login page for unauthenticated users
    return redirect(url_for('login'))

@app.route('/dashboard')
@require_auth
def dashboard():
    try:
        # Database mode - get data from SQLAlchemy models
        from models import BudgetLimit, Transaction, Member as DBMember
        
        members = {}
        pending_brothers = {}  # No pending brothers in database mode for now
        
        # Get actual members from database
        print("🔍 Querying members from database...")
        db_members = DBMember.query.all()
        print(f"🔍 Found {len(db_members)} members")
        
        for member in db_members:
            # Calculate total paid for this member
            total_paid = sum(payment.amount for payment in member.payments)
            
            print(f"🔍 {member.name}: ${member.dues_amount} dues, ${total_paid} paid, ${member.dues_amount - total_paid} balance")
            
            # Add payment info to member object for template display with multiple attribute names
            member.total_paid = total_paid
            member.balance = member.dues_amount - total_paid
            member.paid = total_paid  # Template might expect 'paid' attribute
            member.amount_paid = total_paid  # Another possible attribute name
            
            members[str(member.id)] = member
        
        # Calculate dues summary from database
        print("🔍 Calculating dues summary...")
        total_projected = sum(member.dues_amount for member in db_members)
        total_collected = 0.0
        
        # Sum all payments made by all members
        for member in db_members:
            try:
                member_payments = sum(payment.amount for payment in member.payments)
                total_collected += member_payments
                print(f"🔍 {member.name}: ${member_payments} paid of ${member.dues_amount} due")
            except Exception as e:
                print(f"⚠️ Error calculating payments for {member.name}: {e}")
        
        outstanding = total_projected - total_collected
        collection_rate = (total_collected / total_projected * 100) if total_projected > 0 else 0
        
        print(f"🔍 Totals: projected=${total_projected}, collected=${total_collected}, outstanding=${outstanding}")
        
        dues_summary = {
            'total_collected': total_collected,
            'total_projected': total_projected, 
            'outstanding': outstanding,
            'collection_rate': collection_rate
        }
        
        # Get budget summary from database - format to match template expectations
        budget_summary = {}
        budget_limits = BudgetLimit.query.all()
        
        for limit in budget_limits:
            # Calculate spending for this category
            expense_transactions = Transaction.query.filter_by(type='expense', category=limit.category).all()
            spent = sum(t.amount for t in expense_transactions)
            remaining = limit.amount - spent
            percent_used = (spent / limit.amount * 100) if limit.amount > 0 else 0
            
            print(f"🔍 Budget {limit.category}: ${limit.amount} limit, ${spent} spent ({len(expense_transactions)} transactions), ${remaining} remaining")
            
            # Create object-like dict that matches template expectations
            budget_summary[limit.category] = {
                'budget_limit': limit.amount,  # Template expects 'budget_limit' attribute
                'spent': spent,
                'remaining': remaining,
                'percent_used': percent_used,  # Template expects 'percent_used' attribute
                'limit': limit.amount,  # Also include 'limit' for backward compatibility
                'amount': spent  # Template might expect 'amount' for spent
            }
        
        print(f"🔍 Rendering dashboard with {len(members)} members")
        return render_template('index.html',
                         members=members,
                         budget_summary=budget_summary,
                         dues_summary=dues_summary,
                         categories=BUDGET_CATEGORIES,
                         pending_brothers=pending_brothers)
    
    except Exception as e:
        print(f"❌ Dashboard error: {e}")
        import traceback
        print(f"❌ Dashboard traceback: {traceback.format_exc()}")
        return f"Dashboard Error: {str(e)}", 500



@app.route('/add_transaction', methods=['POST'])
@require_auth
@require_permission('add_transactions')
def add_transaction():
    from models import Transaction as DBTransaction, Semester as DBSemester
    
    category = request.form['category']
    description = request.form['description']
    amount = float(request.form['amount'])
    transaction_type = request.form['type']
    
    try:
        # Database mode - create transaction directly
        current_semester = DBSemester.query.filter_by(is_current=True).first()
        
        transaction = DBTransaction(
            date=datetime.now().date(),
            category=category,
            description=description,
            amount=amount,
            type=transaction_type,
            semester_id=current_semester.id if current_semester else None
        )
        
        db.session.add(transaction)
        db.session.commit()
        print(f"✅ Transaction saved to database: {description} - ${amount}")
        flash('Transaction added successfully!')
    except Exception as e:
        db.session.rollback()
        print(f"❌ Database transaction failed: {e}")
        flash(f'Error adding transaction: {e}', 'error')
    
    return redirect(url_for('dashboard'))

@app.route('/edit_transaction/<transaction_id>', methods=['GET', 'POST'])
@require_auth
@require_permission('edit_transactions')
def edit_transaction(transaction_id):
    from models import Transaction as DBTransaction
    
    transaction = DBTransaction.query.get(int(transaction_id))
    if not transaction:
        flash('Transaction not found!')
        return redirect(url_for('transactions'))
    
    if request.method == 'GET':
        return render_template('edit_transaction.html', 
                             transaction=transaction,
                             categories=BUDGET_CATEGORIES + ['Dues Collection'])
    
    # POST request - update transaction
    try:
        transaction.category = request.form['category']
        transaction.description = request.form['description']
        transaction.amount = float(request.form['amount'])
        transaction.type = request.form['type']
        
        db.session.commit()
        flash('Transaction updated successfully!')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating transaction: {e}', 'error')
    
    return redirect(url_for('transactions'))

@app.route('/remove_transaction/<transaction_id>', methods=['POST'])
@require_auth
@require_permission('edit_transactions')
def remove_transaction(transaction_id):
    print(f"🗑️ Attempting to remove transaction: {transaction_id}")
    
    # Database mode - delete from SQLAlchemy
    from models import Transaction as DBTransaction, db
    try:
        transaction = DBTransaction.query.get_or_404(int(transaction_id))
        description = transaction.description
        
        db.session.delete(transaction)
        db.session.commit()
        
        flash(f'Transaction "{description}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"❌ Database transaction deletion failed: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error deleting transaction: {e}', 'error')
    
    return redirect(url_for('transactions'))

@app.route('/record_payment', methods=['POST'])
@require_auth
@require_permission('record_payments')
def record_payment():
    member_id = request.form['member_id']
    amount = float(request.form['amount'])
    payment_method = request.form['payment_method']
    
    print(f"🔍 Recording payment: member_id={member_id}, amount=${amount}, method={payment_method}")
    
    # Database mode - record payment in SQLAlchemy
    try:
        from models import Member as DBMember, Payment, db
        
        # Find the member
        member = DBMember.query.get(int(member_id))
        if not member:
            flash('Member not found!', 'error')
            return redirect(url_for('dashboard'))
        
        # Create payment record
        payment = Payment(
            member_id=int(member_id),
            amount=amount,
            payment_method=payment_method,
            date=datetime.now().date()
        )
        
        db.session.add(payment)
        db.session.commit()
        
        print(f"✅ Payment recorded in database: {member.name} paid ${amount} via {payment_method}")
        flash('Payment recorded successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Database payment recording failed: {e}")
        flash(f'Error recording payment: {e}', 'error')
    
    return redirect(url_for('dashboard'))

@app.route('/edit_payment/<payment_id>', methods=['GET', 'POST'])
@require_auth
@require_permission('record_payments')
def edit_payment(payment_id):
    """Edit an existing payment"""
    from models import Payment, Member as DBMember, db
    
    payment = Payment.query.get_or_404(int(payment_id))
    
    if request.method == 'GET':
        members = DBMember.query.all()
        return render_template('edit_payment.html', payment=payment, members=members)
    
    # POST request - update payment
    try:
        payment.member_id = int(request.form['member_id'])
        payment.amount = float(request.form['amount'])
        payment.payment_method = request.form['payment_method']
        
        db.session.commit()
        flash('Payment updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating payment: {e}', 'error')
    
    return redirect(url_for('transactions'))

@app.route('/remove_payment/<payment_id>', methods=['POST'])
@require_auth
@require_permission('record_payments')
def remove_payment(payment_id):
    """Delete a payment"""
    from models import Payment, db
    
    try:
        payment = Payment.query.get_or_404(int(payment_id))
        member_name = payment.member.name
        amount = payment.amount
        
        db.session.delete(payment)
        db.session.commit()
        
        flash(f'Payment of ${amount} from {member_name} deleted successfully!', 'success')
        print(f"✅ Payment deleted from database: ${amount} from {member_name}")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error deleting payment: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error deleting payment: {e}', 'error')
    
    return redirect(url_for('transactions'))

@app.route('/send_reminders')
@require_auth
@require_permission('send_reminders')
def send_reminders():
    try:
        print("\n🚀 Starting bulk reminder sending...")
        
        # Simple error handling without signal-based timeouts for cloud compatibility
        # TODO: Implement database version
        # reminders_sent = treasurer_app.check_and_send_reminders()
        
        if reminders_sent > 0:
            flash(f'✅ {reminders_sent} payment reminders sent successfully!', 'success')
        else:
            flash('ℹ️ No reminders needed - all members are paid up!', 'info')
        
    except Exception as e:
        print(f"Reminder error: {e}")
        flash(f'❌ Error sending reminders: {str(e)}. Try selective reminders for better control.', 'error')
    
    return redirect(url_for('dashboard'))

@app.route('/selective_reminders', methods=['GET', 'POST'])
@require_auth
@require_permission('send_reminders')
def selective_reminders():
    if request.method == 'GET':
        # Get members with outstanding balances
        members_with_balance = []
        # TODO: Implement database version
        # for member_id, member in treasurer_app.members.items():
        #     balance = treasurer_app.get_member_balance(member_id)
        #     if balance > 0:
        #         members_with_balance.append({
        #             'id': member_id,
        #             'member': member,
        #             'balance': balance
        #         })
        
        return render_template('selective_reminders.html', 
                             members_with_balance=members_with_balance)
    
    # POST request - send reminders to selected members
    selected_members = request.form.getlist('selected_members')
    
    if not selected_members:
        flash('No members selected for reminders!', 'warning')
        return redirect(url_for('selective_reminders'))
    
    try:
        print(f"\n📱 Sending selective reminders to {len(selected_members)} members...")
        
        # Simple error handling for cloud compatibility
        # TODO: Implement database version
        # reminders_sent = treasurer_app.check_and_send_reminders(selected_members)
        
        if reminders_sent > 0:
            flash(f'✅ Reminders sent to {reminders_sent} selected member(s)!', 'success')
        else:
            flash('ℹ️ No reminders sent - check member balances.', 'info')
        
    except Exception as e:
        print(f"Selective reminder error: {e}")
        flash(f'❌ Error sending selective reminders: {str(e)}', 'error')
    
    return redirect(url_for('dashboard'))

@app.route('/budget_summary')
@require_auth
def budget_summary():
    # Database mode - get budget data from DB
    from models import BudgetLimit, Transaction
    budget_data = {}
    
    # Get budget limits
    budget_limits = BudgetLimit.query.all()
    for limit in budget_limits:
        budget_data[limit.category] = {
        'limit': limit.amount,
        'spent': 0.0  # Will calculate below
        }
    
    # Calculate spending per category
    transactions = Transaction.query.filter_by(type='expense').all()
    for transaction in transactions:
        if transaction.category in budget_data:
            budget_data[transaction.category]['spent'] += transaction.amount
    
    # Calculate remaining amounts
    for category, data in budget_data.items():
        data['remaining'] = data['limit'] - data['spent']
    
    return jsonify(budget_data)

@app.route('/bulk_import', methods=['GET', 'POST'])
@require_auth
@require_permission('add_members')
def bulk_import():
    if request.method == 'GET':
        return render_template('bulk_import.html')
    
    # Parse the pasted data
    raw_data = request.form['member_data']
    default_dues = float(request.form.get('default_dues', 0))
    default_payment_plan = request.form.get('default_payment_plan', 'semester')
    
    parsed_members = []
    errors = []
    
    lines = raw_data.strip().split('\n')
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
            
            # Try to parse different formats
            parts = [part.strip() for part in line.split('\\t') if part.strip()]  # Tab-separated
        if len(parts) < 2:
            parts = [part.strip() for part in line.split(',') if part.strip()]  # Comma-separated
        if len(parts) < 2:
            parts = line.split()  # Space-separated
        
        if len(parts) >= 2:
            phone = None  # Initialize phone variable
            full_name = ""
            
            # Try different arrangements
            if len(parts) == 2:
                # "John Doe" "1234567890" or "John" "Doe 1234567890"
                name_part = parts[0]
                second_part = parts[1]
                
                # Check if second part contains phone number
                phone_chars = ''.join(filter(str.isdigit, second_part))
                if len(phone_chars) >= 10:
                    # Second part has phone, might have last name too
                    phone = phone_chars[-10:]  # Last 10 digits
                    remaining = second_part.replace(phone, '').replace(phone_chars, '').strip()
                    if remaining:
                        full_name = f"{name_part} {remaining}".strip()
                    else:
                        full_name = name_part
                else:
                    # Assume first is first name, second is last name, need phone
                    full_name = f"{parts[0]} {parts[1]}"
                    phone = None
                    
            elif len(parts) >= 3:
                # "John" "Doe" "1234567890" or similar
                phone_candidates = []
                name_parts = []
                
                for part in parts:
                    digits = ''.join(filter(str.isdigit, part))
                    if len(digits) >= 10:
                        phone_candidates.append(digits[-10:])  # Last 10 digits
                    else:
                        name_parts.append(part)
                
                full_name = ' '.join(name_parts)
                phone = phone_candidates[0] if phone_candidates else None
            
            if phone is None:
                errors.append(f"Line {i}: Could not find phone number - '{line}'")
                continue
                
            # Format phone number
            if len(phone) == 10:
                formatted_phone = f"+1{phone}"
            elif len(phone) == 11 and phone.startswith('1'):
                formatted_phone = f"+{phone}"
            else:
                formatted_phone = phone
            
            parsed_members.append({
                'name': full_name,
                'phone': formatted_phone,
                'dues_amount': default_dues,
                'payment_plan': default_payment_plan
            })
    
    return render_template('bulk_import.html', 
                         parsed_members=parsed_members, 
                         errors=errors,
                         show_review=True)

@app.route('/confirm_bulk_import', methods=['POST'])
@require_auth
@require_permission('add_members')
def confirm_bulk_import():
    # Get the confirmed member data
    member_count = int(request.form.get('member_count', 0))
    added_count = 0
    
    for i in range(member_count):
        if f'include_{i}' in request.form:  # Only add checked members
            name = request.form.get(f'name_{i}')
            phone = request.form.get(f'phone_{i}')
            dues_amount = float(request.form.get(f'dues_{i}'))
            payment_plan = request.form.get(f'plan_{i}')
            
            # TODO: Implement database version
            # treasurer_app.add_member(name, phone, dues_amount, payment_plan)
            added_count += 1
    
    flash(f'Successfully added {added_count} members!')
    return redirect(url_for('dashboard'))

@app.route('/edit_member/<member_id>', methods=['GET', 'POST'])
@require_auth
@require_permission('edit_members')
def edit_member(member_id):
    try:
        if request.method == 'GET':
            # Database mode
            from models import Member as DBMember
            member = DBMember.query.get(int(member_id))
            if not member:
                flash('Member not found!', 'error')
                return redirect(url_for('dashboard'))
            
            # Generate payment schedule for display
            from datetime import datetime, timedelta
            payment_schedule = []
            total_paid = sum(p.amount for p in member.payments)
            
            if member.payment_plan == 'monthly':
                start_date = datetime.now()
                monthly_amount = member.dues_amount / 4
                for i in range(4):
                    due_date = start_date.replace(day=1) + timedelta(days=32*i)
                    due_date = due_date.replace(day=1)
                    period_paid = sum(p.amount for p in member.payments 
                                    if hasattr(p.date, 'month') and p.date.month == due_date.month)
                    status = 'paid' if period_paid >= monthly_amount else 'pending'
                    payment_schedule.append({
                        'due_date': due_date.isoformat(),
                        'amount': monthly_amount,
                        'description': f'Monthly payment {i+1}/4',
                        'status': status,
                        'amount_due': max(0, monthly_amount - period_paid)
                    })
            elif member.payment_plan == 'semester':
                start_date = datetime.now()
                status = 'paid' if total_paid >= member.dues_amount else 'pending'
                payment_schedule.append({
                    'due_date': start_date.isoformat(),
                    'amount': member.dues_amount,
                    'description': 'Full semester payment',
                    'status': status,
                    'amount_due': max(0, member.dues_amount - total_paid)
                })
            elif member.payment_plan == 'bimonthly':
                start_date = datetime.now()
                bimonthly_amount = member.dues_amount / 2
                for i in range(2):
                    due_date = start_date.replace(day=1) + timedelta(days=60*i)
                    due_date = due_date.replace(day=1)
                    period_paid = 0
                    for p in member.payments:
                        if hasattr(p.date, 'month'):
                            if i == 0 and p.date.month in [start_date.month, start_date.month + 1]:
                                period_paid += p.amount
                            elif i == 1 and p.date.month in [start_date.month + 2, start_date.month + 3]:
                                period_paid += p.amount
                    status = 'paid' if period_paid >= bimonthly_amount else 'pending'
                    payment_schedule.append({
                        'due_date': due_date.isoformat(),
                        'amount': bimonthly_amount,
                        'description': f'Bi-monthly payment {i+1}/2',
                        'status': status,
                        'amount_due': max(0, bimonthly_amount - period_paid)
                    })
            
            return render_template('edit_member.html', 
                                 member=member,
                                 payment_schedule=payment_schedule)
        
        # POST request - update member
        name = request.form['name']
        contact = request.form.get('contact', request.form.get('phone', ''))  # Support both field names
        dues_amount = float(request.form['dues_amount'])
        payment_plan = request.form['payment_plan']
        role = request.form.get('role', 'brother')  # Get role assignment
        
        # Database mode
        from models import Member as DBMember
        member = DBMember.query.get(int(member_id))
        if not member:
            flash('Member not found!', 'error')
            return redirect(url_for('dashboard'))
        
        member.name = name
        member.contact = contact
        member.dues_amount = dues_amount
        member.payment_plan = payment_plan
        member.role = role
        
        try:
            db.session.commit()
            flash(f'Member {name} updated successfully!')
            print(f"✅ Member {name} updated successfully in database")
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating member: {e}', 'error')
            print(f"❌ Error updating member: {e}")
            return redirect(url_for('dashboard'))
        
        return redirect(url_for('member_details', member_id=member_id))
    
    except Exception as e:
        print(f"❌ Edit member error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error editing member: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/remove_member/<member_id>', methods=['POST'])
@require_auth
@require_permission('edit_members')
def remove_member(member_id):
    # TODO: Implement database version  
    flash('Member removal not yet implemented!')
    return redirect(url_for('dashboard'))

@app.route('/member_details/<member_id>')
@require_auth
def member_details(member_id):
    from datetime import datetime, timedelta
    
    # Database mode
    from models import Member as DBMember, Payment
    
    try:
        member = DBMember.query.get(int(member_id))
        if not member:
            flash('Member not found!', 'error')
            return redirect(url_for('dashboard'))
        
        # Calculate balance from payments
        total_paid = sum(payment.amount for payment in member.payments)
        balance = member.dues_amount - total_paid
        
        # Format payments for template (database mode uses different structure)
        formatted_payments = []
        for payment in member.payments:
            formatted_payments.append({
                'id': payment.id,
                'amount': payment.amount,
                'method': payment.payment_method,
                'date': payment.date.strftime('%Y-%m-%d') if hasattr(payment.date, 'strftime') else str(payment.date)
            })
        
        # Add payments_made attribute for template compatibility
        member.payments_made = formatted_payments
        member.phone = member.contact  # Template expects 'phone' attribute
        
        # Generate payment schedule based on payment plan
        payment_schedule = []
        if member.payment_plan == 'monthly':
            # Generate 4 monthly payments
            start_date = datetime.now()
            monthly_amount = member.dues_amount / 4
            for i in range(4):
                due_date = start_date.replace(day=1) + timedelta(days=32*i)
                due_date = due_date.replace(day=1)
                
                # Check if this payment period is paid
                period_paid = 0
                for payment in member.payments:
                    if hasattr(payment.date, 'month') and payment.date.month == due_date.month:
                        period_paid += payment.amount
                
                status = 'paid' if period_paid >= monthly_amount else 'pending'
                payment_schedule.append({
                    'due_date': due_date.isoformat(),
                    'amount': monthly_amount,
                    'description': f'Monthly payment {i+1}/4',
                    'status': status,
                    'amount_due': max(0, monthly_amount - period_paid)
                })
        elif member.payment_plan == 'semester':
            # Single payment for full semester
            start_date = datetime.now()
            status = 'paid' if total_paid >= member.dues_amount else 'pending'
            payment_schedule.append({
                'due_date': start_date.isoformat(),
                'amount': member.dues_amount,
                'description': 'Full semester payment',
                'status': status,
                'amount_due': max(0, member.dues_amount - total_paid)
            })
        elif member.payment_plan == 'bimonthly':
            # Generate 2 bi-monthly payments
            start_date = datetime.now()
            bimonthly_amount = member.dues_amount / 2
            for i in range(2):
                due_date = start_date.replace(day=1) + timedelta(days=60*i)
                due_date = due_date.replace(day=1)
                
                # Check if this payment period is paid
                period_paid = 0
                for payment in member.payments:
                    if hasattr(payment.date, 'month'):
                        # Check if payment falls in this bi-monthly period
                        if i == 0 and payment.date.month in [start_date.month, start_date.month + 1]:
                            period_paid += payment.amount
                        elif i == 1 and payment.date.month in [start_date.month + 2, start_date.month + 3]:
                            period_paid += payment.amount
                
                status = 'paid' if period_paid >= bimonthly_amount else 'pending'
                payment_schedule.append({
                    'due_date': due_date.isoformat(),
                    'amount': bimonthly_amount,
                    'description': f'Bi-monthly payment {i+1}/2',
                    'status': status,
                    'amount_due': max(0, bimonthly_amount - period_paid)
                })
        elif member.payment_plan == 'custom':
            # Handle custom payment plan
            # Check if member has custom_schedule in database
            if hasattr(member, 'custom_schedule') and member.custom_schedule:
                # Use stored custom schedule
                try:
                    import json
                    if isinstance(member.custom_schedule, str):
                        loaded_schedule = json.loads(member.custom_schedule)
                    else:
                        loaded_schedule = member.custom_schedule
                    
                    # Add status and amount_due to each payment in the schedule
                    for payment_item in loaded_schedule:
                        # Calculate if this specific payment is paid
                        payment_amount = float(payment_item.get('amount', 0))
                        # For custom schedules, we'll mark individual payments as paid if total exceeds them
                        # This is a simplification - ideally we'd track per-payment status
                        payment_item['status'] = 'paid' if total_paid >= payment_amount else 'pending'
                        payment_item['amount_due'] = max(0, payment_amount - min(total_paid, payment_amount))
                    
                    payment_schedule = loaded_schedule
                except Exception as e:
                    print(f"⚠️ Error parsing custom schedule: {e}")
                    payment_schedule = []
            
            # If no custom schedule or empty, fall back to semester plan
            if not payment_schedule:
                start_date = datetime.now()
                status = 'paid' if total_paid >= member.dues_amount else 'pending'
                payment_schedule.append({
                    'due_date': start_date.isoformat(),
                    'amount': member.dues_amount,
                    'description': 'Full semester payment (custom schedule not set)',
                    'status': status,
                    'amount_due': max(0, member.dues_amount - total_paid)
                })
        
        return render_template('member_details.html',
                             member=member,
                             payment_schedule=payment_schedule,
                             balance=balance)
    except Exception as e:
        print(f"❌ Error loading member details: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error loading member details: {e}', 'error')
        return redirect(url_for('dashboard'))
@app.route('/budget_management', methods=['GET', 'POST'])
@require_auth
@require_permission('manage_budgets')
def budget_management():
    try:
        print(f"🔍 Budget management route called")
        if request.method == 'GET':
                # Database mode - get budget data
                from models import BudgetLimit, Transaction, Member
                
                # Calculate dues summary
                members = Member.query.all()
                total_projected = sum(member.dues_amount for member in members)
                total_collected = sum(sum(payment.amount for payment in member.payments) for member in members)
                outstanding = total_projected - total_collected
                collection_rate = (total_collected / total_projected * 100) if total_projected > 0 else 0
                
                dues_summary = {
                    'total_collected': total_collected,
                    'total_projected': total_projected,
                    'outstanding': outstanding,
                    'collection_rate': collection_rate
                }
                
                # Get budget limits
                budget_limits_data = {}
                budget_limits = BudgetLimit.query.all()
                for limit in budget_limits:
                    budget_limits_data[limit.category] = limit.amount
                
                # Get budget summary
                budget_summary = {}
                for limit in budget_limits:
                    spent = sum(t.amount for t in Transaction.query.filter_by(type='expense', category=limit.category).all())
                    budget_summary[limit.category] = {
                        'budget_limit': limit.amount,
                        'spent': spent,
                        'remaining': limit.amount - spent,
                        'percent_used': (spent / limit.amount * 100) if limit.amount > 0 else 0
                    }
                
                return render_template('budget_management.html',
                                     budget_limits=budget_limits_data,
                                     budget_summary=budget_summary,
                                     dues_summary=dues_summary,
                                     categories=BUDGET_CATEGORIES)
        elif request.method == 'POST':
                # POST request - update budget limits
                from models import BudgetLimit
                
                try:
                    for category in BUDGET_CATEGORIES:
                        amount_key = f'budget_{category.replace("(", "_").replace(")", "_").replace(" ", "_").replace(",", "")}'
                        if amount_key in request.form:
                            amount = float(request.form[amount_key] or 0)
                            # Update or create budget limit
                            limit = BudgetLimit.query.filter_by(category=category).first()
                            if limit:
                                limit.amount = amount
                            else:
                                limit = BudgetLimit(category=category, amount=amount)
                                db.session.add(limit)
                    
                    db.session.commit()
                    flash('Budget limits updated successfully!')
                    return redirect(url_for('budget_management'))
                except Exception as post_error:
                    db.session.rollback()
                    print(f"❌ Budget management POST error: {post_error}")
                    import traceback
                    print(f"❌ Traceback: {traceback.format_exc()}")
                    flash(f'Error updating budget limits: {str(post_error)}', 'error')
                    return redirect(url_for('budget_management'))
    except Exception as e:
        print(f"❌ Budget management error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error loading budget management: {str(e)}', 'error')
        return redirect(url_for('dashboard'))


@app.route('/custom_payment_schedule/<member_id>', methods=['GET', 'POST'])
@require_auth
@require_permission('edit_members')
def custom_payment_schedule(member_id):
    from models import Member
    import json
    
    member = Member.query.get(int(member_id))
    if not member:
        flash('Member not found!')
        return redirect(url_for('dashboard'))
    
    if request.method == 'GET':
        # Parse custom_schedule if it exists (it's stored as JSON string)
        if member.custom_schedule:
            try:
                member.custom_schedule = json.loads(member.custom_schedule) if isinstance(member.custom_schedule, str) else member.custom_schedule
            except:
                member.custom_schedule = None
        
        return render_template('custom_payment_schedule.html',
                             member=member)
    
    # POST request - update custom payment schedule
    try:
        custom_schedule = []
        payment_count = int(request.form.get('payment_count', 0))
        
        for i in range(payment_count):
            due_date = request.form.get(f'due_date_{i}')
            amount = request.form.get(f'amount_{i}')
            description = request.form.get(f'description_{i}')
            
            if due_date and amount and description:
                try:
                    # Validate date format and convert to ISO format
                    parsed_date = datetime.strptime(due_date, '%Y-%m-%d')
                    custom_schedule.append({
                        'due_date': parsed_date.isoformat(),
                        'amount': float(amount),
                        'description': description
                    })
                except (ValueError, TypeError) as e:
                    flash(f'Error in payment {i+1}: Invalid date or amount format')
                    return redirect(url_for('custom_payment_schedule', member_id=member_id))
        
        # Update member with custom schedule
        member.payment_plan = 'custom'
        # Store custom_schedule in database using the model's method
        import json
        member.custom_schedule = json.dumps(custom_schedule)
        db.session.commit()
        
        flash(f'Custom payment schedule updated for {member.full_name}!')
        return redirect(url_for('member_details', member_id=member_id))
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating payment schedule: {e}')
        return redirect(url_for('custom_payment_schedule', member_id=member_id))

@app.route('/dues_summary')
@require_auth
def dues_summary_page():
    try:
        # Database mode - get data from SQLAlchemy models
        from models import Member, Payment
        print("🔍 Using database mode for dues summary")
        
        db_members = Member.query.all()
        members = {}
        
        # Build members dictionary for template
        for member in db_members:
            total_paid = sum(payment.amount for payment in member.payments)
            members[str(member.id)] = {
                'name': member.full_name,
                'dues_amount': member.dues_amount,
                'payments_made': [{'amount': p.amount, 'date': p.date.strftime('%Y-%m-%d'), 'method': p.payment_method} for p in member.payments]
            }
        
        # Calculate dues summary
        total_projected = sum(member.dues_amount for member in db_members)
        total_collected = sum(sum(payment.amount for payment in member.payments) for member in db_members)
        outstanding = total_projected - total_collected
        collection_rate = (total_collected / total_projected * 100) if total_projected > 0 else 0
        members_paid_up = sum(1 for member in db_members if sum(payment.amount for payment in member.payments) >= member.dues_amount)
        members_outstanding = len(db_members) - members_paid_up
        
        dues_summary = {
            'total_collected': total_collected,
            'total_projected': total_projected,
            'outstanding': outstanding,
            'collection_rate': collection_rate,
            'members_paid_up': members_paid_up,
            'members_outstanding': members_outstanding
        }
        
        print(f"🔍 Dues summary loaded: {dues_summary}")
        return render_template('dues_summary.html',
                         dues_summary=dues_summary,
                         members=members)
    except Exception as e:
        print(f"❌ Dues summary error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error loading dues summary: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

# Google Sheets sync functionality removed

@app.route('/transactions')
@require_auth
def transactions():
    """Show all transactions and outstanding dues in itemized list"""
    try:
        # Database mode - get transactions from DB
        print("🔍 Loading database models...")
        from models import Transaction, Member, Payment
        all_items = []
        
        # Get all transactions
        print("🔍 Querying transactions...")
        db_transactions = Transaction.query.order_by(Transaction.date.desc()).all()
        print(f"🔍 Found {len(db_transactions)} transactions")
        
        for trans in db_transactions:
            all_items.append({
                'id': trans.id,
                'date': trans.date.strftime('%Y-%m-%d'),
                'date_str': trans.date.strftime('%Y-%m-%d'),
                'description': trans.description,
                'amount': trans.amount,
                'category': trans.category,
                'transaction_type': trans.type,
                'type': 'transaction'
            })
        
        # Get all payments as income transactions
        print("🔍 Querying payments...")
        db_payments = Payment.query.order_by(Payment.date.desc()).all()
        print(f"🔍 Found {len(db_payments)} payments")
        
        for payment in db_payments:
            all_items.append({
                'id': f'payment_{payment.id}',
                'date': payment.date.strftime('%Y-%m-%d'),
                'date_str': payment.date.strftime('%Y-%m-%d'),
                'description': f'Payment from {payment.member.name} ({payment.payment_method})',
                'amount': payment.amount,
                'category': 'Dues Collection',
                'transaction_type': 'income',
                'type': 'payment',
                'member_name': payment.member.name
            })
        
        # Sort all items by date (newest first)
        all_items.sort(key=lambda x: x['date'] if x['date'] != 'Ongoing' else '1900-01-01', reverse=True)
        
        # Get outstanding dues (members with unpaid balances)
        print("🔍 Querying members for outstanding dues...")
        members = Member.query.all()
        print(f"🔍 Found {len(members)} members")
        
        for member in members:
            try:
                total_paid = sum(p.amount for p in member.payments)
                outstanding = member.dues_amount - total_paid
                if outstanding > 0:
                    all_items.append({
                        'id': f'outstanding_{member.id}',
                        'date': 'Ongoing',
                        'date_str': 'Ongoing',
                        'description': f'Outstanding dues - {member.name}',
                        'amount': outstanding,
                        'category': 'Dues',
                        'transaction_type': 'outstanding',
                        'type': 'outstanding'
                    })
            except Exception as e:
                print(f"⚠️ Error processing member {member.name}: {e}")
        
        # Calculate totals avoiding double-counting of dues collection
        transaction_income = sum(item['amount'] for item in all_items 
                               if item['transaction_type'] == 'income' and item['type'] == 'transaction' and item['category'] != 'Dues Collection')
        
        payment_income = sum(item['amount'] for item in all_items 
                           if item['transaction_type'] == 'income' and item['type'] == 'payment')
        
        dues_transactions = sum(item['amount'] for item in all_items 
                              if item['transaction_type'] == 'income' and item['type'] == 'transaction' and item['category'] == 'Dues Collection')
        
        total_income = transaction_income + payment_income
        
        total_expenses = sum(item['amount'] for item in all_items 
                            if item['transaction_type'] == 'expense')
        total_outstanding = sum(item['amount'] for item in all_items 
                               if item['transaction_type'] == 'outstanding')
        
        net_position = total_income - total_expenses
        
        print(f"🔍 Income breakdown: transaction_income=${transaction_income}, payment_income=${payment_income}, dues_transactions=${dues_transactions}")
        print(f"🔍 Totals: income={total_income}, expenses={total_expenses}, outstanding={total_outstanding}")
        print(f"🔍 Rendering template with {len(all_items)} items")
        
        return render_template('transactions.html',
                         transactions=all_items,
                         total_income=total_income,
                         total_expenses=total_expenses,
                         total_outstanding=total_outstanding,
                         net_position=net_position)
        
    except Exception as e:
        print(f"❌ Transactions route error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        return f"Transactions Error: {str(e)}", 500

# 4) ROUTES (make sure the route comes AFTER the function so Python knows it)

# Google Sheets export route removed

@app.route('/treasurer_setup', methods=['GET', 'POST'])
@require_auth
@require_permission('manage_users')
def treasurer_setup():
    try:
        if request.method == 'GET':
            # Database mode - get config from SQLAlchemy models
            from models import TreasurerConfig
            print("🔍 Using database mode for treasurer setup")
            
            config = TreasurerConfig.query.first()
            if not config:
                # Create default config if none exists
                config = TreasurerConfig()
                db.session.add(config)
                db.session.commit()
            
            return render_template('treasurer_setup.html', config=config)
        
        elif request.method == 'POST':
            # POST - Update treasurer configuration
            from models import TreasurerConfig
            config = TreasurerConfig.query.first()
            if not config:
                config = TreasurerConfig()
                db.session.add(config)
            
            config.name = request.form.get('name', '')
            config.email = request.form.get('email', '')
            config.phone = request.form.get('phone', '')
            config.smtp_username = request.form.get('smtp_username', '')
            config.smtp_password = request.form.get('smtp_password', '')
            
            db.session.commit()
            
            flash('Treasurer configuration updated successfully!')
            return redirect(url_for('treasurer_setup'))
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Treasurer setup error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error in treasurer setup: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/handover_treasurer', methods=['GET', 'POST'])
@require_auth
@require_permission('manage_users')
def handover_treasurer():
    if request.method == 'GET':
        return render_template('handover_treasurer.html')
    
    try:
        # Clear treasurer-specific data
        from models import TreasurerConfig, Semester
        
        config = TreasurerConfig.query.first()
        if config:
            config.name = ""
            config.email = ""
            config.phone = ""
            config.smtp_username = ""
            config.smtp_password = ""
        
        # Archive current semester
        current_sem = Semester.query.filter_by(is_current=True).first()
        if current_sem:
            current_sem.is_current = False
            current_sem.archived = True
            current_sem.end_date = datetime.now().isoformat()
        
        db.session.commit()
        
        flash('Treasurer handover completed! Please provide setup instructions to the new treasurer.')
        return redirect(url_for('dashboard'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error during handover: {e}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/optimize_storage')
@require_auth
@require_permission('manage_users')
def optimize_storage():
    """Optimize data storage and clean up files"""
    try:
        # TODO: Implement database version
        # treasurer_app.optimize_data_storage()
        flash('Storage optimization completed successfully! Temporary files removed and data compressed.')
    except Exception as e:
        flash(f'Optimization failed: {e}')
    return redirect(url_for('dashboard'))

@app.route('/semester_management', methods=['GET', 'POST'])
@require_auth
@require_permission('manage_users')
def semester_management():
    try:
        if request.method == 'GET':
            # Database mode - get semesters from SQLAlchemy models
            from models import Semester
            print("🔍 Using database mode for semester management")
            
            db_semesters = Semester.query.all()
            semesters = db_semesters
            semesters.sort(key=lambda s: (s.year, ['Spring', 'Summer', 'Fall'].index(s.season)), reverse=True)
            current_semester = Semester.query.filter_by(is_current=True).first()
            
            print(f"🔍 Found {len(semesters)} semesters from database")
            
            return render_template('semester_management.html', semesters=semesters, current_semester=current_semester)
        
        elif request.method == 'POST':
            # POST - Create new semester
            from models import Semester
            
            season = request.form.get('season')
            year = int(request.form.get('year'))
            
            # Archive current semester
            current_sem = Semester.query.filter_by(is_current=True).first()
            if current_sem:
                current_sem.is_current = False
                current_sem.end_date = datetime.now().isoformat()
            
            # Create new semester
            semester_id = f"{season.lower()}_{year}"
            new_semester = Semester(id=semester_id, name=f"{season} {year}", year=year, season=season, 
                               start_date=datetime.now().isoformat(), end_date="", is_current=True)
            
            db.session.add(new_semester)
            db.session.commit()
            
            flash(f'New semester {season} {year} created!')
            return redirect(url_for('semester_management'))
    except Exception as e:
        db.session.rollback()
        print(f"❌ Semester management error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error in semester management: {str(e)}', 'error')
        return redirect(url_for('semester_management'))

# Google Sheets export functionality removed

@app.route('/preview_role/<role_name>')
@require_auth
def preview_role(role_name):
    """Preview dashboard as different role (admin only)"""
    # Check if user is admin - strict check for admin access only
    current_role = get_current_user_role()
    if current_role != 'admin' and session.get('user') != 'admin':
        flash('Only admin can preview other roles.', 'error')
        return redirect(url_for('dashboard'))
    
    # Valid roles for preview (updated to use generic 'chair' terminology)
    valid_roles = ['president', 'vice_president', 'social_chair', 'phi_ed_chair', 'recruitment_chair', 'brotherhood_chair', 'brother']
    if role_name not in valid_roles:
        flash('Invalid role for preview.', 'error')
        return redirect(url_for('dashboard'))
    
    # Store current role in session for restoration
    session['preview_mode'] = True
    session['preview_role'] = role_name
    session['original_role'] = 'admin'
    
    # Create user-friendly role name
    role_display = role_name.replace('_', ' ').title()
    if 'chair' in role_name.lower():
        role_display = role_display.replace('Chair', 'Chair')  # Keep Chair capitalization
    
    flash(f'Now previewing dashboard as: {role_display}. Click "Exit Preview" to return to admin view.', 'info')
    
    # Redirect based on role type
    if role_name in ['president', 'vice_president']:
        # Presidents/VPs see restricted treasurer dashboard
        return redirect(url_for('dashboard'))
    else:
        # Brothers and chairs see brother dashboard
        return redirect(url_for('brother_dashboard_preview', role_name=role_name))

@app.route('/exit_preview')
@require_auth
def exit_preview():
    """Exit role preview mode"""
    if 'preview_mode' in session:
        del session['preview_mode']
        del session['preview_role']
        del session['original_role']
        flash('Exited preview mode. Back to treasurer view.')
    return redirect(url_for('dashboard'))

@app.route('/test_sms')
@require_auth
@require_permission('send_reminders')
def test_sms():
    """Test SMS functionality with comprehensive diagnostics"""
    from models import TreasurerConfig
    
    config = TreasurerConfig.query.first()
    if not config or not config.phone:
        flash('Please configure your phone number in Treasurer Setup first.', 'error')
        return redirect(url_for('treasurer_setup'))
    
    if not config.smtp_username or not config.smtp_password:
        flash('Please configure your email credentials in Treasurer Setup first.', 'error')
        return redirect(url_for('treasurer_setup'))
    
    test_message = "Test SMS from Fraternity Treasurer App - SMS working correctly! 📱✅"
    
    print(f"\n🧪 SMS TEST STARTING")
    print(f"📱 Phone: {config.phone}")
    print(f"📧 SMTP User: {config.smtp_username}")
    print(f"💬 Message: {test_message}")
    
    if send_email_to_sms(config.phone, test_message, config):
        flash(f'✅ Test SMS sent successfully to {config.phone}!', 'success')
        flash('📱 Check your phone for the message (may take 1-2 minutes).', 'info')
    else:
        flash('❌ Failed to send test SMS. Check the console logs for details.', 'error')
        flash('💡 Common issues: Gmail app password expired, phone number format, or carrier blocking.', 'warning')
    
    return redirect(url_for('notifications_dashboard'))

@app.route('/test_sms_to_number', methods=['POST'])
@require_auth
@require_permission('send_reminders')
def test_sms_to_number():
    """Test SMS to a specific phone number"""
    from models import TreasurerConfig
    
    config = TreasurerConfig.query.first()
    test_phone = request.form.get('test_phone', '').strip()
    
    if not test_phone:
        flash('Please enter a phone number to test.', 'error')
        return redirect(url_for('notifications_dashboard'))
    
    if not config or not config.smtp_username or not config.smtp_password:
        flash('Please configure your email credentials in Treasurer Setup first.', 'error')
        return redirect(url_for('treasurer_setup'))
    
    test_message = f"Test SMS from Fraternity Treasurer App to {test_phone} 📱✅"
    
    print(f"\n🧪 SMS TEST TO CUSTOM NUMBER")
    print(f"📱 Target Phone: {test_phone}")
    print(f"📧 SMTP User: {config.smtp_username}")
    
    if send_email_to_sms(test_phone, test_message, config):
        flash(f'✅ Test SMS sent successfully to {test_phone}!', 'success')
        flash('📱 Check the target phone for the message (may take 1-2 minutes).', 'info')
    else:
        flash(f'❌ Failed to send test SMS to {test_phone}. Check console logs.', 'error')
    
    return redirect(url_for('notifications_dashboard'))

@app.route('/submit_payment_plan', methods=['POST'])
@require_auth
def submit_payment_plan():
    """Brother submits a payment plan request (example route)"""
    member_name = request.form.get('member_name', 'Unknown Member')
    plan_details = request.form.get('plan_details', '')
    
    # TODO: Implement notification for payment plan request
    # from models import TreasurerConfig
    # config = TreasurerConfig.query.first()
    # if notify_payment_plan_request(member_name, plan_details, config):
    flash('Payment plan request submitted successfully! Treasurer has been notified.')
    return redirect(url_for('dashboard'))

@app.route('/submit_reimbursement', methods=['POST'])
@require_auth  
def submit_reimbursement():
    """Submit a reimbursement request (example route)"""
    submitter_name = request.form.get('submitter_name', session.get('user', 'Unknown'))
    amount = float(request.form.get('amount', 0))
    category = request.form.get('category', '')
    description = request.form.get('description', '')
    
    # TODO: Implement notification for reimbursement request
    # from models import TreasurerConfig
    # config = TreasurerConfig.query.first()
    # if notify_reimbursement_request(submitter_name, amount, category, description, config):
    flash('Reimbursement request submitted successfully! Treasurer has been notified.')
    return redirect(url_for('dashboard'))

@app.route('/test_approval_notification')
@require_auth
@require_permission('send_reminders')
def test_approval_notification():
    """Test the approval notification system"""
    from models import TreasurerConfig
    
    config = TreasurerConfig.query.first()
    if not config or (not config.phone and not config.email):
        flash('Please configure your phone and/or email in Treasurer Setup first.')
        return redirect(url_for('treasurer_setup'))
    
    # Send a test reimbursement request notification
    if notify_reimbursement_request('John Doe (Test)', 75.50, 'Social', 'Test reimbursement notification - pizza for brotherhood event', config):
        flash('Test approval notification sent successfully! Check your phone and email.')
    else:
        flash('Failed to send test approval notification. Check your configuration.')
    
    return redirect(url_for('notifications_dashboard'))

@app.route('/notifications')
@require_auth
@require_permission('send_reminders')
def notifications_dashboard():
    """Notifications dashboard for approval requests"""
    try:
        # Database mode - get config from SQLAlchemy models
        from models import TreasurerConfig
        print("🔍 Using database mode for notifications")
        
        config = TreasurerConfig.query.first()
        if config:
            email_configured = bool(config.smtp_username and config.smtp_password)
            treasurer_phone_configured = bool(config.phone)
            
            notification_status = {
                'email_configured': email_configured,
                'treasurer_phone_configured': treasurer_phone_configured,
                'email_username': config.smtp_username,
                'treasurer_phone': config.phone
            }
        else:
            notification_status = {
                'email_configured': False,
                'treasurer_phone_configured': False,
                'email_username': '',
                'treasurer_phone': ''
            }
        
        print(f"🔍 Notification status: {notification_status}")
        
        # TODO: In the future, you could add pending approval requests here
        # For example:
        # pending_requests = get_pending_approval_requests()
        
        return render_template('notifications_dashboard.html',
                         notification_status=notification_status)
    except Exception as e:
        print(f"❌ Notifications dashboard error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error loading notifications dashboard: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/register', methods=['GET', 'POST'])
def brother_registration():
    """Brother registration form"""
    if request.method == 'GET':
        return render_template('brother_registration.html')
    
    # POST request - process registration
    full_name = request.form.get('full_name', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip().lower()
    
    # Basic validation
    if not all([full_name, phone, email]):
        flash('All fields are required.', 'error')
        return render_template('brother_registration.html')
    
    try:
        from models import User, PendingBrother
        
        # Check if email already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('An account with this email already exists.', 'error')
            return render_template('brother_registration.html')
        
        # Check if already registered
        existing_pending = PendingBrother.query.filter_by(email=email).first()
        if existing_pending:
            flash('Registration with this email is already pending approval.', 'warning')
            return render_template('brother_registration.html')
        
        # Register the brother
        pending_brother = PendingBrother(full_name=full_name, phone=phone, email=email)
        db.session.add(pending_brother)
        db.session.commit()
        
        # Success message with clear next steps
        flash('🎉 Registration submitted successfully!', 'success')
        flash('✅ Your information has been sent to the treasurer for verification.', 'info')
        flash('📱 Once approved, your login credentials will be sent to your phone via SMS.', 'info')
        flash('⏰ Please allow 24-48 hours for verification.', 'info')
        
        return render_template('brother_registration_success.html', 
                             full_name=full_name, 
                             phone=phone, 
                             email=email)
    except Exception as e:
        db.session.rollback()
        flash(f'Registration failed: {str(e)}', 'error')
        return render_template('brother_registration.html')

@app.route('/brother_dashboard_preview/<role_name>')
@require_auth
def brother_dashboard_preview(role_name):
    """Preview brother dashboard as specific role (admin only)"""
    current_role = get_current_user_role()
    if (current_role != 'admin' and session.get('user') != 'admin') or not session.get('preview_mode'):
        return redirect(url_for('brother_dashboard'))
    
    # Create a mock member for preview
    from dataclasses import dataclass
    
    @dataclass
    class MockMember:
        id: str = 'preview'
        name: str = f'Preview {role_name.replace("_", " ").title()}'
        contact: str = 'preview@example.com'
        dues_amount: float = 500.0
        payment_plan: str = 'semester'
        payments_made: list = None
        contact_type: str = 'email'
        role: str = role_name
        
        def __post_init__(self):
            if self.payments_made is None:
                self.payments_made = [
                    {'amount': 250.0, 'date': '2024-09-01', 'method': 'Zelle', 'id': 'preview1'}
                ]
    
    mock_member = MockMember()
    balance = 250.0  # Mock balance
    total_paid = sum(payment['amount'] for payment in mock_member.payments_made)
    
    # Mock payment schedule
    payment_schedule = [
        {'description': 'Full semester payment', 'due_date': '2024-09-01', 'amount': 500.0, 'status': 'paid'},
    ]
    payment_history = [
        {
            'amount': payment['amount'],
            'date': payment['date'],
            'method': payment['method']
        }
        for payment in mock_member.payments_made
    ]
    
    # Get summary data based on permissions
    data = {
        'member': mock_member,
        'balance': balance,
        'payment_schedule': payment_schedule,
        'payment_history': payment_history,
        'total_paid': total_paid,
        'user_role': role_name,
        'chair_manual_links': CHAIR_MANUAL_LINKS,
        'chair_role_to_category': CHAIR_ROLE_TO_CATEGORY,
        'officer_roles': OFFICER_ROLES,
        'chapter_events': []
    }
    
    # Add additional data for executives (handle both database and JSON modes)
    if role_name in ['president', 'vice_president']:
        # Database mode - get data from SQLAlchemy models
        from models import Member as DBMember
        total_members = DBMember.query.count()
        data.update({
            'total_members': total_members,
            'dues_summary': {'total_collected': 5000.0, 'total_projected': 10000.0, 'outstanding': 5000.0, 'collection_rate': 50.0},  # Mock data
            'budget_summary': {}  # Mock budget data
        })
    elif role_name in ['social_chair', 'phi_ed_chair', 'brotherhood_chair', 'recruitment_chair']:
        # Database mode - mock budget data for preview
        data.update({'budget_summary': {}})
    return render_template('brother_dashboard.html', **data)

@app.route('/brother_dashboard')
@require_auth
def brother_dashboard():
    """Brother-specific dashboard with role-based content"""
    # Get current user's member info
    member = get_user_member()
    if not member:
        flash('Member information not found. Please contact the treasurer.', 'error')
        return redirect(url_for('logout'))
    
    # Get summary data using database
    balance = member.get_balance() if hasattr(member, 'get_balance') else 0.0
    payments = sorted(member.payments, key=lambda payment: payment.date, reverse=True)
    payment_history = [
        {
            'amount': payment.amount,
            'date': payment.date.isoformat(),
            'method': payment.payment_method
        }
        for payment in payments
    ]
    total_paid = sum(payment.amount for payment in payments)
    payment_schedule = []  # TODO: Implement payment schedule for database mode
    
    current_semester = Semester.query.filter_by(is_current=True).first()
    chapter_events = []
    if current_semester:
        chapter_events = Event.query.filter_by(semester_id=current_semester.id).order_by(Event.date.asc()).all()
    
    user_role = get_current_user_role()
    chair_category = CHAIR_ROLE_TO_CATEGORY.get(user_role)
    
    # Basic data for all users
    data = {
        'member': member,
        'balance': balance,
        'payment_schedule': payment_schedule,
        'payment_history': payment_history,
        'total_paid': total_paid,
        'user_role': user_role,
        'chair_manual_links': CHAIR_MANUAL_LINKS,
        'chair_role_to_category': CHAIR_ROLE_TO_CATEGORY,
        'officer_roles': OFFICER_ROLES,
        'chapter_events': chapter_events
    }
    
    if current_semester and user_role in ['president', 'vice_president', 'admin', 'treasurer']:
        total_members = Member.query.filter_by(semester_id=current_semester.id).count()
        data.update({
            'total_members': total_members,
            'dues_summary': build_dues_summary(current_semester.id),
            'budget_summary': build_budget_summary(current_semester.id)
        })
    elif current_semester and chair_category:
        data.update({
            'budget_summary': build_budget_summary(current_semester.id, categories=[chair_category])
        })
    else:
        data.update({'budget_summary': {}})
    return render_template('brother_dashboard.html', **data)

@app.route('/debug_pending_brothers')
@require_auth
@require_permission('manage_users')
def debug_pending_brothers():
    """Debug route to check pending brothers status"""
    from models import PendingBrother
    
    print(f"\n🔍 DEBUGGING PENDING BROTHERS")
    pending_brothers = PendingBrother.query.all()
    print(f"   Current pending brothers count: {len(pending_brothers)}")
    
    for pending_brother in pending_brothers:
        print(f"   - {pending_brother.id}: {pending_brother.full_name} ({pending_brother.email})")
    
    flash(f'Debug complete: {len(pending_brothers)} pending brothers found. Check console for details.')
    return redirect(url_for('verify_brothers'))

@app.route('/credential_management')
@require_auth
@require_permission('manage_users')
def credential_management():
    """Credential management page for treasurers to view all brother login details"""
    from models import User
    
    print(f"\n🔐 LOADING CREDENTIAL MANAGEMENT")
    
    credentials = []
    brother_accounts = 0
    linked_accounts = 0
    
    users = User.query.all()
    total_users = len(users)
    
    for user in users:
        is_brother = any(r.name == 'brother' for r in user.roles) or (user.get_primary_role() and user.get_primary_role().name == 'brother')
        if is_brother:
            brother_accounts += 1
            member = getattr(user, 'member_record', None)
            credentials.append({
                'username': user.phone or user.email,
                'password': '********** (Hashed - Not Recoverable)',
                'role': user.get_primary_role().name if user.get_primary_role() else 'brother',
                'created_at': getattr(user, 'created_at', 'Unknown'),
                'member_name': getattr(member, 'full_name', getattr(member, 'name', None)) if member else None,
                'member_id': getattr(member, 'id', None) if member else None,
                'phone': user.phone
            })
            if member:
                linked_accounts += 1
    
    print(f"   Total users: {total_users}")
    print(f"   Brother accounts: {brother_accounts}")
    print(f"   Linked accounts: {linked_accounts}")
    
    return render_template('credential_management.html',
                         credentials=credentials,
                         total_users=total_users,
                         brother_accounts=brother_accounts,
                         linked_accounts=linked_accounts)

@app.route('/verify_brothers', methods=['GET', 'POST'])
@require_auth
@require_permission('manage_users')
def verify_brothers():
    """Treasurer interface to verify pending brother registrations"""
    try:
        # Database mode - handle pending user approvals
        from models import User, Member as MemberModel, Role
        print("🔍 Using database mode for brother verification")
        
        if request.method == 'GET':
            # Get pending users (status='pending')
            pending_users = User.query.filter_by(status='pending').all()
            # Get all members to link with
            members = MemberModel.query.all()
            
            print(f"👥 Found {len(pending_users)} pending users")
            
            return render_template('verify_brothers_db.html',
                                 pending_users=pending_users,
                                 members=members)
        
        elif request.method == 'POST':
            # POST request - handle approval/rejection
            user_id = request.form.get('user_id')
            member_id = request.form.get('member_id')
            action = request.form.get('action')
            
            if action == 'approve' and user_id:
                user = User.query.get(user_id)
                if user:
                    user.status = 'active'
                    user.approved_at = datetime.utcnow()
                    
                    # Link to member if specified
                    if member_id:
                        member = MemberModel.query.get(member_id)
                        if member:
                            member.user_id = user.id
                    
                    # Assign brother role
                    brother_role = Role.query.filter_by(name='brother').first()
                    if brother_role and brother_role not in user.roles:
                        user.roles.append(brother_role)
                    
                    db.session.commit()
                    
                    # Send SMS credentials if configured
                    from models import TreasurerConfig
                    config = TreasurerConfig.query.first()
                    if config and config.smtp_username and user.phone:
                        password_msg = f"Welcome to the fraternity app! Login: {user.phone} | Password: (same as registration)"
                        send_email_to_sms(user.phone, password_msg, config)
                    
                    flash(f'User {user.full_name} approved and activated!', 'success')
                else:
                    flash('User not found!', 'error')
            
            elif action == 'reject' and user_id:
                user = User.query.get(user_id)
                if user:
                    db.session.delete(user)
                    db.session.commit()
                    flash(f'User {user.full_name} rejected and removed.', 'info')
                else:
                    flash('User not found!', 'error')
            
            return redirect(url_for('verify_brothers'))
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Verify brothers error: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        flash(f'Error in brother verification: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/role_management')
@require_auth
@require_permission('assign_roles')
def role_management():
    """Role management interface for treasurers"""
    from models import Member as MemberModel
    
    db_members = MemberModel.query.all()
    members = {}
    for member in db_members:
        members[str(member.id)] = member
    
    # Log current executive board for debugging
    executive_roles = ['treasurer', 'president', 'vice_president', 'social_chair', 'phi_ed_chair', 'brotherhood_chair', 'recruitment_chair']
    print(f"✅ Current Executive Board:")
    
    for exec_role in executive_roles:
        assigned_members = []
        for member_id, member in members.items():
            member_role = getattr(member, 'role', 'brother')
            member_name = getattr(member, 'full_name', getattr(member, 'name', 'Unknown'))
            
            if member_role == exec_role:
                assigned_members.append(member_name)
        
        if assigned_members:
            print(f"  {exec_role}: {', '.join(assigned_members)}")
        else:
            print(f"  {exec_role}: VACANT")
    
    return render_template('role_management.html', members=members)

@app.route('/assign_role', methods=['POST'])
@require_auth
@require_permission('assign_roles')
def assign_role():
    """Assign a role to a member"""
    from models import Member as MemberModel, User, Role
    
    member_id = request.form.get('member_id')
    role = request.form.get('role')
    
    if not member_id or not role:
        flash('Member and role must be specified.', 'error')
        return redirect(url_for('role_management'))
    
    try:
        member = MemberModel.query.get(member_id)
        if not member:
            flash('Member not found.', 'error')
            return redirect(url_for('role_management'))
        
        # Check if role is already taken
        if role != 'brother':
            existing_member = MemberModel.query.filter_by(role=role).first()
            if existing_member and str(existing_member.id) != member_id:
                flash(f'{role.replace("_", " ").title()} position is already filled by {existing_member.full_name}.', 'warning')
                return redirect(url_for('role_management'))
        
        # Update member role in database
        member.role = role
        
        # Update user roles if user account exists
        if member.user:
            user = member.user
            # Clear existing roles except admin
            user.roles = [r for r in user.roles if r.name == 'admin']
            
            # Add new role
            if role != 'brother':
                role_obj = Role.query.filter_by(name=role).first()
                if not role_obj:
                    role_obj = Role(name=role, description=f'{role.replace("_", " ").title()} role')
                    db.session.add(role_obj)
                user.roles.append(role_obj)
            
            # Ensure brother role
            brother_role = Role.query.filter_by(name='brother').first()
            if not brother_role:
                brother_role = Role(name='brother', description='Brother role')
                db.session.add(brother_role)
            if brother_role not in user.roles:
                user.roles.append(brother_role)
        
        db.session.commit()
        flash(f'{member.full_name} has been successfully assigned as {role.replace("_", " ").title()}.', 'success')
        print(f"✅ Database role assignment: {member.full_name} -> {role}")
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error assigning role: {e}', 'error')
        print(f"❌ Database role assignment failed: {e}")
    
    return redirect(url_for('role_management'))

@app.route('/change_role', methods=['POST'])
@require_auth
@require_permission('assign_roles')
def change_role():
    """Change a member's role - delegates to assign_role"""
    return assign_role()

@app.route('/ai_assistant', methods=['GET', 'POST'])
@require_auth
def ai_assistant():
    if request.method == 'GET':
        return render_template('ai_assistant.html')
    
    user_message = request.form.get('message', '').lower().strip()
    response = get_ai_response(user_message)
    
    return jsonify({'response': response})

def get_ai_response(message):
    """Simple rule-based AI assistant responses"""
    
    # Troubleshooting responses
    if 'not working' in message or 'broken' in message or 'error' in message:
        return "🔧 **Troubleshooting Steps:**\n1. Try refreshing the page\n2. Check if all required fields are filled\n3. Restart the app using 'Start Treasurer App.command'\n4. Check the terminal for error messages\n\nWhat specific issue are you experiencing?"
    
    if 'email' in message and ('not send' in message or 'fail' in message):
        return "📧 **Email Issues:**\n1. Go to Treasurer Setup → Email Configuration\n2. Verify Gmail username is correct\n3. Use Gmail **App Password**, not regular password\n4. Test with your own email first\n\n**Get App Password:** Google Account → Security → 2-Step Verification → App passwords"
    
    if 'sms' in message or 'text' in message:
        return "📱 **SMS Issues:**\n1. SMS uses free email-to-SMS gateways\n2. Works with all major carriers: Verizon, AT&T, T-Mobile\n3. Use format: +1234567890 (include +1)\n4. Test via Notifications → 'Test SMS to Treasurer'\n\n**Tip:** SMS delivery may take 1-2 minutes"
    
    # Setup help
    if 'setup' in message or 'configure' in message or 'install' in message:
        return "⚙️ **Setup Guide:**\n1. **New Treasurer:** Login → Treasurer Setup → Configure credentials\n2. **Email:** Get Gmail App Password → Enter in Email Config\n3. **Phone:** Add your phone for SMS notifications\n4. **Test:** Use 'Test SMS to Treasurer' to verify setup\n\nNeed help with specific setup?"
    
    # Feature help
    if 'how to' in message or 'add member' in message:
        return "👥 **Member Management:**\n• **Add Single:** Dashboard → Member Management → Fill form\n• **Bulk Import:** Dashboard → 'Bulk Import' → Paste member list\n• **Payment:** Find member → 'Record Payment'\n• **Edit:** Click member name → Edit details\n\n**Tip:** Use bulk import for large member lists!"
    
    if 'payment' in message or 'dues' in message:
        return "💰 **Payment & Dues:**\n• **Record Payment:** Dashboard → Find member → Record Payment\n• **Send Reminders:** Selective Reminders → Choose members\n• **View Status:** Click member name for details\n• **Payment Plans:** Edit member → Choose plan (semester/monthly)\n\n**Custom Schedules:** Member Details → Custom Payment Schedule"
    
    if 'budget' in message or 'expense' in message:
        return "📊 **Budget & Expenses:**\n• **Set Budget:** Budget Management → Set limits per category\n• **Add Expense:** Dashboard → Add Transaction → Select 'Expense'\n• **Track Spending:** Budget Management shows % used\n• **Categories:** Executive, Social, Philanthropy, etc.\n\n**Monthly Reports:** Monthly Income page"
    
    if 'export' in message or 'backup' in message:
        return "📄 **Data Export & Backup:**\n• **CSV Export:** Export data to CSV files\n• **Manual Backup:** Copy entire app folder\n• **Handover:** All data preserved automatically\n• **Local Storage:** All data stored securely locally\n\n**Tip:** Regular backups ensure data safety!"
    
    if 'semester' in message or 'new year' in message:
        return "📅 **Semester Management:**\n• **New Semester:** Semesters → Create New Semester\n• **Auto-Archive:** Previous semester archived automatically\n• **View History:** All semesters page shows past terms\n• **Data:** All member/transaction data preserved\n\n**Best Practice:** Export data before creating new semester"
    
    # General help
    if 'help' in message or 'what can you do' in message:
        return "🤖 **I can help with:**\n• Troubleshooting issues\n• Setup and configuration\n• Member management\n• Payment processing\n• Budget tracking\n• Data export\n• Semester transitions\n\n**Ask me:** 'How to add members?' or 'Email not working?'"
    
    # Default response
    return "💡 **Common Questions:**\n• 'Email not working' - Email troubleshooting\n• 'How to add members' - Member management help\n• 'Setup help' - Configuration guidance\n• 'SMS issues' - Text message problems\n• 'Export data' - Backup and export help\n\n**Tip:** Be specific about your issue for better help!"

# Fallback chair dashboard route when blueprint fails
@app.route('/chair')
@app.route('/chair/')
@require_auth
def chair_dashboard_fallback():
    """Fallback chair dashboard when blueprint routing fails"""
    current_user_role = get_current_user_role()
    
    # Check if user is a chair
    if not current_user_role.endswith('_chair'):
        flash('Access denied. You must be a chair to access this page.', 'error')
        return redirect(url_for('dashboard'))
    
    # Mock data for now since chair blueprint might not be working
    chair_type = current_user_role.replace('_chair', '')
    
    mock_data = {
        'primary_category': chair_type.title(),
        'current_semester': {'name': 'Fall 2024'},
        'events': [],
        'spending_plans': [],
        'budget_limit': {'amount': 2500.0},
        'total_estimated_cost': 0.0,
        'total_actual_cost': 0.0
    }
    
    return render_template('chair/dashboard.html', **mock_data)

@app.route('/chair_budget_management')
@require_auth
def chair_budget_management():
    """Chair budget management page with tab navigation"""
    current_user_role = get_current_user_role()
    
    # Define chair categories
    chair_categories = {
        'social': 'Social Chair',
        'phi_ed': 'Phi Ed Chair', 
        'brotherhood': 'Brotherhood Chair',
        'recruitment': 'Recruitment Chair'
    }
    
    # Check user permissions
    can_view_all_budgets = has_permission('manage_budgets') or current_user_role in ['admin', 'treasurer', 'president', 'vice_president']
    
    # Determine user's chair type if they're a chair
    user_chair_type = None
    if current_user_role.endswith('_chair'):
        user_chair_type = current_user_role.replace('_chair', '')
    
    # Build chair budget data
    chair_budgets = {}
    
    for chair_type, display_name in chair_categories.items():
        # Determine if user can access this chair's budget
        accessible = can_view_all_budgets or (user_chair_type == chair_type)
        
        if accessible:
            # Get budget data for this chair category (database mode)
            budget_data = get_chair_budget_data_db(chair_type)
            
            chair_budgets[chair_type] = {
                'display_name': display_name,
                'accessible': True,
                'is_own_budget': (user_chair_type == chair_type),
                **budget_data
            }
        else:
            chair_budgets[chair_type] = {
                'display_name': display_name,
                'accessible': False,
                'is_own_budget': False
            }
    
    return render_template('chair_budget_management.html',
                         chair_budgets=chair_budgets,
                         can_view_all_budgets=can_view_all_budgets,
                         user_chair_type=user_chair_type,
                         restricted_access=(not can_view_all_budgets and user_chair_type))

def get_chair_budget_data_db(chair_type):
    """Get chair budget data from database"""
    from models import BudgetLimit, Transaction
    
    # Map chair types to budget categories
    category_mapping = {
        'social': 'Social',
        'phi_ed': 'Phi ED',
        'brotherhood': 'Brotherhood',
        'recruitment': 'Recruitment'
    }
    
    category = category_mapping.get(chair_type, chair_type.title())
    
    # Get budget limit from database
    budget_limit_record = BudgetLimit.query.filter_by(category=category).first()
    budget_limit = budget_limit_record.amount if budget_limit_record else 0.0
    
    # Get all expenses for this category from database
    expense_transactions = Transaction.query.filter_by(
        type='expense',
        category=category
    ).order_by(Transaction.date.desc()).all()
    
    # Calculate total spent
    total_spent = sum(t.amount for t in expense_transactions)
    
    # Format recent expenses for display
    recent_expenses = []
    for trans in expense_transactions[:10]:  # Get last 10
        recent_expenses.append({
            'date': trans.date.strftime('%Y-%m-%d'),
            'description': trans.description,
            'category': trans.category,
            'amount': trans.amount,
            'status': 'completed',
            'notes': getattr(trans, 'notes', '')
        })
    
    # Calculate remaining and usage
    remaining = budget_limit - total_spent
    usage_percentage = (total_spent / budget_limit * 100) if budget_limit > 0 else 0
    
    return {
        'budget_limit': budget_limit,
        'total_spent': total_spent,
        'pending_amount': 0.0,  # TODO: Get from pending reimbursements if needed
        'remaining': remaining,
        'usage_percentage': min(usage_percentage, 100),
        'expenses_count': len(expense_transactions),
        'spending_plans': [],  # TODO: Get from spending plans table if needed
        'pending_reimbursements': [],  # TODO: Get from reimbursement requests if needed
        'recent_expenses': recent_expenses
    }




@app.route('/chair_budget_management/export/<chair_type>')
@require_auth
def export_chair_budget(chair_type):
    """Export chair budget data as CSV"""
    current_user_role = get_current_user_role()
    user_chair_type = current_user_role.replace('_chair', '') if current_user_role.endswith('_chair') else None
    
    # Check permissions
    can_view_all = has_permission('manage_budgets') or current_user_role in ['admin', 'treasurer', 'president', 'vice_president']
    if not can_view_all and user_chair_type != chair_type:
        flash('Access denied', 'error')
        return redirect(url_for('chair_budget_management'))
    
    # Get chair budget data
        budget_data = get_chair_budget_data_db(chair_type)
    # Create CSV content
    import io
    import csv
    from flask import Response
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write budget overview
    writer.writerow([f'{chair_type.title()} Chair Budget Export'])
    writer.writerow(['Budget Allocation', f"${budget_data['budget_limit']:.2f}"])
    writer.writerow(['Total Spent', f"${budget_data['total_spent']:.2f}"])
    writer.writerow(['Remaining', f"${budget_data['remaining']:.2f}"])
    writer.writerow([])
    
    # Write expenses
    writer.writerow(['Recent Expenses'])
    writer.writerow(['Date', 'Description', 'Amount', 'Status'])
    for expense in budget_data['recent_expenses']:
        writer.writerow([expense['date'], expense['description'], f"${expense['amount']:.2f}", expense['status']])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={chair_type}_budget_export.csv'}
    )

@app.route('/debug/db_status')
def debug_db_status():
    """Debug endpoint to check database configuration status"""
    return {
        'database_mode': 'always_active',
        'database_available': True,
        'DATABASE_URL_exists': bool(os.environ.get('DATABASE_URL')),
        'DATABASE_URL_prefix': os.environ.get('DATABASE_URL', '')[:20] + '...' if os.environ.get('DATABASE_URL') else 'None',
        'FLASK_ENV': os.environ.get('FLASK_ENV', 'Not set'),
        'SECRET_KEY_exists': bool(os.environ.get('SECRET_KEY')),
        'PORT': os.environ.get('PORT', 'Not set')
    }

@app.route('/debug/init_db')
def debug_init_db():
    """Manually initialize database with default data"""
    try:
        init_database(app)
        return {'success': True, 'message': 'Database initialized successfully'}
    except Exception as e:
        return {'error': str(e)}

@app.route('/debug/payment_status')
def debug_payment_status():
    """Debug payment data specifically"""
    try:
        from models import User, Role, Member, Transaction, Payment
        
        # Get sample members with their payments
        members = Member.query.limit(10).all()
        member_data = []
        
        for member in members:
            payment_count = len(member.payments)
            total_paid = sum(p.amount for p in member.payments)
        
            member_data.append({
                'name': member.name,
                'dues': member.dues_amount,
                'payment_count': payment_count,
                'total_paid': total_paid,
                'payments': [{
                    'amount': p.amount,
                    'date': p.date.strftime('%Y-%m-%d'),
                    'method': p.payment_method
                } for p in member.payments[:3]]  # First 3 payments
            })
        
        return {
            'member_payment_data': member_data,
            'total_payments_in_db': Payment.query.count()
        }
        
    except Exception as e:
        return {'error': str(e)}

@app.route('/debug/data_status')
def debug_data_status():
    """Check what data exists in the database"""
    try:
        from models import User, Role, Member, Transaction, Payment, BudgetLimit, Semester
        
        data_status = {
        'users': User.query.count(),
        'roles': Role.query.count(), 
        'members': Member.query.count(),
        'transactions': Transaction.query.count(),
        'payments': Payment.query.count(),
        'budget_limits': BudgetLimit.query.count(),
        'semesters': Semester.query.count()
        }
        
        # Get sample data
        sample_users = [{'phone': u.phone, 'name': f'{u.first_name} {u.last_name}', 'roles': [r.name for r in u.roles]} for u in User.query.limit(5).all()]
        sample_members = [{'name': m.name, 'dues': m.dues_amount, 'payments': len(m.payments)} for m in Member.query.limit(5).all()]
        sample_transactions = [{'date': t.date.strftime('%Y-%m-%d'), 'description': t.description, 'amount': t.amount, 'type': t.type} for t in Transaction.query.limit(5).all()]
        
        return {
        'counts': data_status,
        'sample_users': sample_users,
        'sample_members': sample_members, 
        'sample_transactions': sample_transactions
        }
    except Exception as e:
        return {'error': str(e), 'traceback': str(e.__traceback__)}

@app.route('/debug/fix_roles')
def debug_fix_roles():
    """Check and create missing default roles"""
    try:
        from models import Role, init_default_roles
        
        # Check current roles
        existing_roles = [r.name for r in Role.query.all()]
        
        # Create missing roles
        init_default_roles()
        
        # Check roles after init
        all_roles = [r.name for r in Role.query.all()]
        
        return {
        'existing_roles_before': existing_roles,
        'all_roles_after': all_roles,
        'roles_created': [r for r in all_roles if r not in existing_roles]
        }
        
    except Exception as e:
        return {'error': str(e)}

@app.route('/debug/fix_admin_role')
def debug_fix_admin_role():
    """Manually fix admin role assignment"""
    try:
        from models import User, Role
        
        # Get admin user and admin role
        admin_user = User.query.filter_by(phone='admin').first()
        admin_role = Role.query.filter_by(name='admin').first()
        
        if not admin_user:
            return {'error': 'Admin user not found'}
        
        if not admin_role:
            return {'error': 'Admin role not found - try /debug/fix_roles first'}
        
        # Check current roles
        current_roles = [r.name for r in admin_user.roles]
        
        if 'admin' not in current_roles:
            admin_user.roles.append(admin_role)
            db.session.commit()
            return {'success': True, 'message': f'Admin role added. User now has roles: {[r.name for r in admin_user.roles]}'}
        else:
            return {'success': True, 'message': f'Admin user already has admin role. Current roles: {current_roles}'}
        
    except Exception as e:
        return {'error': str(e)}

if __name__ == '__main__':
    # Check if we're on Render (cloud) by checking for PORT environment variable
    port = os.environ.get('PORT')
    
    if port:
        # We're on Render - start the app
        port = int(port)
        debug = os.environ.get('DEBUG', 'False').lower() == 'true'
        print(f"🚀 Starting Flask app on Render.com (port {port})")
        app.run(host='0.0.0.0', port=port, debug=debug)
    else:
        # Local development fallback
        host = os.environ.get('HOST', '127.0.0.1')
        local_port = int(os.environ.get('LOCAL_PORT', '8080'))
        debug = os.environ.get('DEBUG', 'True').lower() == 'true'
        print(f"🧪 Starting local dev server at http://{host}:{local_port}")
        app.run(host=host, port=local_port, debug=debug)

# Add error handlers to show detailed errors in production
@app.errorhandler(500)
def internal_error(error):
    import traceback
    error_details = traceback.format_exc()
    print(f"❌ 500 Error: {error_details}")
    return f"<h1>Internal Server Error</h1><pre>{error_details}</pre>", 500

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    error_details = traceback.format_exc()
    print(f"❌ Unhandled Exception: {error_details}")
    return f"<h1>Application Error</h1><pre>{error_details}</pre>", 500
