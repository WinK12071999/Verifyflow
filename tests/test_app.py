"""
Automated tests for VerifyFlow.

Run from the project folder (with the virtual environment active):
    pytest -q
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import (
    ComplianceDocument,
    Contractor,
    Organization,
    ReminderLog,
    User,
    create_app,
    db,
    process_reminders,
)


@pytest.fixture()
def app(tmp_path: Path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    db_path = tmp_path / "test.db"

    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-secret-key",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "UPLOAD_FOLDER": str(upload_dir),
            "ADMIN_EMAIL": "admin@example.com",
            "ADMIN_PASSWORD": "test-password-123",
            "ORGANIZATION_NAME": "Org Alpha",
            "APP_BASE_URL": "http://127.0.0.1:5000",
        }
    )

    # Point upload helpers at the temporary folder used by this test run
    import app as app_module

    app_module.UPLOAD_DIR = upload_dir
    application.config["UPLOAD_FOLDER"] = str(upload_dir)

    with application.app_context():
        db.create_all()
        org = Organization(name="Org Alpha")
        db.session.add(org)
        db.session.flush()
        admin = User(email="admin@example.com", organization_id=org.id, is_admin=True)
        admin.set_password("test-password-123")
        db.session.add(admin)
        db.session.commit()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email="admin@example.com", password="test-password-123"):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def make_pdf_bytes(text: str = "Policy number: ABC-12345\nExpiry date: 2030-01-15\nCoverage amount: 1000000") -> bytes:
    # Minimal valid-enough PDF for upload type checks; text extraction may be empty.
    # For unsupported-type / size tests we mainly care about extension and size.
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<<>>endobj\n"
        b"trailer<<>>\n"
        b"%%EOF\n"
        + text.encode("utf-8")
    )


def test_login_success(client):
    response = login(client)
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_login_invalid(client):
    response = login(client, password="wrong-password")
    assert b"Invalid email or password" in response.data


def test_organization_separation(app, client):
    with app.app_context():
        other = Organization(name="Org Beta")
        db.session.add(other)
        db.session.flush()
        foreign = Contractor(
            organization_id=other.id,
            company_name="Secret Co",
            contact_name="Hidden",
            email="hidden@example.com",
            is_active=True,
        )
        db.session.add(foreign)
        db.session.commit()
        foreign_id = foreign.public_id

    login(client)
    response = client.get(f"/contractors/{foreign_id}")
    assert response.status_code == 404


def test_add_and_edit_contractor(client):
    login(client)
    response = client.post(
        "/contractors/new",
        data={
            "company_name": "Build Right Inc",
            "contact_name": "Alex Builder",
            "email": "alex@buildright.example",
            "phone": "613-555-0100",
            "trade_or_service": "Electrical",
            "government_vendor_number": "V-100",
            "notes": "Preferred vendor",
            "is_active": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Build Right Inc" in response.data

    # Find public id via listing page link is hard; query DB through app context
    from flask import current_app

    with client.application.app_context():
        contractor = Contractor.query.filter_by(company_name="Build Right Inc").one()
        public_id = contractor.public_id

    response = client.post(
        f"/contractors/{public_id}/edit",
        data={
            "company_name": "Build Right Ltd",
            "contact_name": "Alex Builder",
            "email": "alex@buildright.example",
            "phone": "613-555-0100",
            "trade_or_service": "Electrical",
            "government_vendor_number": "V-100",
            "notes": "Updated",
            "is_active": "y",
        },
        follow_redirects=True,
    )
    assert b"Build Right Ltd" in response.data


def test_add_document(client, app, tmp_path):
    login(client)
    with app.app_context():
        org = Organization.query.filter_by(name="Org Alpha").one()
        contractor = Contractor(
            organization_id=org.id,
            company_name="Doc Co",
            contact_name="Dana",
            email="dana@doc.example",
            is_active=True,
        )
        db.session.add(contractor)
        db.session.commit()
        contractor_id = contractor.public_id

    data = {
        "category": "Insurance",
        "document_type": "Commercial General Liability",
        "issuer": "SafeInsure",
        "policy_number": "POL-999",
        "coverage_amount": "1000000",
        "currency": "CAD",
        "issued_date": "2025-01-01",
        "expiry_date": (date.today() + timedelta(days=60)).isoformat(),
        "verification_status": "unverified",
        "notes": "",
        "file": (io.BytesIO(make_pdf_bytes()), "policy.pdf"),
    }
    response = client.post(
        f"/contractors/{contractor_id}/documents/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Document saved" in response.data or b"Commercial General Liability" in response.data


def test_invalid_expiry_date(client, app):
    login(client)
    with app.app_context():
        org = Organization.query.filter_by(name="Org Alpha").one()
        contractor = Contractor(
            organization_id=org.id,
            company_name="Date Co",
            contact_name="Dana",
            email="date@example.com",
            is_active=True,
        )
        db.session.add(contractor)
        db.session.commit()
        contractor_id = contractor.public_id

    data = {
        "category": "Insurance",
        "document_type": "Automobile Liability",
        "issuer": "SafeInsure",
        "policy_number": "POL-1",
        "coverage_amount": "1000",
        "currency": "CAD",
        "issued_date": "2030-01-01",
        "expiry_date": "2020-01-01",
        "verification_status": "unverified",
        "notes": "",
        "file": (io.BytesIO(make_pdf_bytes()), "policy.pdf"),
    }
    response = client.post(
        f"/contractors/{contractor_id}/documents/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Expiry date cannot be before the issued date" in response.data


def test_unsupported_file_type(client, app):
    login(client)
    with app.app_context():
        org = Organization.query.filter_by(name="Org Alpha").one()
        contractor = Contractor(
            organization_id=org.id,
            company_name="File Co",
            contact_name="Fran",
            email="fran@example.com",
            is_active=True,
        )
        db.session.add(contractor)
        db.session.commit()
        contractor_id = contractor.public_id

    data = {
        "category": "Insurance",
        "document_type": "Commercial General Liability",
        "issuer": "SafeInsure",
        "policy_number": "POL-2",
        "coverage_amount": "1000",
        "currency": "CAD",
        "issued_date": "2025-01-01",
        "expiry_date": (date.today() + timedelta(days=40)).isoformat(),
        "verification_status": "unverified",
        "notes": "",
        "file": (io.BytesIO(b"not a pdf"), "malware.exe"),
    }
    response = client.post(
        f"/contractors/{contractor_id}/documents/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Only PDF, PNG, JPG, or JPEG" in response.data or b"Unsupported file type" in response.data


def test_oversized_file(client, app):
    login(client)
    with app.app_context():
        org = Organization.query.filter_by(name="Org Alpha").one()
        contractor = Contractor(
            organization_id=org.id,
            company_name="Big File Co",
            contact_name="Big",
            email="big@example.com",
            is_active=True,
        )
        db.session.add(contractor)
        db.session.commit()
        contractor_id = contractor.public_id

    huge = b"a" * (10 * 1024 * 1024 + 1000)
    data = {
        "category": "Insurance",
        "document_type": "Commercial General Liability",
        "issuer": "SafeInsure",
        "policy_number": "POL-3",
        "coverage_amount": "1000",
        "currency": "CAD",
        "issued_date": "2025-01-01",
        "expiry_date": (date.today() + timedelta(days=40)).isoformat(),
        "verification_status": "unverified",
        "notes": "",
        "file": (io.BytesIO(huge), "huge.pdf"),
    }
    response = client.post(
        f"/contractors/{contractor_id}/documents/new",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    # Flask returns 413; our handler flashes and redirects
    assert response.status_code in (200, 413)
    assert (
        b"too large" in response.data.lower()
        or b"Maximum size is 10 MB" in response.data
        or response.status_code == 413
    )


def test_expiry_statuses(app):
    with app.app_context():
        org = Organization.query.filter_by(name="Org Alpha").one()
        contractor = Contractor(
            organization_id=org.id,
            company_name="Status Co",
            contact_name="Sam",
            email="sam@example.com",
            is_active=True,
        )
        db.session.add(contractor)
        db.session.flush()

        today = date.today()
        valid = ComplianceDocument(
            organization_id=org.id,
            contractor_id=contractor.id,
            category="Insurance",
            document_type="CGL",
            expiry_date=today + timedelta(days=45),
        )
        soon = ComplianceDocument(
            organization_id=org.id,
            contractor_id=contractor.id,
            category="Insurance",
            document_type="Auto",
            expiry_date=today + timedelta(days=10),
        )
        expired = ComplianceDocument(
            organization_id=org.id,
            contractor_id=contractor.id,
            category="Insurance",
            document_type="WSIB",
            expiry_date=today - timedelta(days=2),
        )
        db.session.add_all([valid, soon, expired])
        db.session.commit()

        assert valid.expiry_status(today) == "Valid"
        assert soon.expiry_status(today) == "Expiring Soon"
        assert expired.expiry_status(today) == "Expired"


def test_duplicate_reminder_prevention(app):
    with app.app_context():
        org = Organization.query.filter_by(name="Org Alpha").one()
        contractor = Contractor(
            organization_id=org.id,
            company_name="Remind Co",
            contact_name="Riley",
            email="riley@example.com",
            is_active=True,
        )
        db.session.add(contractor)
        db.session.flush()
        document = ComplianceDocument(
            organization_id=org.id,
            contractor_id=contractor.id,
            category="Insurance",
            document_type="CGL",
            policy_number="REM-1",
            expiry_date=date.today() + timedelta(days=30),
        )
        db.session.add(document)
        db.session.commit()

        first = process_reminders(app, today=date.today())
        second = process_reminders(app, today=date.today())

        assert first["dry_run"] == 1
        assert second["dry_run"] == 0
        assert ReminderLog.query.filter_by(document_id=document.id, reminder_offset_days=30).count() == 1
