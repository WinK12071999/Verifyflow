"""
VerifyFlow — beginner-friendly compliance document tracker (MVP).

This single file contains the Flask app, database models, forms, helpers,
routes, and CLI commands so a new learner can open one place and follow along.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import smtplib
import uuid
from datetime import date, datetime, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect, FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from pydantic import BaseModel, ValidationError, field_validator
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    Length,
    NumberRange,
    Optional as OptionalField,
    ValidationError as WTFormsValidationError,
)

# ---------------------------------------------------------------------------
# Paths and environment
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verifyflow")

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB

DOCUMENT_CATEGORIES = [
    ("Insurance", "Insurance"),
    ("Government", "Government"),
    ("Safety", "Safety"),
    ("Training", "Training"),
    ("Licence", "Licence"),
    ("Fleet", "Fleet"),
    ("Other", "Other"),
]

DOCUMENT_TYPE_CHOICES = [
    "Commercial General Liability",
    "Automobile Liability",
    "Professional Liability",
    "Errors and Omissions",
    "WSIB Clearance Certificate",
    "Business Licence",
    "Working at Heights",
    "WHMIS",
    "First Aid",
    "Driver's Licence",
    "Vehicle Inspection",
    "Other",
]

CURRENCY_CHOICES = [("CAD", "CAD"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")]

VERIFICATION_STATUSES = [
    ("unverified", "Unverified"),
    ("verified", "Verified"),
    ("rejected", "Rejected"),
]

REMINDER_OFFSETS = [90, 60, 30, 14, 7, 1, 0, -1]


# ---------------------------------------------------------------------------
# Setup / config helpers
# ---------------------------------------------------------------------------


class SetupError(Exception):
    """Raised when required environment values are missing."""


def require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise SetupError(
            f"Missing required setting '{name}'. "
            f"Copy .env.example to .env and fill in {name}."
        )
    return value


def get_config() -> dict[str, Any]:
    """Read and validate configuration from environment variables."""
    secret_key = require_env("SECRET_KEY")
    admin_email = require_env("ADMIN_EMAIL").lower()
    admin_password = require_env("ADMIN_PASSWORD")
    if len(admin_password) < 8:
        raise SetupError("ADMIN_PASSWORD must be at least 8 characters long.")

    database_url = os.getenv("DATABASE_URL", "sqlite:///compliance.db").strip()
    organization_name = os.getenv("ORGANIZATION_NAME", "VerifyFlow").strip() or "VerifyFlow"

    return {
        "SECRET_KEY": secret_key,
        "ADMIN_EMAIL": admin_email,
        "ADMIN_PASSWORD": admin_password,
        "DATABASE_URL": database_url,
        "ORGANIZATION_NAME": organization_name,
        "OPENAI_API_KEY": (os.getenv("OPENAI_API_KEY") or "").strip(),
        "OPENAI_MODEL": (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip(),
        "SMTP_HOST": (os.getenv("SMTP_HOST") or "").strip(),
        "SMTP_PORT": int(os.getenv("SMTP_PORT") or "587"),
        "SMTP_USERNAME": (os.getenv("SMTP_USERNAME") or "").strip(),
        "SMTP_PASSWORD": (os.getenv("SMTP_PASSWORD") or "").strip(),
        "SMTP_USE_TLS": (os.getenv("SMTP_USE_TLS") or "true").lower() in {"1", "true", "yes"},
        "REMINDER_FROM_EMAIL": (os.getenv("REMINDER_FROM_EMAIL") or "").strip(),
        "APP_BASE_URL": (os.getenv("APP_BASE_URL") or "http://127.0.0.1:5000").rstrip("/"),
    }


# ---------------------------------------------------------------------------
# Database models
# ---------------------------------------------------------------------------


class Organization(db.Model):
    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    users = db.relationship("User", back_populates="organization", lazy=True)
    contractors = db.relationship("Contractor", back_populates="organization", lazy=True)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    is_admin = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    organization = db.relationship("Organization", back_populates="users")

    def set_password(self, password: str) -> None:
        # pbkdf2 is widely available; some macOS Python builds lack hashlib.scrypt
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Contractor(db.Model):
    __tablename__ = "contractors"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    company_name = db.Column(db.String(200), nullable=False)
    contact_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50), nullable=True)
    trade_or_service = db.Column(db.String(200), nullable=True)
    government_vendor_number = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    organization = db.relationship("Organization", back_populates="contractors")
    documents = db.relationship(
        "ComplianceDocument",
        back_populates="contractor",
        cascade="all, delete-orphan",
        lazy=True,
    )


class ComplianceDocument(db.Model):
    __tablename__ = "compliance_documents"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    contractor_id = db.Column(db.Integer, db.ForeignKey("contractors.id"), nullable=False, index=True)

    category = db.Column(db.String(50), nullable=False, default="Insurance")
    document_type = db.Column(db.String(200), nullable=False)
    issuer = db.Column(db.String(200), nullable=True)
    policy_number = db.Column(db.String(100), nullable=True)
    coverage_amount = db.Column(db.Numeric(14, 2), nullable=True)
    currency = db.Column(db.String(10), nullable=True, default="CAD")
    issued_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=False)

    verification_status = db.Column(db.String(30), nullable=False, default="unverified")
    original_filename = db.Column(db.String(255), nullable=True)
    stored_filename = db.Column(db.String(255), nullable=True)
    extraction_method = db.Column(db.String(50), nullable=True)
    date_uploaded = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    notes = db.Column(db.Text, nullable=True)

    contractor = db.relationship("Contractor", back_populates="documents")

    def days_remaining(self, today: Optional[date] = None) -> int:
        today = today or date.today()
        return (self.expiry_date - today).days

    def expiry_status(self, today: Optional[date] = None) -> str:
        days = self.days_remaining(today)
        if days < 0:
            return "Expired"
        if days <= 30:
            return "Expiring Soon"
        return "Valid"


class ReminderLog(db.Model):
    __tablename__ = "reminder_logs"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("compliance_documents.id"), nullable=False, index=True)
    reminder_offset_days = db.Column(db.Integer, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    recipient_email = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), nullable=False, default="sent")
    error_message = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "document_id",
            "reminder_offset_days",
            name="uq_reminder_document_offset",
        ),
    )


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(255), nullable=True)
    organization_id = db.Column(db.Integer, nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)
    object_type = db.Column(db.String(50), nullable=False)
    object_id = db.Column(db.String(36), nullable=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(check_deliverability=False), Length(max=255)],
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Sign in")


class ContractorForm(FlaskForm):
    company_name = StringField("Company name", validators=[DataRequired(), Length(max=200)])
    contact_name = StringField("Contact name", validators=[DataRequired(), Length(max=200)])
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(check_deliverability=False), Length(max=255)],
    )
    phone = StringField("Phone number", validators=[OptionalField(), Length(max=50)])
    trade_or_service = StringField("Trade or service", validators=[OptionalField(), Length(max=200)])
    government_vendor_number = StringField(
        "Government vendor number", validators=[OptionalField(), Length(max=100)]
    )
    notes = TextAreaField("Notes", validators=[OptionalField(), Length(max=5000)])
    is_active = BooleanField("Active", default=True)
    submit = SubmitField("Save subcontractor")


class DeleteForm(FlaskForm):
    submit = SubmitField("Confirm delete")


class DocumentForm(FlaskForm):
    category = SelectField("Category", choices=DOCUMENT_CATEGORIES, validators=[DataRequired()])
    document_type = StringField("Document type", validators=[DataRequired(), Length(max=200)])
    issuer = StringField("Insurance company or issuer", validators=[OptionalField(), Length(max=200)])
    policy_number = StringField("Policy / certificate number", validators=[OptionalField(), Length(max=100)])
    coverage_amount = DecimalField(
        "Coverage amount",
        places=2,
        validators=[OptionalField(), NumberRange(min=0)],
    )
    currency = SelectField("Currency", choices=CURRENCY_CHOICES, validators=[OptionalField()])
    issued_date = DateField("Issued date", validators=[OptionalField()], format="%Y-%m-%d")
    expiry_date = DateField("Expiry date", validators=[DataRequired()], format="%Y-%m-%d")
    verification_status = SelectField(
        "Verification status",
        choices=VERIFICATION_STATUSES,
        validators=[DataRequired()],
    )
    notes = TextAreaField("Notes", validators=[OptionalField(), Length(max=5000)])
    file = FileField(
        "Document file",
        validators=[
            OptionalField(),
            FileAllowed(list(ALLOWED_EXTENSIONS), "Only PDF, PNG, JPG, or JPEG files are allowed."),
        ],
    )
    submit = SubmitField("Save document")

    def validate_expiry_date(self, field: DateField) -> None:
        if field.data and self.issued_date.data and field.data < self.issued_date.data:
            raise WTFormsValidationError("Expiry date cannot be before the issued date.")


class ReportFilterForm(FlaskForm):
    status = SelectField(
        "Status",
        choices=[
            ("", "All statuses"),
            ("Valid", "Valid"),
            ("Expiring Soon", "Expiring soon"),
            ("Expired", "Expired"),
        ],
        validators=[OptionalField()],
    )
    insurance_only = BooleanField("Insurance only")
    contractor_id = SelectField("Contractor", choices=[], validators=[OptionalField()], validate_choice=False)
    start_date = DateField("Expiry from", validators=[OptionalField()], format="%Y-%m-%d")
    end_date = DateField("Expiry to", validators=[OptionalField()], format="%Y-%m-%d")
    submit = SubmitField("Apply filters")
    export = SubmitField("Export CSV")


# ---------------------------------------------------------------------------
# AI extraction validation (Pydantic)
# ---------------------------------------------------------------------------


class ExtractedDocumentData(BaseModel):
    """Structured fields returned by OpenAI. Missing values stay None — no guessing."""

    document_type: Optional[str] = None
    category: Optional[str] = None
    issuer: Optional[str] = None
    policy_number: Optional[str] = None
    coverage_amount: Optional[float] = None
    currency: Optional[str] = None
    issued_date: Optional[date] = None
    expiry_date: Optional[date] = None
    contractor_company_name: Optional[str] = None

    @field_validator("issued_date", "expiry_date", mode="before")
    @classmethod
    def parse_dates(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(value.strip(), fmt).date()
                except ValueError:
                    continue
            raise ValueError(f"Invalid date format: {value}")
        raise ValueError("Invalid date value")

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        allowed = {c[0] for c in DOCUMENT_CATEGORIES}
        if value not in allowed:
            return None
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.upper().strip()
        allowed = {c[0] for c in CURRENCY_CHOICES}
        return value if value in allowed else None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(test_config: Optional[dict[str, Any]] = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    if test_config:
        app.config.update(test_config)
        config = {
            "ADMIN_EMAIL": test_config.get("ADMIN_EMAIL", "admin@example.com"),
            "ADMIN_PASSWORD": test_config.get("ADMIN_PASSWORD", "test-password-123"),
            "ORGANIZATION_NAME": test_config.get("ORGANIZATION_NAME", "Test Org"),
            "OPENAI_API_KEY": "",
            "OPENAI_MODEL": "gpt-4o-mini",
            "SMTP_HOST": "",
            "SMTP_PORT": 587,
            "SMTP_USERNAME": "",
            "SMTP_PASSWORD": "",
            "SMTP_USE_TLS": True,
            "REMINDER_FROM_EMAIL": "",
            "APP_BASE_URL": test_config.get("APP_BASE_URL", "http://127.0.0.1:5000"),
        }
    else:
        try:
            config = get_config()
        except SetupError as exc:
            app.config["SECRET_KEY"] = "setup-incomplete"
            app.config["SETUP_ERROR"] = str(exc)
            register_setup_error_routes(app)
            return app

        app.config["SECRET_KEY"] = config["SECRET_KEY"]
        app.config["SQLALCHEMY_DATABASE_URI"] = config["DATABASE_URL"]

    app.config.setdefault("SQLALCHEMY_DATABASE_URI", "sqlite:///compliance.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
    app.config["WTF_CSRF_ENABLED"] = test_config.get("WTF_CSRF_ENABLED", True) if test_config else True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    app.config["VERIFYFLOW"] = config

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "login"
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id: str) -> Optional[User]:
        return db.session.get(User, int(user_id))

    register_error_handlers(app)
    register_template_helpers(app)
    register_routes(app)
    register_cli(app)

    return app


def register_setup_error_routes(app: Flask) -> None:
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def setup_required(path: str):  # noqa: ARG001
        return (
            render_template("setup_error.html", error=app.config.get("SETUP_ERROR")),
            503,
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def write_audit(
    action: str,
    object_type: str,
    object_id: Optional[str] = None,
    details: Optional[str] = None,
    organization_id: Optional[int] = None,
    user_email: Optional[str] = None,
) -> None:
    resolved_email = user_email
    resolved_org = organization_id
    # Reminder CLI runs outside a browser request — avoid touching current_user then
    try:
        if resolved_email is None and current_user.is_authenticated:
            resolved_email = current_user.email
        if resolved_org is None and current_user.is_authenticated:
            resolved_org = current_user.organization_id
    except Exception:  # noqa: BLE001
        pass

    entry = AuditLog(
        user_email=resolved_email,
        organization_id=resolved_org,
        action=action,
        object_type=object_type,
        object_id=object_id,
        details=details,
    )
    db.session.add(entry)


def org_required(view):
    """Ensure the logged-in user has an organization."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.organization_id:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def get_contractor_or_404(public_id: str) -> Contractor:
    contractor = Contractor.query.filter_by(
        public_id=public_id,
        organization_id=current_user.organization_id,
    ).first()
    if not contractor:
        abort(404)
    return contractor


