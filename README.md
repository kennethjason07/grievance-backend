# Government Grievance Tracking and Follow-up Reminder System — Backend API

A production-ready FastAPI backend for a government grievance management platform. It supports the full grievance lifecycle — from registration through assignment, follow-up tracking, reminder generation, and escalation processing — with comprehensive audit logging and role-based access control.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Local Development Setup](#local-development-setup)
- [Environment Variables](#environment-variables)
- [Database Setup](#database-setup)
- [Running the Server](#running-the-server)
- [Running Tests](#running-tests)
- [OCR Setup & Configuration (Stage 1)](#ocr-setup--configuration-stage-1)
- [API Reference](#api-reference)
- [Role and Permission Model](#role-and-permission-model)
- [Grievance Lifecycle](#grievance-lifecycle)
- [Follow-up and Escalation Engine](#follow-up-and-escalation-engine)
- [Project Structure](#project-structure)
- [Integration Notes for Frontend](#integration-notes-for-frontend)

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Framework | FastAPI 0.162 |
| Language | Python 3.11+ |
| ORM | SQLAlchemy 2.x |
| Database | PostgreSQL 15+ |
| DB Driver | psycopg 3 (`psycopg[binary]`) |
| Migrations | Alembic |
| Auth | JWT (python-jose) + bcrypt |
| Validation | Pydantic v2 |
| Testing | pytest + FastAPI TestClient |

---

## Architecture Overview

```
app/
├── api/v1/          # FastAPI routers (one per domain)
├── core/            # Config, DB session, security, roles/permissions
├── models/          # SQLAlchemy ORM models
├── schemas/         # Pydantic request/response schemas
├── services/        # Business logic layer
└── utils/           # Grievance number generation, notification idempotency
```

---

## Prerequisites

- **Python 3.11 or higher**
- **PostgreSQL 15 or higher** (must be running locally)
- `git`
- **Tesseract OCR 5.x** with `eng` (English) and `kan` (Kannada) language data files
  - *Note:* Digital PDFs and DOCX files extract embedded text natively via PyMuPDF and python-docx without Tesseract. However, **Tesseract is required for scanned petitions, mobile phone photos, and image attachments (`.png`, `.jpg`, `.jpeg`)**.

---

## Local Development Setup

### 1. Clone the repository

```bash
git clone https://github.com/kennethjason07/grievance-backend.git
cd grievance-backend
```

### 2. Create and activate a virtual environment

```bash
# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Open `.env` and set your values (see [Environment Variables](#environment-variables) below).

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `Government Grievance Tracking System` | Application display name |
| `APP_ENV` | `development` | Environment tag (`development` / `production`) |
| `DEBUG` | `True` | Enable verbose error responses |
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/grievance_db` | Main database connection string using psycopg 3 driver |
| `SECRET_KEY` | *(change this!)* | JWT signing key — **must be at least 32 characters in production** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | JWT access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | JWT refresh token lifetime |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:8000` | Comma-separated list of allowed frontend origins |
| `UPLOAD_DIR` | `uploads` | Local directory for uploaded grievance files |
| `MAX_UPLOAD_SIZE_BYTES` | `26214400` (25 MB) | Maximum permitted file upload size |
| `TESSERACT_CMD` | `null` | Absolute path to `tesseract` binary (required on Windows if not in PATH) |
| `TESSDATA_PREFIX` | `null` | Directory containing `tessdata/*.traineddata` language files |
| `OCR_DEFAULT_LANGUAGES` | `eng+kan` | Tesseract language codes to load (bilingual English + Kannada) |
| `MAX_OCR_PAGES` | `20` | Max page count allowed per PDF document for OCR processing |
| `MAX_RENDER_DPI` | `200` | Render DPI for converting scanned PDF pages to images for OCR |
| `OCR_TIMEOUT_SECONDS` | `120` | OCR extraction execution timeout |
| `TRANSLATION_PROVIDER` | `mock` | Translation provider (`mock` or `indictrans2`) |

> **Security note:** Never commit `.env` to version control. It is already listed in `.gitignore`.

---

## Database Setup

### 1. Create the main database

```sql
-- Connect to PostgreSQL and run:
CREATE DATABASE grievance_db;
```

### 2. Apply migrations

```bash
alembic upgrade head
```

This creates all tables, indexes, constraints, and partial unique indexes in one step.

### 3. Seed system roles

```bash
python seed.py
```

This idempotently seeds the six system roles (`SUPER_ADMIN`, `DEPARTMENT_ADMIN`, `SUPERVISOR`, `OFFICER`, `DATA_ENTRY_OPERATOR`, `VIEWER`). Safe to run multiple times.

---

## Running the Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

| URL | Purpose |
|-----|---------|
| `http://localhost:8000/docs` | Swagger UI (interactive API docs) |
| `http://localhost:8000/redoc` | ReDoc API documentation |
| `http://localhost:8000/health` | Application health check |
| `http://localhost:8000/api/v1/health` | API v1 health check |
| `http://localhost:8000/api/v1/health/db` | Database connectivity check |

---

## Running Tests

### Create the test database (one time)

```sql
CREATE DATABASE grievance_test_db;
```

The test suite automatically runs migrations and seeds the test database in isolation. It never touches `grievance_db`.

### Run all tests

```bash
.\venv\Scripts\pytest.exe -q          # Windows
pytest -q                              # macOS / Linux
```

### Run a specific stage's tests

```bash
pytest tests/test_stage10e.py -v
```

### Current test baseline

| Stage | Tests |
|-------|-------|
| Stages 1–9E (core + lifecycle) | 75 |
| Stage 10A (follow-up policies) | +11 = 86 |
| Stage 10B (follow-up tasks) | +14 = 100 |
| Stage 10C (follow-up actions) | +18 = 118 |
| Stage 10D (reminder generation) | +16 = 134 |
| Stage 10E (escalation processing) | +19 = 153 |
| Documents & Attachments (`test_documents.py`) | +15 = 168 |
| Stage 1 OCR Text Extraction (`test_ocr.py`) | +22 = 190 |
| Stage 2 Translation Pipeline (`test_translation.py`) | +15 = **205** |

**205 tests passing.**

---

---

## OCR Setup & Configuration (Stage 1)

JanSetu processes citizen grievance petitions submitted as digital PDFs, scanned documents, camera images (PNG/JPG), and DOCX files. Petitions are frequently written in **English**, **Kannada (ಕನ್ನಡ)**, or a bilingual mix of both.

### How the Ingestion Pipeline Works

```
Uploaded File (.pdf, .docx, .png, .jpg, .txt)
   │
   ├─► DOCX / TXT: Extracted directly via python-docx / utf-8 text stream
   │
   ├─► Digital PDF (has selectable text):
   │     └─► PyMuPDF extracts embedded text directly (Fast, 100% accurate, no Tesseract needed)
   │
   └─► Scanned PDF Pages or Images (.png, .jpg, .jpeg):
         ├─► Step 1: Render PDF page to high-res image (200 DPI)
         ├─► Step 2: OpenCV Preprocessing (Grayscale ➔ Gaussian Blur ➔ Otsu Thresholding ➔ Deskewing)
         ├─► Step 3: Tesseract OCR with bilingual language models (`eng+kan`)
         └─► Step 4: Extract text and calculate confidence score
```

---

### Step-by-Step Tesseract Installation Guide

#### 1. Windows Installation

1. **Download the Tesseract 5.x 64-bit installer**:
   - Official UB-Mannheim Windows build: [https://github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki)
   - Direct Installer: `tesseract-ocr-w64-setup-5.x.x.exe`

2. **Select Language Packs during installation (CRITICAL)**:
   - When the installer reaches the **"Choose Components"** screen:
   - Expand **"Additional script data (download)"** or **"Additional language data (download)"**.
   - Check the box for **"Kannada"** (`kan`).
   - English (`eng`) is included by default.

3. **Verify the installation directory**:
   - By default, Tesseract installs to:
     `C:\Program Files\Tesseract-OCR`
   - Ensure `C:\Program Files\Tesseract-OCR\tesseract.exe` exists.
   - Ensure `C:\Program Files\Tesseract-OCR\tessdata\eng.traineddata` and `kan.traineddata` exist.

4. **If you already installed Tesseract without Kannada**:
   - Download `kan.traineddata` manually from the official Tesseract repo:
     [https://github.com/tesseract-ocr/tessdata/raw/main/kan.traineddata](https://github.com/tesseract-ocr/tessdata/raw/main/kan.traineddata)
   - Move `kan.traineddata` into `C:\Program Files\Tesseract-OCR\tessdata\` (requires Admin permissions).

5. **Configure your `.env` file**:
   ```env
   TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
   TESSDATA_PREFIX="C:\Program Files\Tesseract-OCR\tessdata"
   OCR_DEFAULT_LANGUAGES="eng+kan"
   ```
   > **Note on Windows path formatting:** In `.env`, use double backslashes (`\\`) or forward slashes (`/`), e.g. `C:/Program Files/Tesseract-OCR/tesseract.exe`.

---

#### 2. Ubuntu / Debian Linux Installation

1. Install Tesseract, English, and Kannada language packs via `apt`:
   ```bash
   sudo apt update
   sudo apt install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-kan libgl1
   ```
   > **Note on `libgl1`:** OpenCV (`cv2`) requires system OpenGL shared libraries. Installing `libgl1` prevents `ImportError: libGL.so.1: cannot open shared object file`.

2. Configure `.env` (optional on Linux if `tesseract` is in standard `/usr/bin` PATH):
   ```env
   TESSERACT_CMD="/usr/bin/tesseract"
   TESSDATA_PREFIX="/usr/share/tesseract-ocr/5/tessdata"
   OCR_DEFAULT_LANGUAGES="eng+kan"
   ```

---

#### 3. macOS Installation (Homebrew)

1. Install via Homebrew:
   ```bash
   brew install tesseract
   brew install tesseract-lang
   ```

2. Configure `.env`:
   ```env
   # Apple Silicon (M1/M2/M3)
   TESSERACT_CMD="/opt/homebrew/bin/tesseract"
   TESSDATA_PREFIX="/opt/homebrew/share/tessdata"
   OCR_DEFAULT_LANGUAGES="eng+kan"

   # Intel Mac
   # TESSERACT_CMD="/usr/local/bin/tesseract"
   # TESSDATA_PREFIX="/usr/local/share/tessdata"
   ```

---

### Verification and Health Check

Once installed, verify the setup using any of these 3 methods:

#### Method A: Command-line Check
Run in your terminal:
```bash
tesseract --version
tesseract --list-langs
```
**Expected output includes:**
```
List of available languages in "...":
eng
kan
osd
```

#### Method B: Built-in Health Endpoint Check
Start the backend server and call the OCR status diagnostic endpoint:
```bash
curl http://localhost:8000/api/v1/documents/ocr/status
```
**Expected JSON response:**
```json
{
  "installed": true,
  "version": "5.3.3",
  "languages": ["eng", "kan", "osd"],
  "tesseract_cmd": "C:\Program Files\Tesseract-OCR\tesseract.exe",
  "supports_english": true,
  "supports_kannada": true
}
```

#### Method C: Run the OCR Pytest Suite
```bash
pytest tests/test_ocr.py -v
```
All **22 tests** should pass.

---

### OCR API Endpoints

| Method | Endpoint | Description | Auth / Role |
|--------|----------|-------------|-------------|
| `POST` | `/api/v1/documents/extract-text` | Ad-hoc extraction directly from uploaded file before grievance is registered. Accepts `multipart/form-data` with `file` and optional `languages` form fields. | `CREATE_GRIEVANCE` or `VIEW_GRIEVANCE` |
| `POST` | `/api/v1/documents/{document_id}/ocr` | Trigger OCR on a saved grievance document attachment. | `CREATE_GRIEVANCE` or `EDIT_GRIEVANCE` |
| `GET` | `/api/v1/documents/{document_id}/ocr` | Retrieve extracted text, page-by-page data, and review status. | `VIEW_GRIEVANCE` |
| `PATCH` | `/api/v1/documents/{document_id}/ocr/review` | Save human-corrected / reviewed text without mutating raw OCR text. | `CREATE_GRIEVANCE` or `EDIT_GRIEVANCE` |
| `GET` | `/api/v1/documents/ocr/status` | System diagnostic endpoint reporting Tesseract installation and language support. | Authenticated user |

---

### Common Developer Troubleshooting

| Symptom / Error | Root Cause | Solution |
|---|---|---|
| `"Tesseract OCR engine is not installed or configured on the server"` | `tesseract.exe` was not found in system `PATH` or `TESSERACT_CMD` is empty/incorrect. | Install Tesseract using the OS instructions above and set `TESSERACT_CMD` in your `.env`. |
| `"Tesseract language traineddata for ['kan'] is missing"` | Tesseract is installed, but the Kannada language model `kan.traineddata` was omitted. | Download `kan.traineddata` from [tessdata repository](https://github.com/tesseract-ocr/tessdata) and place in your `tessdata` directory. |
| `ImportError: libGL.so.1` (Linux Docker / Ubuntu) | OpenCV image preprocessing requires X11 / OpenGL libraries on headless Linux. | Run `sudo apt install -y libgl1` or switch to `opencv-python-headless` in requirements. |
| Unescaped backslashes in `.env` | Windows paths with single backslashes (e.g. `C:\temp`) treated as escape sequences. | Use double backslashes `C:\\Program Files\\...` or forward slashes `C:/Program Files/...`. |
| `422 Unprocessable Content` on file upload | Frontend sending JSON header with FormData or missing required form keys. | Ensure frontend uses `FormData` with key `file` and does **not** override `Content-Type: multipart/form-data`. |

## API Reference

All endpoints are prefixed `/api/v1`. The interactive Swagger UI at `/docs` documents every request/response schema.

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/login` | Obtain JWT access token |
| `POST` | `/auth/refresh` | Refresh access token |

All protected endpoints require `Authorization: Bearer <token>` header.

### Documents & OCR (Stage 1)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/documents/extract-text` | Direct text extraction / OCR on raw uploaded file (pre-registration) |
| `GET` | `/documents/ocr/status` | OCR engine installation & language model health status |
| `POST` | `/grievances/{id}/documents` | Upload and attach document file to grievance |
| `GET` | `/grievances/{id}/documents` | List attachments for a grievance |
| `GET` | `/documents/{id}` | Get document metadata |
| `GET` | `/documents/{id}/download` | Download or preview binary attachment file |
| `POST` | `/documents/{id}/ocr` | Trigger OCR extraction on an attached document |
| `GET` | `/documents/{id}/ocr` | Get document OCR results and review state |
| `PATCH` | `/documents/{id}/ocr/review` | Save human-edited reviewed text |

### Core Resources

| Domain | Base Path | Description |
|--------|-----------|-------------|
| Departments | `/departments` | CRUD for departments |
| Offices | `/offices` | CRUD for subordinate offices |
| Users | `/users` | User management and role assignment |
| Grievances | `/grievances` | Register, retrieve, update grievances |
| Assignments | `/assignments` | Assign and reassign grievances to officers |
| Lifecycle | `/lifecycle` | Status transitions (review → resolve → close) |

### Follow-up Engine

| Domain | Base Path | Description |
|--------|-----------|-------------|
| Policies | `/follow-up-policies` | Configure follow-up intervals and escalation rules |
| Tasks | `/follow-up-tasks` | Create and manage scheduled follow-up tasks |
| Actions | `/follow-up-tasks/{id}/actions` | Record call attempts, field visits, responses |

### Escalation

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/escalations` | List escalations (department-scoped) |
| `GET` | `/escalations/{id}` | Get single escalation |
| `PATCH` | `/escalations/{id}/acknowledge` | Acknowledge an escalation |
| `PATCH` | `/escalations/{id}/resolve` | Resolve an escalation |
| `PATCH` | `/escalations/{id}/dismiss` | Dismiss with mandatory reason |
| `POST` | `/escalations/process-due` | Trigger batch escalation processing (SUPER_ADMIN) |

### Notifications

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/notifications` | List notifications for current user |
| `POST` | `/notifications/process-due` | Trigger reminder generation batch (SUPER_ADMIN) |

---

## Role and Permission Model

Six fixed system roles are seeded at startup:

| Role | Scope | Key Capabilities |
|------|-------|-----------------|
| `SUPER_ADMIN` | System-wide | Full access, cross-department |
| `DEPARTMENT_ADMIN` | Own department | Manage users, policies, grievances, escalations |
| `SUPERVISOR` | Own department | Assign, resolve, escalate, view reports |
| `OFFICER` | Assigned grievances | Update status, record follow-up actions |
| `DATA_ENTRY_OPERATOR` | Own entries | Register and edit grievances |
| `VIEWER` | Read-only | View grievances, follow-ups, escalations |

Roles are enforced at the service layer. JWT tokens carry the user's role. Roles are **not** user-configurable at runtime.

---

## Grievance Lifecycle

```
NEW → UNDER_REVIEW → ASSIGNED → IN_PROGRESS → RESOLVED → CLOSED
                              ↘ AWAITING_INFORMATION ↗
```

| Status | Trigger |
|--------|---------|
| `NEW` | Grievance registered |
| `UNDER_REVIEW` | Department confirms receipt |
| `ASSIGNED` | Assigned to an officer |
| `AWAITING_INFORMATION` | Officer requests more info |
| `IN_PROGRESS` | Officer begins working |
| `RESOLVED` | Officer marks resolved |
| `CLOSED` | Admin closes the case |

**Reopening:** Closed grievances can only be reopened by `SUPER_ADMIN` or `DEPARTMENT_ADMIN`. A mandatory reason is required and the event is recorded in `activity_logs`.

---

## Follow-up and Escalation Engine

### Follow-up Policy fields

| Field | Meaning |
|-------|---------|
| `first_reminder_after_days` | Days from assignment anchor before first reminder notification |
| `reminder_interval_days` | Days between subsequent reminders |
| `max_reminders` | Maximum reminders per cycle (0 = disabled) |
| `escalation_after_days` | Days from assignment anchor before escalation |

### Clock anchor

The **initial grievance assignment timestamp** (`GrievanceAssignment.assigned_at` of the first assignment) is the single SLA clock anchor for both reminders and escalations.

- **Reassignment does NOT reset this clock.**
- Escalation timing is never derived from `task.due_at`.

### Escalation recipient selection

1. Active `SUPERVISOR` in the same department, preferring the supervisor whose office matches the grievance office.
2. If no supervisor, falls back to `DEPARTMENT_ADMIN`.
3. If neither is found, the failure is logged to `activity_logs` and the grievance is skipped (no crash).

Escalation **never** auto-reassigns the grievance.

### Idempotency

A partial unique database index prevents duplicate active escalations for the same grievance. Reminder notifications use a composite idempotency key to prevent duplicate sends.

---

## Project Structure

```
grievance-backend/
├── alembic/                        # Migration scripts
│   └── versions/
│       ├── a01c9af298b3_create_grievance_system_core_tables.py
│       └── b02d1ef345a1_add_partial_unique_index_active_escalations.py
├── app/
│   ├── api/v1/                     # Routers
│   │   ├── actions.py
│   │   ├── assignments.py
│   │   ├── auth.py
│   │   ├── departments.py
│   │   ├── escalations.py
│   │   ├── grievances.py
│   │   ├── lifecycle.py
│   │   ├── notifications.py
│   │   ├── offices.py
│   │   ├── policies.py
│   │   ├── router.py
│   │   ├── tasks.py
│   │   └── users.py
│   ├── core/
│   │   ├── config.py               # Settings (pydantic-settings)
│   │   ├── database.py             # SQLAlchemy engine and session
│   │   ├── roles.py                # Role constants and permission map
│   │   └── security.py             # JWT helpers, password hashing
│   ├── models/                     # SQLAlchemy ORM models
│   │   ├── activity_log.py
│   │   ├── category.py
│   │   ├── department.py
│   │   ├── document.py
│   │   ├── escalation.py
│   │   ├── follow_up_action.py
│   │   ├── follow_up_policy.py
│   │   ├── follow_up_task.py
│   │   ├── grievance.py
│   │   ├── grievance_assignment.py
│   │   ├── message_template.py
│   │   ├── notification.py
│   │   ├── office.py
│   │   ├── role.py
│   │   └── user.py
│   ├── schemas/                    # Pydantic v2 schemas
│   ├── services/                   # Business logic
│   │   ├── action_service.py
│   │   ├── assignment_service.py
│   │   ├── audit.py
│   │   ├── escalation_service.py
│   │   ├── grievance_lifecycle.py
│   │   ├── grievance_service.py
│   │   ├── notification_service.py
│   │   ├── policy_service.py
│   │   ├── task_service.py
│   │   └── user_service.py
│   └── utils/
│       ├── grievance_number.py     # GRV-YYYY-NNNNN generator with advisory lock
│       └── notification_idempotency.py
├── docs/
│   └── business_conventions.md
├── tests/                          # Pytest test suite (153 tests)
├── .env.example                    # Environment variable template
├── .gitignore
├── alembic.ini
├── requirements.txt
└── seed.py                         # Role seeder script
```

---

## Integration Notes for Frontend

### Base URL

```
http://localhost:8000/api/v1
```

In production, replace with the deployed backend URL and update `CORS_ORIGINS` in `.env` accordingly.

### Authentication flow

1. `POST /auth/login` with `{ "email": "...", "password": "..." }` → returns `{ "access_token": "...", "token_type": "bearer" }`
2. Include `Authorization: Bearer <access_token>` on every subsequent request.
3. On 401, call `POST /auth/refresh` to obtain a new access token.

### Pagination

List endpoints return:

```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "size": 20,
  "pages": 5
}
```

Use query params `?page=1&size=20` to paginate.

### Timestamps

All timestamps in API responses are **UTC ISO 8601** (e.g., `"2026-11-04T13:00:00Z"`). The frontend is responsible for converting to local time for display.

### Error responses

| HTTP Status | Meaning |
|-------------|---------|
| 400 | Bad request / business rule violation |
| 401 | Missing or invalid JWT |
| 403 | Authenticated but insufficient permissions |
| 404 | Resource not found |
| 409 | Conflict (e.g., duplicate escalation) |
| 422 | Validation error (Pydantic) |
| 500 | Internal server error |

Error body:

```json
{ "detail": "Human-readable error message" }
```

### Key domain IDs

All primary keys are **UUID v4** strings. Store and transmit them as strings.

### Interactive docs

Point the browser at `http://localhost:8000/docs` to explore every endpoint with live request/response schemas.

---

## License

This project is developed as a government prototype system. Contact the project owner for licensing information.
