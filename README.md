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
| Stage 10E (escalation processing) | +19 = **153** |

**153 tests passing.**

---

## API Reference

All endpoints are prefixed `/api/v1`. The interactive Swagger UI at `/docs` documents every request/response schema.

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/login` | Obtain JWT access token |
| `POST` | `/auth/refresh` | Refresh access token |

All protected endpoints require `Authorization: Bearer <token>` header.

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