def get_document_or_404(public_id: str) -> ComplianceDocument:
    document = ComplianceDocument.query.filter_by(
        public_id=public_id,
        organization_id=current_user.organization_id,
    ).first()
    if not document:
        abort(404)
    return document


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def safe_delete_file(stored_filename: Optional[str]) -> None:
    if not stored_filename:
        return
    if "/" in stored_filename or "\\" in stored_filename or ".." in stored_filename:
        logger.warning("Refused to delete suspicious filename: %s", stored_filename)
        return
    path = UPLOAD_DIR / stored_filename
    try:
        resolved = path.resolve()
        if resolved.parent != UPLOAD_DIR.resolve():
            logger.warning("Path traversal blocked for: %s", stored_filename)
            return
        if resolved.exists():
            resolved.unlink()
    except OSError as exc:
        logger.error("Could not delete file %s: %s", stored_filename, exc)


def save_upload(file_storage) -> tuple[str, str]:
    """Save an uploaded file with a UUID name. Returns (original, stored)."""
    if not file_storage or not file_storage.filename:
        raise ValueError("No file was selected.")

    original = secure_filename(file_storage.filename)
    if not original or not allowed_file(original):
        raise ValueError("Unsupported file type. Use PDF, PNG, JPG, or JPEG.")

    extension = original.rsplit(".", 1)[1].lower()
    stored = f"{uuid.uuid4().hex}.{extension}"
    destination = UPLOAD_DIR / stored

    if destination.resolve().parent != UPLOAD_DIR.resolve():
        raise ValueError("Invalid storage path.")

    file_storage.save(destination)
    return original, stored


