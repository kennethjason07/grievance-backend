"""
Seed initial demo department, office, and users into grievance_db.
Idempotent script safe to run repeatedly.
"""
import logging
from app.core.database import SessionLocal
from app.core.roles import RoleName
from app.core.security import hash_password
from app.models.department import Department
from app.models.office import Office
from app.models.role import Role
from app.models.user import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def seed_demo_users():
    db = SessionLocal()
    try:
        # 1. Department: Police Department
        dept = db.query(Department).filter_by(code="POL").first()
        if not dept:
            dept = Department(
                name="Police Department",
                code="POL",
                description="Government of Karnataka Police Department",
                is_active=True,
            )
            db.add(dept)
            db.flush()
            logger.info("Created department 'POL'")
        else:
            logger.info("Department 'POL' already exists")

        # 2. Office: Central Police Station
        office = db.query(Office).filter_by(office_code="CPS-01").first()
        if not office:
            office = Office(
                department_id=dept.id,
                name="Central Police Station",
                office_code="CPS-01",
                district="Bengaluru Urban",
                state="Karnataka",
                address="Infantry Road, Bengaluru",
                is_active=True,
            )
            db.add(office)
            db.flush()
            logger.info("Created office 'CPS-01'")
        else:
            logger.info("Office 'CPS-01' already exists")

        # Get role IDs
        super_admin_role = db.query(Role).filter_by(name=RoleName.SUPER_ADMIN).first()
        supervisor_role = db.query(Role).filter_by(name=RoleName.SUPERVISOR).first()
        officer_role = db.query(Role).filter_by(name=RoleName.OFFICER).first()

        default_password = hash_password("JanSetu@123")

        # 3. Demo Admin User: admin@jansetu.demo
        admin_user = db.query(User).filter_by(email="admin@jansetu.demo").first()
        if not admin_user:
            admin_user = User(
                full_name="Shri Rajeev Gowda, IAS",
                email="admin@jansetu.demo",
                password_hash=default_password,
                phone="+91 94481 00001",
                role_id=super_admin_role.id,
                department_id=dept.id,
                office_id=office.id,
                is_active=True,
                is_verified=True,
            )
            db.add(admin_user)
            logger.info("Created demo user 'admin@jansetu.demo'")

        # 4. Demo PA User: pa@jansetu.demo
        pa_user = db.query(User).filter_by(email="pa@jansetu.demo").first()
        if not pa_user:
            pa_user = User(
                full_name="Smt. Lakshmi Devi, KAS",
                email="pa@jansetu.demo",
                password_hash=default_password,
                phone="+91 94481 00002",
                role_id=super_admin_role.id,
                department_id=dept.id,
                office_id=office.id,
                is_active=True,
                is_verified=True,
            )
            db.add(pa_user)
            logger.info("Created demo user 'pa@jansetu.demo'")

        # 5. Demo Officer User: officer@jansetu.demo
        officer_user = db.query(User).filter_by(email="officer@jansetu.demo").first()
        if not officer_user:
            officer_user = User(
                full_name="Inspector Ramesh Patil",
                email="officer@jansetu.demo",
                password_hash=default_password,
                phone="+91 94481 00003",
                role_id=officer_role.id,
                department_id=dept.id,
                office_id=office.id,
                is_active=True,
                is_verified=True,
            )
            db.add(officer_user)
            logger.info("Created demo user 'officer@jansetu.demo'")

        db.commit()
        logger.info("Demo users and seed data successfully provisioned.")
    except Exception as exc:
        logger.error(f"Error provisioning demo users: {exc}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo_users()
