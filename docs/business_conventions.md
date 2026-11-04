# Business Conventions and Architectural Decisions (Stage 8)
## Government Grievance Tracking and Follow-up Reminder System

This document specifies the business conventions, formatting standards, security rules, and architectural patterns implemented in Stage 8.

---

### 1. Grievance Number Format and Sequence

- **Format**: `GRV-YYYY-NNNNN`
  - `GRV`: Standard government grievance prefix.
  - `YYYY`: 4-digit calendar year (UTC) corresponding to the date of grievance registration.
  - `NNNNN`: 5-digit zero-padded sequence number starting at `00001` and extending up to `99999` (e.g., `GRV-2026-00001`).
- **Yearly Reset**:
  - The sequence restarts at `00001` on January 1st (00:00:00 UTC) of each new calendar year.
  - Queries calculate the sequence using only the current year's prefix (`GRV-YYYY-%`), ensuring that previous years' counters have no effect on new year registrations.
- **Concurrency Safety**:
  - The numbering generator acquires a PostgreSQL transaction-scoped advisory lock (`SELECT pg_advisory_xact_lock(:key)` with fixed application lock key `123456789`).
  - Concurrent incoming requests queue behind the active lock holder.
  - If a transaction is rolled back due to validation errors or database failures, the sequence is not leaked or advanced, and the advisory lock is automatically freed at transaction termination.

---

### 2. Approved System Roles and Permissions Matrix

The system implements 6 system authorization roles with granular permission mappings:

| Role Name | Scope & Description | Key Permissions |
|---|---|---|
| `SUPER_ADMIN` | Global cross-department administrator | Full access: manage departments, manage users, configure policies and templates, resolve escalations, view audit logs. |
| `DEPARTMENT_ADMIN` | Department-level administrator | Department-scoped user management, policy & template configuration, grievance reassignment, escalation handling, reporting. |
| `SUPERVISOR` | Supervisory review & escalation | Review grievances, assign/reassign cases, resolve grievances, escalate overdue cases, resolve escalations. |
| `OFFICER` | Grievance resolution officer | View assigned grievances, record follow-up actions (calls, visits, updates), update grievance progress. |
| `DATA_ENTRY_OPERATOR` | Front-desk & portal registrar | Register new grievances received in person, by post, or by phone; limited edit rights; no assignment or resolution powers. |
| `VIEWER` | Read-only audit & oversight | Read-only access to grievances, follow-up progress, notifications, escalations, and department metrics. |

#### Permission Set:
- **Departments**: `manage_departments`, `view_departments`
- **Users**: `manage_users`, `view_users`
- **Grievance**: `create_grievance`, `view_grievance`, `edit_grievance`, `assign_grievance`, `resolve_grievance`, `close_grievance`
- **Follow-up**: `manage_follow_ups`, `view_follow_ups`, `log_follow_up_action`
- **Notifications**: `manage_notifications`, `view_notifications`
- **Escalations**: `escalate_grievance`, `view_escalations`, `resolve_escalation`
- **Policies & Templates**: `manage_policies`, `manage_templates`
- **Audit & Reports**: `view_reports`, `view_audit_logs`

---

### 3. Role Seeder

- **Idempotency**: The seeder (`app/services/seeder.py` / `seed.py`) inspects existing rows by name. Missing roles are inserted in a single atomic transaction; existing roles are untouched.
- **Execution**: Can be executed via command line (`python seed.py`) or imported directly in deployment automation scripts without risking data duplication.

---

### 4. Authentication Architecture

- **Password Security**:
  - Handled using `bcrypt` (work factor: 12 rounds) with individual salts.
  - Plaintext passwords are never stored, logged, or serialized.
  - Constant-time verification is performed against a dummy hash when non-existent emails are queried, preventing timing side-channel attacks.
- **Token Strategy**:
  - **Access Token**: JSON Web Token (JWT) signed with HMAC-SHA256 (`HS256`). Carries `sub` (User UUID), `role` (Role Name), `type` ("access"), issue time (`iat`), and expiration time (`exp`, default 60 minutes).
  - **Refresh Token**: Longer-lived JWT (default 7 days) carrying `sub` and `type` ("refresh") used exclusively at `/api/v1/auth/refresh`.
- **Account Verification and Status**:
  - Accounts with `is_active = False` are rejected with HTTP 403 Forbidden ("This account has been deactivated.").
  - Accounts with `is_verified = False` are rejected with HTTP 403 Forbidden ("This account has not been verified.").
  - Successful authentication updates `last_login_at` with current UTC timestamp.

---

### 5. Notification Idempotency Key Construction

To enforce the business rule that each reminder occurrence must have a unique record while preventing duplicate reminders under retry loops or concurrent workers, the idempotency key is constructed deterministically:

$$\text{idempotency\_key} = \text{grievance\_hex} : \text{task\_hex} : \text{channel} : \text{notification\_type} : \text{scheduled\_date}$$

- **Format Details**:
  - `grievance_hex`: 32-character hex UUID of the grievance (e.g. `11111111222233334444555555555555`).
  - `task_hex`: 32-character hex UUID of the `follow_up_task`, or `"none"` if unlinked.
  - `channel`: Notification delivery channel (`EMAIL`, `SMS`, `WHATSAPP`, `IN_APP`, `POSTAL`).
  - `notification_type`: Classification (`REMINDER`, `ESCALATION`, `STATUS_UPDATE`, `ACKNOWLEDGMENT`).
  - `scheduled_date`: ISO-8601 UTC date string (`YYYY-MM-DD`).
- **Database Enforcement**:
  - Unique index `ix_notifications_idempotency_key` on `notifications.idempotency_key` rejects duplicate submissions at the database engine level.
  - Distinct occurrences on different days produce different date strings and are correctly permitted.

---

### 6. Office Geography and State Configuration

- **Decision**: The `offices` table retains `state` with a default of `"Karnataka"` for the Police Department prototype.
- **Extensibility**: The column is typed as `String(100)` rather than an Enum or restricted check constraint. Subordinate offices from other states (e.g., Maharashtra, Tamil Nadu) or central agencies can be stored seamlessly without schema migration.

