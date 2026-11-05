"""
Seed rich sample departments, offices, categories, policies, and grievances into grievance_db.
"""
import uuid
from datetime import datetime, timedelta, timezone
from app.core.database import SessionLocal
from app.core.roles import RoleName
from app.models.category import Category
from app.models.department import Department
from app.models.escalation import Escalation
from app.models.follow_up_action import FollowUpAction
from app.models.follow_up_policy import FollowUpPolicy
from app.models.follow_up_task import FollowUpTask
from app.models.grievance import Grievance
from app.models.grievance_assignment import GrievanceAssignment
from app.models.office import Office
from app.models.user import User
from app.utils.grievance_number import generate_grievance_number

def seed():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # 1. Departments
        dept_specs = [
            ("Police Department", "POL", "Law enforcement, public safety, and crime prevention across Karnataka"),
            ("Revenue Department", "REV", "Land records, revenue administration, disaster management, and citizen certificates"),
            ("Health & Family Welfare", "HFW", "Public hospitals, health centers, disease prevention, and welfare schemes"),
            ("Women & Child Development", "WCD", "Maternal health, child care centers (Anganwadis), and women empowerment"),
            ("Rural Development & Panchayati Raj", "RDPR", "Rural infrastructure, drinking water supply, sanitation, and MGNREGA"),
        ]
        depts = {}
        for name, code, desc in dept_specs:
            d = db.query(Department).filter_by(code=code).first()
            if not d:
                d = Department(name=name, code=code, description=desc, is_active=True)
                db.add(d)
                db.flush()
            depts[code] = d

        pol_dept = depts["POL"]

        # 2. Offices for Police Department
        office_specs = [
            ("Central Police Station", "CPS-01", "Infantry Road, Bengaluru", "Bengaluru Urban"),
            ("Vidhana Soudha Police Station", "VSPS-02", "Ambedkar Veedhi, Bengaluru", "Bengaluru Urban"),
            ("Cubbon Park Police Station", "CPPS-03", "Kasturba Road, Bengaluru", "Bengaluru Urban"),
            ("Ashok Nagar Police Station", "ANPS-04", "Museum Road, Bengaluru", "Bengaluru Urban"),
            ("Ulsoor Police Station", "UPS-05", "Old Madras Road, Bengaluru", "Bengaluru Urban"),
            ("Malleshwaram Police Station", "MPS-06", "8th Main Road, Malleshwaram, Bengaluru", "Bengaluru Urban"),
        ]
        offices = {}
        for name, code, addr, dist in office_specs:
            o = db.query(Office).filter_by(office_code=code).first()
            if not o:
                o = Office(
                    department_id=pol_dept.id,
                    name=name,
                    office_code=code,
                    address=addr,
                    district=dist,
                    state="Karnataka",
                    is_active=True,
                )
                db.add(o)
                db.flush()
            offices[code] = o

        central_office = offices["CPS-01"]

        # 3. Categories under Police Department
        cat_specs = [
            ("Cyber Crime & Online Fraud", "Financial phishing, unauthorized account debits, and identity theft"),
            ("Land & Property Dispute", "Illegal trespassing, boundary tampering, and unauthorized building"),
            ("Public Nuisance & Noise Pollution", "Loudspeakers after permissible hours and industrial noise"),
            ("Missing Person & Kidnapping", "Missing adults, runaway minors, and immediate search requests"),
            ("Theft, Burglary & Robbery", "House break-ins, vehicle theft, and snatching incidents"),
            ("Women Safety & Harassment", "Workplace harassment, eve-teasing, stalking, and domestic distress"),
        ]
        categories = {}
        for name, desc in cat_specs:
            c = db.query(Category).filter_by(department_id=pol_dept.id, name=name).first()
            if not c:
                c = Category(department_id=pol_dept.id, name=name, description=desc, is_active=True)
                db.add(c)
                db.flush()
            categories[name] = c

        # 4. Follow-up Policy for Police Department
        policy = db.query(FollowUpPolicy).filter_by(department_id=pol_dept.id).first()
        if not policy:
            policy = FollowUpPolicy(
                department_id=pol_dept.id,
                name="Standard Police Grievance Follow-up Policy",
                description="Default 3-day reminder and 7-day supervisor escalation policy",
                first_reminder_after_days=3,
                reminder_interval_days=2,
                escalation_after_days=7,
                max_reminders=3,
                is_active=True,
            )
            db.add(policy)
            db.flush()

        # 5. Fetch Users
        admin_user = db.query(User).filter_by(email="admin@jansetu.demo").first()
        pa_user = db.query(User).filter_by(email="pa@jansetu.demo").first()
        officer_user = db.query(User).filter_by(email="officer@jansetu.demo").first()

        # 6. Sample Grievances
        sample_grievances = [
            {
                "title": "Unauthorized debit of Rs. 48,500 via deceptive lottery SMS link",
                "desc": "Citizen received a deceptive SMS regarding lottery prize winnings. After clicking the phishing link and entering OTP, Rs. 48,500 was immediately debited from SBI savings account.",
                "summary": "Phishing fraud resulting in Rs. 48,500 financial loss via rogue OTP portal.",
                "cat": "Cyber Crime & Online Fraud",
                "source": "ONLINE_PORTAL",
                "priority": "HIGH",
                "status": "ASSIGNED",
                "office": "CPS-01",
                "assigned": officer_user.id if officer_user else None,
                "days_ago": 2,
            },
            {
                "title": "Illegal construction and boundary wall demolition on ancestral land",
                "desc": "Encroachers entered Survey No. 42/3 during night hours and demolished 20 meters of stone boundary fence. Threatening physical violence if reported.",
                "summary": "Boundary wall destruction and trespassing on ancestral agricultural land.",
                "cat": "Land & Property Dispute",
                "source": "IN_PERSON",
                "priority": "URGENT",
                "status": "IN_PROGRESS",
                "office": "VSPS-02",
                "assigned": officer_user.id if officer_user else None,
                "days_ago": 5,
            },
            {
                "title": "Persistent midnight commercial loudspeaker noise near residential hospital zone",
                "desc": "Commercial party venue on 80ft Road operates high-decibel acoustic systems beyond 1:30 AM every weekend, disturbing pediatric hospital ward.",
                "summary": "Repeated noise pollution violations beyond statutory curfew hours.",
                "cat": "Public Nuisance & Noise Pollution",
                "source": "HELPLINE",
                "priority": "MEDIUM",
                "status": "NEW",
                "office": "CPPS-03",
                "assigned": None,
                "days_ago": 1,
            },
            {
                "title": "Stolen two-wheeler motorcycle from metro station parking",
                "desc": "Honda Activa 6G (KA-01-EQ-9821) parked at Cubbon Park metro station lot was found missing upon returning from office at 7:30 PM.",
                "summary": "Vehicle theft reported at metro parking bay with CCTV coverage available.",
                "cat": "Theft, Burglary & Robbery",
                "source": "ONLINE_PORTAL",
                "priority": "MEDIUM",
                "status": "AWAITING_INFORMATION",
                "office": "CPPS-03",
                "assigned": officer_user.id if officer_user else None,
                "days_ago": 6,
            },
            {
                "title": "Repeated stalking and threatening WhatsApp calls from unknown number",
                "desc": "College student reporting persistent stalking along route from bus stop to residence, accompanied by obscene messages from VoIP numbers.",
                "summary": "Stalking and digital harassment targeting college student.",
                "cat": "Women Safety & Harassment",
                "source": "WRITTEN_LETTER",
                "priority": "URGENT",
                "status": "RESOLVED",
                "office": "ANPS-04",
                "assigned": officer_user.id if officer_user else None,
                "days_ago": 10,
                "resolved_days_ago": 1,
            },
            {
                "title": "Recovery of stolen laptop and gold ornaments in residential burglary",
                "desc": "Apartment burglary on 4th floor. Culprits apprehended at bus terminal; all stolen goods recovered and verified by owner.",
                "summary": "Burglary investigation concluded with full property recovery and charge sheet filed.",
                "cat": "Theft, Burglary & Robbery",
                "source": "IN_PERSON",
                "priority": "HIGH",
                "status": "CLOSED",
                "office": "UPS-05",
                "assigned": officer_user.id if officer_user else None,
                "days_ago": 15,
                "resolved_days_ago": 4,
                "closed_days_ago": 2,
            },
        ]

        for spec in sample_grievances:
            existing = db.query(Grievance).filter_by(title=spec["title"]).first()
            if not existing:
                date_rec = now - timedelta(days=spec["days_ago"])
                grv_num = generate_grievance_number(db)
                g = Grievance(
                    grievance_number=grv_num,
                    title=spec["title"],
                    description=spec["desc"],
                    neutral_summary=spec["summary"],
                    source=spec["source"],
                    original_language="en",
                    suggested_department_id=pol_dept.id,
                    confirmed_department_id=pol_dept.id,
                    category_id=categories[spec["cat"]].id if spec["cat"] in categories else None,
                    office_id=offices[spec["office"]].id if spec["office"] in offices else central_office.id,
                    assigned_to_user_id=spec["assigned"],
                    status=spec["status"],
                    priority=spec["priority"],
                    date_received=date_rec,
                    created_at=date_rec,
                    updated_at=date_rec,
                    created_by=pa_user.id if pa_user else None,
                )
                if spec.get("resolved_days_ago"):
                    g.resolved_at = now - timedelta(days=spec["resolved_days_ago"])
                if spec.get("closed_days_ago"):
                    g.closed_at = now - timedelta(days=spec["closed_days_ago"])

                db.add(g)
                db.flush()

                # Add assignment record if assigned
                if spec["assigned"] and pa_user:
                    assignment = GrievanceAssignment(
                        grievance_id=g.id,
                        assigned_to_user_id=spec["assigned"],
                        assigned_by_user_id=pa_user.id,
                        assigned_at=date_rec + timedelta(hours=2),
                        remarks="Assigned for immediate investigation and citizen outreach.",
                        is_active=(spec["status"] not in ("RESOLVED", "CLOSED")),
                    )
                    db.add(assignment)
                    db.flush()

                    # Add scheduled follow-up task
                    task = FollowUpTask(
                        grievance_id=g.id,
                        policy_id=policy.id,
                        assigned_to_user_id=spec["assigned"],
                        due_at=now + timedelta(days=2),
                        status="SCHEDULED",
                    )
                    db.add(task)
                    db.flush()

                    # Add an action for in-progress / resolved
                    if spec["status"] in ("IN_PROGRESS", "AWAITING_INFORMATION", "RESOLVED", "CLOSED"):
                        action = FollowUpAction(
                            grievance_id=g.id,
                            follow_up_task_id=task.id,
                            performed_by_user_id=spec["assigned"],
                            action_type="CITIZEN_CONTACTED",
                            description="Contacted complainant via official phone, recorded preliminary statement, and collected evidence.",
                            outcome="Citizen confirmed statement details and provided transaction reference ID.",
                            response_received=True,
                            action_taken_at=date_rec + timedelta(days=1),
                        )
                        db.add(action)

                    # Add escalation if urgent and older
                    if spec["priority"] == "URGENT" and spec["status"] == "IN_PROGRESS" and pa_user and officer_user:
                        esc = Escalation(
                            grievance_id=g.id,
                            follow_up_task_id=task.id,
                            escalated_from_user_id=officer_user.id,
                            escalated_to_user_id=pa_user.id,
                            reason="Critical property dispute with imminent risk of physical altercation. Field supervisor intervention required.",
                            trigger_date=now - timedelta(days=1),
                            escalated_at=now - timedelta(hours=12),
                            status="PENDING",
                        )
                        db.add(esc)

        db.commit()
        print("Rich demo data (departments, offices, categories, grievances, tasks, escalations) successfully seeded!")
    except Exception as e:
        db.rollback()
        print(f"Error seeding demo data: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed()