def days_label(days: int) -> str:
    absolute = abs(days)
    unit = "day" if absolute == 1 else "days"
    if days > 0:
        return f"{absolute} {unit} remaining"
    if days == 0:
        return "expires today"
    return f"expired {absolute} {unit} ago"


def reminder_days_phrase(offset: int) -> str:
    if offset > 0:
        unit = "day" if offset == 1 else "days"
        return f"{offset} {unit} before expiry"
    if offset == 0:
        return "on the expiry date"
    unit = "day" if abs(offset) == 1 else "days"
    return f"{abs(offset)} {unit} after expiry"


def parse_flexible_date(text: str) -> Optional[date]:
    patterns = [
        r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b",
        r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parts = match.groups()
        try:
            if len(parts[0]) == 4:
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if month > 12 and day <= 12:
                day, month = month, day
            return date(year, month, day)
        except ValueError:
            continue
    return None


def extract_text_from_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        chunks: list[str] = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


def regex_extract(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "policy_number": None,
        "coverage_amount": None,
        "expiry_date": None,
        "extraction_method": "regex",
    }
    if not text:
        return result

    policy_match = re.search(
        r"(?:policy|certificate|cert)\s*(?:no|number|#)?\s*[:#]?\s*([A-Z0-9\-/]{5,})",
        text,
        re.IGNORECASE,
    )
    if policy_match:
        result["policy_number"] = policy_match.group(1).strip()

    amount_match = re.search(
        r"(?:coverage|limit|amount)\s*(?:of)?\s*[:$]?\s*\$?\s*([\d,]+\.?\d*)",
        text,
        re.IGNORECASE,
    )
    if amount_match:
        try:
            result["coverage_amount"] = float(amount_match.group(1).replace(",", ""))
        except ValueError:
            pass

    expiry_match = re.search(
        r"(?:expir(?:y|es|ation)|valid\s+until|valid\s+through)\s*(?:date)?\s*[:\-]?\s*"
        r"([0-9]{1,4}[-/][0-9]{1,2}[-/][0-9]{1,4})",
        text,
        re.IGNORECASE,
    )
    if expiry_match:
        result["expiry_date"] = parse_flexible_date(expiry_match.group(1))

    return result


def openai_extract(text: str, app: Flask) -> Optional[ExtractedDocumentData]:
    api_key = app.config["VERIFYFLOW"].get("OPENAI_API_KEY")
    if not api_key or not text.strip():
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = app.config["VERIFYFLOW"].get("OPENAI_MODEL") or "gpt-4o-mini"
        prompt = (
            "Extract insurance/compliance fields from this document text. "
            "Return ONLY valid JSON with keys: document_type, category, issuer, "
            "policy_number, coverage_amount, currency, issued_date, expiry_date, "
            "contractor_company_name. Use null for unknown values. "
            "Do not guess. Dates must be YYYY-MM-DD. Category must be one of: "
            "Insurance, Government, Safety, Training, Licence, Fleet, Other.\n\n"
            f"Document text:\n{text[:12000]}"
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You extract structured compliance data. Never invent values.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = response.choices[0].message.content or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("OpenAI returned invalid JSON")
            return None
        return ExtractedDocumentData.model_validate(payload)
    except ValidationError as exc:
        logger.error("OpenAI payload failed validation: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("OpenAI extraction failed: %s", exc)
        return None


def try_extract_from_file(stored_filename: str, app: Flask) -> dict[str, Any]:
    path = UPLOAD_DIR / stored_filename
    extension = stored_filename.rsplit(".", 1)[-1].lower()

    if extension in {"png", "jpg", "jpeg"}:
        return {
            "extraction_method": "none",
            "message": "OCR is not included yet. Image files cannot be read automatically.",
        }

    if extension != "pdf":
        return {"extraction_method": "none", "message": "Unsupported extraction type."}

    text = extract_text_from_pdf(path)
    if not text:
        return {
            "extraction_method": "none",
            "message": (
                "No readable text was found in this PDF. "
                "It may be a scanned image. OCR is not included yet."
            ),
        }

    ai_data = openai_extract(text, app)
    if ai_data:
        return {
            "extraction_method": "openai",
            "document_type": ai_data.document_type,
            "category": ai_data.category,
            "issuer": ai_data.issuer,
            "policy_number": ai_data.policy_number,
            "coverage_amount": ai_data.coverage_amount,
            "currency": ai_data.currency,
            "issued_date": ai_data.issued_date,
            "expiry_date": ai_data.expiry_date,
            "message": (
                "AI suggested values were applied as unverified. "
                "AI extraction does not prove that the document is authentic "
                "or that the insurance coverage is sufficient."
            ),
        }

    regex_data = regex_extract(text)
    regex_data["message"] = "Simple text extraction suggested some fields. Please review carefully."
    return regex_data


def org_documents_query():
    return ComplianceDocument.query.filter_by(organization_id=current_user.organization_id)


def org_contractors_query():
    return Contractor.query.filter_by(organization_id=current_user.organization_id)


def filter_documents_for_report(
    status: str = "",
    insurance_only: bool = False,
    contractor_public_id: str = "",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    query = (
        org_documents_query()
        .join(Contractor)
        .order_by(ComplianceDocument.expiry_date.asc())
    )
    if insurance_only:
        query = query.filter(ComplianceDocument.category == "Insurance")
    if contractor_public_id:
        query = query.filter(Contractor.public_id == contractor_public_id)
    if start_date:
        query = query.filter(ComplianceDocument.expiry_date >= start_date)
    if end_date:
        query = query.filter(ComplianceDocument.expiry_date <= end_date)

    documents = query.all()
    if status:
        documents = [d for d in documents if d.expiry_status() == status]
    return documents


def send_email_message(app: Flask, to_email: str, subject: str, body: str) -> tuple[bool, str]:
    cfg = app.config["VERIFYFLOW"]
    host = cfg.get("SMTP_HOST")
    from_email = cfg.get("REMINDER_FROM_EMAIL") or cfg.get("SMTP_USERNAME")

    if not host or not from_email:
        print("===== VERIFYFLOW REMINDER (DRY-RUN) =====")
        print(f"To: {to_email}")
        print(f"Subject: {subject}")
        print(body)
        print("=========================================")
        return True, "dry-run"

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(host, cfg["SMTP_PORT"], timeout=30) as server:
            if cfg.get("SMTP_USE_TLS"):
                server.starttls()
            username = cfg.get("SMTP_USERNAME")
            password = cfg.get("SMTP_PASSWORD")
            if username and password:
                server.login(username, password)
            server.send_message(message)
        return True, "sent"
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send reminder to %s: %s", to_email, exc)
        return False, str(exc)


def process_reminders(app: Flask, today: Optional[date] = None) -> dict[str, int]:
    today = today or date.today()
    stats = {"sent": 0, "dry_run": 0, "skipped": 0, "failed": 0}
    base_url = app.config["VERIFYFLOW"]["APP_BASE_URL"]

    documents = ComplianceDocument.query.all()
    for document in documents:
        contractor = db.session.get(Contractor, document.contractor_id)
        if not contractor or not contractor.is_active or not contractor.email:
            stats["skipped"] += 1
            continue

        days_left = document.days_remaining(today)
        if days_left not in REMINDER_OFFSETS:
            continue

        offset = days_left
        already = ReminderLog.query.filter_by(
            document_id=document.id,
            reminder_offset_days=offset,
        ).first()
        if already:
            stats["skipped"] += 1
            continue

        subject = (
            f"Compliance reminder: {document.document_type} "
            f"({reminder_days_phrase(offset)})"
        )
        link = f"{base_url}/contractors/{contractor.public_id}"
        body = (
            f"Hello {contractor.contact_name},\n\n"
            f"This is a reminder about a compliance document for {contractor.company_name}.\n\n"
            f"Document type: {document.document_type}\n"
            f"Expiry date: {document.expiry_date.isoformat()}\n"
            f"Policy / certificate number: {document.policy_number or 'Not provided'}\n"
            f"Status: {days_label(days_left)}\n\n"
            f"View the contractor record: {link}\n\n"
            f"This message was sent by VerifyFlow. "
            f"It is not legal advice and does not confirm that coverage is adequate.\n"
        )

        ok, status_or_error = send_email_message(app, contractor.email, subject, body)
        if ok:
            status = status_or_error
            error_message = None
            if status == "dry-run":
                stats["dry_run"] += 1
            else:
                stats["sent"] += 1
        else:
            status = "failed"
            error_message = status_or_error
            stats["failed"] += 1

        log = ReminderLog(
            document_id=document.id,
            reminder_offset_days=offset,
            recipient_email=contractor.email,
            status=status,
            error_message=error_message,
        )
        db.session.add(log)
        write_audit(
            action="reminder_sent" if status != "failed" else "reminder_failed",
            object_type="document",
            object_id=document.public_id,
            details=f"offset={offset}; status={status}",
            organization_id=document.organization_id,
            user_email="system",
        )
        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.error("Could not save reminder log: %s", exc)
            stats["failed"] += 1

    return stats


def ensure_admin_and_org(app: Flask) -> None:
    """Create the default organization and admin user from .env values."""
    cfg = app.config["VERIFYFLOW"]
    org = Organization.query.filter_by(name=cfg["ORGANIZATION_NAME"]).first()
    if not org:
        org = Organization(name=cfg["ORGANIZATION_NAME"])
        db.session.add(org)
        db.session.flush()

    user = User.query.filter_by(email=cfg["ADMIN_EMAIL"]).first()
    if not user:
        user = User(email=cfg["ADMIN_EMAIL"], organization_id=org.id, is_admin=True)
        user.set_password(cfg["ADMIN_PASSWORD"])
        db.session.add(user)
    else:
        user.set_password(cfg["ADMIN_PASSWORD"])
        user.organization_id = org.id
    db.session.commit()


# ---------------------------------------------------------------------------
# Template helpers and error pages
# ---------------------------------------------------------------------------


def register_template_helpers(app: Flask) -> None:
    @app.context_processor
    def inject_globals():
        return {
            "app_name": "VerifyFlow",
            "document_type_suggestions": DOCUMENT_TYPE_CHOICES,
        }

    @app.template_filter("expiry_badge_class")
    def expiry_badge_class(status: str) -> str:
        mapping = {
            "Valid": "badge-valid",
            "Expiring Soon": "badge-soon",
            "Expired": "badge-expired",
        }
        return mapping.get(status, "badge-muted")

    @app.template_filter("days_label")
    def days_label_filter(days: int) -> str:
        return days_label(days)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(error):  # noqa: ARG001
        return render_template(
            "error.html",
            code=403,
            title="Forbidden",
            message="You do not have permission to view this page.",
        ), 403

    @app.errorhandler(404)
    def not_found(error):  # noqa: ARG001
        return render_template(
            "error.html",
            code=404,
            title="Not found",
            message="That page or record could not be found.",
        ), 404

    @app.errorhandler(413)
    def too_large(error):  # noqa: ARG001
        flash("The uploaded file is too large. Maximum size is 10 MB.", "danger")
        return redirect(request.referrer or url_for("dashboard")), 413

    @app.errorhandler(500)
    def server_error(error):  # noqa: ARG001
        logger.exception("Server error: %s", error)
        return render_template(
            "error.html",
            code=500,
            title="Server error",
            message="Something went wrong. Please try again or check the server logs.",
        ), 500


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_routes(app: Flask) -> None:
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        form = LoginForm()
        if form.validate_on_submit():
            user = User.query.filter_by(email=form.email.data.lower().strip()).first()
            if user and user.check_password(form.password.data):
                login_user(user)
                flash("Signed in successfully.", "success")
                next_url = request.args.get("next")
                if next_url and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect(url_for("dashboard"))
            flash("Invalid email or password.", "danger")
        return render_template("login.html", form=form)

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been signed out.", "success")
        return redirect(url_for("login"))

    @app.route("/")
    @org_required
    def dashboard():
        contractors_active = org_contractors_query().filter_by(is_active=True).count()
        documents = org_documents_query().all()
        valid = sum(1 for d in documents if d.expiry_status() == "Valid")
        soon = sum(1 for d in documents if d.expiry_status() == "Expiring Soon")
        expired = sum(1 for d in documents if d.expiry_status() == "Expired")

        upcoming = (
            org_documents_query()
            .filter(ComplianceDocument.expiry_date >= date.today())
            .order_by(ComplianceDocument.expiry_date.asc())
            .limit(8)
            .all()
        )
        recent = (
            org_documents_query()
            .order_by(ComplianceDocument.date_uploaded.desc())
            .limit(8)
            .all()
        )

        return render_template(
            "dashboard.html",
            contractors_active=contractors_active,
            valid_count=valid,
            soon_count=soon,
            expired_count=expired,
            upcoming=upcoming,
            recent=recent,
        )

    @app.route("/contractors")
    @org_required
    def contractors():
        items = org_contractors_query().order_by(Contractor.company_name.asc()).all()
        return render_template("contractors.html", contractors=items)

    @app.route("/contractors/new", methods=["GET", "POST"])
    @org_required
    def contractor_new():
        form = ContractorForm()
        if form.validate_on_submit():
            contractor = Contractor(
                organization_id=current_user.organization_id,
                company_name=form.company_name.data.strip(),
                contact_name=form.contact_name.data.strip(),
                email=form.email.data.strip().lower(),
                phone=(form.phone.data or "").strip() or None,
                trade_or_service=(form.trade_or_service.data or "").strip() or None,
                government_vendor_number=(form.government_vendor_number.data or "").strip() or None,
                notes=(form.notes.data or "").strip() or None,
                is_active=bool(form.is_active.data),
            )
            db.session.add(contractor)
            db.session.flush()
            write_audit("contractor_created", "contractor", contractor.public_id, contractor.company_name)
            db.session.commit()
            flash("Subcontractor added.", "success")
            return redirect(url_for("contractor_detail", public_id=contractor.public_id))
        return render_template("contractor_form.html", form=form, title="Add subcontractor")

    @app.route("/contractors/<public_id>")
    @org_required
    def contractor_detail(public_id: str):
        contractor = get_contractor_or_404(public_id)
        documents = (
            ComplianceDocument.query.filter_by(
                contractor_id=contractor.id,
                organization_id=current_user.organization_id,
            )
            .order_by(ComplianceDocument.expiry_date.asc())
            .all()
        )
        delete_form = DeleteForm()
        return render_template(
            "contractor_detail.html",
            contractor=contractor,
            documents=documents,
            delete_form=delete_form,
        )

    @app.route("/contractors/<public_id>/edit", methods=["GET", "POST"])
    @org_required
    def contractor_edit(public_id: str):
        contractor = get_contractor_or_404(public_id)
        form = ContractorForm(obj=contractor)
        if form.validate_on_submit():
            contractor.company_name = form.company_name.data.strip()
            contractor.contact_name = form.contact_name.data.strip()
            contractor.email = form.email.data.strip().lower()
            contractor.phone = (form.phone.data or "").strip() or None
            contractor.trade_or_service = (form.trade_or_service.data or "").strip() or None
            contractor.government_vendor_number = (
                (form.government_vendor_number.data or "").strip() or None
            )
            contractor.notes = (form.notes.data or "").strip() or None
            contractor.is_active = bool(form.is_active.data)
            write_audit("contractor_edited", "contractor", contractor.public_id, contractor.company_name)
            db.session.commit()
            flash("Subcontractor updated.", "success")
            return redirect(url_for("contractor_detail", public_id=contractor.public_id))
        return render_template("contractor_form.html", form=form, title="Edit subcontractor")

    @app.route("/contractors/<public_id>/delete", methods=["POST"])
    @org_required
    def contractor_delete(public_id: str):
        contractor = get_contractor_or_404(public_id)
        form = DeleteForm()
        if not form.validate_on_submit():
            flash("Delete confirmation failed.", "danger")
            return redirect(url_for("contractor_detail", public_id=public_id))

        for document in list(contractor.documents):
            safe_delete_file(document.stored_filename)
            write_audit("document_deleted", "document", document.public_id, "Deleted with contractor")

        write_audit("contractor_deleted", "contractor", contractor.public_id, contractor.company_name)
        db.session.delete(contractor)
        db.session.commit()
        flash("Subcontractor deleted.", "success")
        return redirect(url_for("contractors"))

    @app.route("/contractors/<contractor_id>/documents/new", methods=["GET", "POST"])
    @org_required
    def document_new(contractor_id: str):
        contractor = get_contractor_or_404(contractor_id)
        form = DocumentForm()
        form.file.validators = [
            FileRequired(message="Please choose a file to upload."),
            FileAllowed(list(ALLOWED_EXTENSIONS), "Only PDF, PNG, JPG, or JPEG files are allowed."),
        ]

        if form.validate_on_submit():
            stored_filename = None
            try:
                original, stored_filename = save_upload(form.file.data)
                extracted = try_extract_from_file(stored_filename, app)
                extraction_method = extracted.get("extraction_method") or "manual"

                document = ComplianceDocument(
                    organization_id=current_user.organization_id,
                    contractor_id=contractor.id,
                    category=form.category.data,
                    document_type=form.document_type.data.strip(),
                    issuer=(form.issuer.data or extracted.get("issuer") or "").strip() or None,
                    policy_number=(
                        form.policy_number.data or extracted.get("policy_number") or ""
                    ).strip()
                    or None,
                    coverage_amount=form.coverage_amount.data
                    if form.coverage_amount.data is not None
                    else extracted.get("coverage_amount"),
                    currency=form.currency.data or extracted.get("currency") or "CAD",
                    issued_date=form.issued_date.data or extracted.get("issued_date"),
                    expiry_date=form.expiry_date.data or extracted.get("expiry_date"),
                    verification_status="unverified"
                    if extraction_method == "openai"
                    else form.verification_status.data,
                    original_filename=original,
                    stored_filename=stored_filename,
                    extraction_method=extraction_method,
                    notes=(form.notes.data or "").strip() or None,
                )
                if not document.expiry_date:
                    raise ValueError("Expiry date is required.")

                db.session.add(document)
                db.session.flush()
                write_audit(
                    "document_uploaded",
                    "document",
                    document.public_id,
                    f"{document.document_type} for {contractor.company_name}",
                )
                db.session.commit()
                if extracted.get("message"):
                    flash(extracted["message"], "warning")
                flash(
                    "Document uploaded. AI extraction does not prove that the document is "
                    "authentic or that the insurance coverage is sufficient.",
                    "warning",
                )
                flash("Document saved.", "success")
                return redirect(url_for("contractor_detail", public_id=contractor.public_id))
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                safe_delete_file(stored_filename)
                flash(str(exc), "danger")
        return render_template(
            "document_form.html",
            form=form,
            contractor=contractor,
            title="Add document",
            is_new=True,
        )

    @app.route("/documents/<public_id>/edit", methods=["GET", "POST"])
    @org_required
    def document_edit(public_id: str):
        document = get_document_or_404(public_id)
        contractor = get_contractor_or_404(document.contractor.public_id)
        form = DocumentForm(obj=document)

        if form.validate_on_submit():
            new_stored = None
            try:
                if form.file.data and form.file.data.filename:
                    original, new_stored = save_upload(form.file.data)
                    document.original_filename = original
                    old = document.stored_filename
                    document.stored_filename = new_stored
                    safe_delete_file(old)
                    extracted = try_extract_from_file(new_stored, app)
                    document.extraction_method = (
                        extracted.get("extraction_method") or document.extraction_method
                    )
                    if extracted.get("message"):
                        flash(extracted["message"], "warning")

                old_verification = document.verification_status
                document.category = form.category.data
                document.document_type = form.document_type.data.strip()
                document.issuer = (form.issuer.data or "").strip() or None
                document.policy_number = (form.policy_number.data or "").strip() or None
                document.coverage_amount = form.coverage_amount.data
                document.currency = form.currency.data or "CAD"
                document.issued_date = form.issued_date.data
                document.expiry_date = form.expiry_date.data
                document.verification_status = form.verification_status.data
                document.notes = (form.notes.data or "").strip() or None

                write_audit("document_edited", "document", document.public_id)
                if old_verification != "verified" and document.verification_status == "verified":
                    write_audit("document_verified", "document", document.public_id)
                db.session.commit()
                flash("Document updated.", "success")
                return redirect(url_for("contractor_detail", public_id=contractor.public_id))
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                safe_delete_file(new_stored)
                flash(str(exc), "danger")

        return render_template(
            "document_form.html",
            form=form,
            contractor=contractor,
            document=document,
            title="Edit document",
            is_new=False,
        )

    @app.route("/documents/<public_id>/download")
    @org_required
    def document_download(public_id: str):
        document = get_document_or_404(public_id)
        if not document.stored_filename:
            abort(404)
        if (
            "/" in document.stored_filename
            or "\\" in document.stored_filename
            or ".." in document.stored_filename
        ):
            abort(404)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            document.stored_filename,
            as_attachment=True,
            download_name=document.original_filename or document.stored_filename,
        )

    @app.route("/documents/<public_id>/delete", methods=["POST"])
    @org_required
    def document_delete(public_id: str):
        document = get_document_or_404(public_id)
        form = DeleteForm()
        contractor_public_id = document.contractor.public_id
        if not form.validate_on_submit():
            flash("Delete confirmation failed.", "danger")
            return redirect(url_for("contractor_detail", public_id=contractor_public_id))

        safe_delete_file(document.stored_filename)
        write_audit("document_deleted", "document", document.public_id, document.document_type)
        db.session.delete(document)
        db.session.commit()
        flash("Document deleted.", "success")
        return redirect(url_for("contractor_detail", public_id=contractor_public_id))

    @app.route("/reports", methods=["GET", "POST"])
    @org_required
    def reports():
        form = ReportFilterForm()
        form.contractor_id.choices = [("", "All contractors")] + [
            (c.public_id, c.company_name)
            for c in org_contractors_query().order_by(Contractor.company_name).all()
        ]

        documents = []
        if request.method == "POST" and form.validate_on_submit():
            documents = filter_documents_for_report(
                status=form.status.data or "",
                insurance_only=bool(form.insurance_only.data),
                contractor_public_id=form.contractor_id.data or "",
                start_date=form.start_date.data,
                end_date=form.end_date.data,
            )
            if form.export.data:
                return export_documents_csv(documents)
        elif request.method == "GET":
            documents = filter_documents_for_report()

        return render_template("reports.html", form=form, documents=documents)

    def export_documents_csv(documents: list[ComplianceDocument]) -> Response:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Subcontractor company",
                "Contact name",
                "Email",
                "Category",
                "Document type",
                "Policy or certificate number",
                "Expiry date",
                "Days remaining",
                "Status",
                "Verification status",
            ]
        )
        for document in documents:
            writer.writerow(
                [
                    document.contractor.company_name,
                    document.contractor.contact_name,
                    document.contractor.email,
                    document.category,
                    document.document_type,
                    document.policy_number or "",
                    document.expiry_date.isoformat(),
                    document.days_remaining(),
                    document.expiry_status(),
                    document.verification_status,
                ]
            )
        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=verifyflow-report.csv"
        return response


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db_command():
        """Create tables and the administrator account from .env."""
        db.create_all()
        ensure_admin_and_org(app)
        print("Database ready. Administrator account created/updated from .env.")

    @app.cli.command("send-reminders")
    def send_reminders_command():
        """Send expiry reminder emails (or dry-run if SMTP is not configured)."""
        stats = process_reminders(app)
        print(
            "Reminder run complete: "
            f"sent={stats['sent']} dry_run={stats['dry_run']} "
            f"skipped={stats['skipped']} failed={stats['failed']}"
        )


# Application instance for `flask --app app` and `python app.py`
app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
