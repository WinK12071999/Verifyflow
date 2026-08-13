# VerifyFlow

VerifyFlow is a beginner-friendly web app that helps a company track **subcontractor insurance and compliance documents** and send reminders before those documents expire.

This first version is an **MVP** (minimum viable product). It is meant for learning and local testing.

---

## Important limitations (read this first)

- This is an MVP for local testing.
- It is **not legal advice**.
- It is **not** an insurance-verification authority.
- AI extraction can be wrong.
- A **human must verify** insurance details.
- Local **SQLite** and local **uploads/** storage are **not suitable for production**.
- Production requires:
  - PostgreSQL
  - Secure cloud file storage
  - Backups
  - Encryption
  - Malware scanning
  - Security testing
  - Privacy and legal review
  - Proper Canadian privacy compliance

Local uploads are only suitable for testing and must later be replaced by secure cloud storage.

---

## What is this software? (simple explanations)

| Term | Plain-English meaning |
|------|------------------------|
| **SaaS** | Software you use in a web browser instead of installing a desktop program. |
| **Python** | The programming language that runs the server logic. |
| **Flask** | A small Python toolkit for building websites and APIs. |
| **HTML** | The structure of each web page (headings, forms, tables). |
| **CSS** | The styling (colors, spacing, layout). |
| **Database** | An organized place to store records (subcontractors, documents, users). |
| **SQLite** | A simple database stored as a file on your computer. Good for learning. |
| **PostgreSQL** | A stronger database used for real production systems. |
| **Virtual environment** | A private folder of Python packages for this project only, so they do not mix with other projects. |
| **API** | A way for programs to talk to each other (for example, VerifyFlow asking OpenAI to read document text). |
| **OCR** | Software that reads text from images/scans. **Not included yet** in this MVP. |
| **localhost** | Your own computer acting as the web server (`http://127.0.0.1:5000`). |
| **Debug mode** | A developer setting that shows detailed errors. Helpful while learning. **Do not use debug mode for real customer data.** |

---

## Folder structure

```text
verifyflow/
├── app.py                 # The whole Flask application
├── requirements.txt       # Python packages to install
├── .env.example           # Sample settings (copy to .env)
├── .gitignore             # Files git should ignore
├── README.md              # This guide
├── templates/             # HTML pages
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── contractors.html
│   ├── contractor_form.html
│   ├── contractor_detail.html
│   ├── document_form.html
│   ├── reports.html
│   ├── error.html
│   └── setup_error.html
├── static/
│   └── styles.css         # Visual styling
├── uploads/
│   └── .gitkeep           # Keeps the folder in git (files stay local)
└── tests/
    └── test_app.py        # Automated checks
```

---

## Features included

1. Secure admin login (password hashed; credentials from `.env`)
2. Organization scoping (users only see their own organization’s records)
3. Subcontractor add / edit / view / activate / delete
4. Compliance documents (Insurance first; also Government, Safety, Training, Licence, Fleet, Other)
5. File uploads (PDF/PNG/JPG/JPEG, max 10 MB, safe UUID filenames)
6. Expiry tracking: Valid / Expiring Soon / Expired with day counts
7. Dashboard with counts and upcoming expirations
8. Reminder command: `flask --app app send-reminders`
9. PDF text extraction with optional OpenAI structured extraction
10. CSV reports with filters
11. CSRF protection, audit log, 403/404/500 pages
12. Flask-Migrate support for future database changes
13. Automated tests with pytest

---

## Exact Windows + Visual Studio Code setup

### 1. Install Python

1. Open [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Download the latest Python 3 release
3. Run the installer
4. **Check the box** “Add python.exe to PATH”
5. Finish installation

### 2. Install Visual Studio Code

1. Open [https://code.visualstudio.com/](https://code.visualstudio.com/)
2. Download and install VS Code

### 3. Install the Microsoft Python extension

1. Open VS Code
2. Click the Extensions icon
3. Search for **Python** by Microsoft
4. Click Install

### 4. Open the whole project folder

1. In VS Code: **File → Open Folder…**
2. Choose the `verifyflow` folder (the one that contains `app.py`)
3. Click Open

### 5. Open the terminal

1. In VS Code: **Terminal → New Terminal**
2. You should see a prompt inside the `verifyflow` folder

### 6. Create a virtual environment

This creates a private Python toolbox for this project.

```powershell
python -m venv .venv
```

### 7. Activate the virtual environment

```powershell
.venv\Scripts\Activate.ps1
```

If PowerShell says scripts are disabled, run this once as Administrator, then try again:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

You should see `(.venv)` at the start of your terminal line.

### 8. Install requirements

```powershell
pip install -r requirements.txt
```

### 9. Copy `.env.example` to `.env`

```powershell
Copy-Item .env.example .env
```

Open `.env` in VS Code and fill in:

- `SECRET_KEY` — a long random string  
  Generate one with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

- `ADMIN_EMAIL` — your admin login email (use a normal domain like `admin@example.com`; avoid `.local`)
- `ADMIN_PASSWORD` — a strong password (at least 8 characters)
- Leave OpenAI and SMTP blank for now if you are only testing locally

### 10. Initialize the database (Flask-Migrate)

```powershell
flask --app app db init
flask --app app db migrate -m "Initial database"
flask --app app db upgrade
```

### 11. Create the administrator

```powershell
flask --app app init-db
```

This creates/updates the organization and admin user from your `.env` values.  
The password is stored as a **hash**, never as plain text.

### 12. Run the app

```powershell
flask --app app run --debug
```

### 13. Open localhost in the browser

Visit: [http://127.0.0.1:5000](http://127.0.0.1:5000)

Sign in with `ADMIN_EMAIL` and `ADMIN_PASSWORD`.

---

## macOS / Linux quick commands

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
flask --app app db init
flask --app app db migrate -m "Initial database"
flask --app app db upgrade
flask --app app init-db
flask --app app run --debug
```

---

## Sending reminders

Dry-run (prints emails in the terminal when SMTP is not configured):

```powershell
flask --app app send-reminders
```

Reminder schedule:

- 90, 60, 30, 14, 7, and 1 day before expiry
- On the expiry date
- 1 day after expiry

The same reminder is not sent twice (`ReminderLog`).

---

## Running tests

```powershell
pytest -q
```

---

## Optional OpenAI extraction

If `OPENAI_API_KEY` is set, VerifyFlow can send extracted PDF text to OpenAI and ask for structured JSON. Results are validated and marked **unverified** until a human confirms them.

Without an API key, simple regular expressions try to find expiry date, policy number, and coverage amount from text-based PDFs.

Images and scanned PDFs without text are **not** read automatically. OCR is not included yet.

---

## Deploy a lasting demo on Render

Use Render when the client needs a link that works even if your laptop is closed. **Do not use Vercel** for this Flask app.

1. Push this project to GitHub (do **not** commit `.env`).
2. Sign up at [https://render.com](https://render.com) with GitHub.
3. **New → Web Service** → connect your VerifyFlow repo.
4. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `flask --app app db upgrade && flask --app app init-db && gunicorn app:app --bind 0.0.0.0:$PORT`
   - **Plan:** Free
5. Add Environment Variables:

| Key | Value |
|-----|--------|
| `SECRET_KEY` | long random string (or let Render generate one) |
| `ADMIN_EMAIL` | e.g. `admin@example.com` |
| `ADMIN_PASSWORD` | strong password, 8+ characters |
| `ORGANIZATION_NAME` | `VerifyFlow` |
| `DATABASE_URL` | `sqlite:///compliance.db` |
| `APP_BASE_URL` | your Render URL after first deploy, e.g. `https://verifyflow.onrender.com` |

6. Deploy, open the Render URL, sign in with the admin email/password you set.
7. Free tier may sleep after idle; the first visit can take 30–60 seconds.

SQLite and local uploads on Render are **demo-only**. Data can reset when the service restarts.

---

## Common errors and how to fix them

| Problem | Likely fix |
|---------|------------|
| `Missing required setting 'SECRET_KEY'` | Copy `.env.example` to `.env` and fill required values, then restart. |
| `Activate.ps1` cannot be loaded | Run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| `flask` is not recognized | Activate `.venv`, then reinstall: `pip install -r requirements.txt` |
| Login fails after changing `.env` password | Run `flask --app app init-db` again to refresh the admin hash. |
| Port 5000 already in use / blank page after login on Mac | On macOS, AirPlay often uses port 5000 and returns a blank 403 page. Run on another port: `flask --app app run --debug --port 5001` then open http://127.0.0.1:5001. Or turn off AirPlay Receiver in System Settings → General → AirDrop & Handoff. |
| File upload rejected | Use PDF/PNG/JPG/JPEG and keep files under 10 MB. |
| Database migration errors on first run | Delete the `migrations` folder only if you are still learning locally, then run `db init` / `migrate` / `upgrade` again. Never do this with real data. |

---

## What is safe for testing only

- Local SQLite database file
- Files saved in `uploads/`
- Debug mode
- Dry-run reminder emails
- Optional OpenAI suggestions without human review

## What must change before accepting real customer documents

- Move to PostgreSQL
- Store files in secured cloud storage (not a local folder)
- Turn off debug mode
- Use HTTPS and secure session cookies
- Add backups, encryption, malware scanning
- Complete security testing
- Complete privacy/legal review for Canadian requirements
- Keep a human verification workflow for every insurance document

---

## License / use

Built as a learning MVP. Review with legal, privacy, and security professionals before any real-world deployment.
