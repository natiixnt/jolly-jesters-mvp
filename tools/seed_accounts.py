"""Seed sample tenants and users for testing admin endpoints."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.db.session import SessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.services.auth_service import hash_password


SAMPLE_ACCOUNTS = [
    {
        "tenant": {
            "name": "Firma Testowa",
            "slug": "firma-testowa",
            "plan": "pro",
            "monthly_ean_quota": 5000,
            "max_concurrent_runs": 5,
            "api_access": True,
        },
        "users": [
            {
                "email": "admin@firma-testowa.pl",
                "password": "TestAdmin123!@#",
                "display_name": "Jan Kowalski",
                "role": "owner",
            },
            {
                "email": "user@firma-testowa.pl",
                "password": "TestUser123!@#",
                "display_name": "Anna Nowak",
                "role": "member",
            },
        ],
    },
    {
        "tenant": {
            "name": "Demo Shop",
            "slug": "demo-shop",
            "plan": "free",
            "monthly_ean_quota": 1000,
            "max_concurrent_runs": 3,
            "api_access": False,
        },
        "users": [
            {
                "email": "admin@demo-shop.pl",
                "password": "DemoAdmin123!@#",
                "display_name": "Piotr Wisniewski",
                "role": "owner",
            },
        ],
    },
    {
        "tenant": {
            "name": "Hurtownia ABC",
            "slug": "hurtownia-abc",
            "plan": "pro",
            "monthly_ean_quota": 10000,
            "max_concurrent_runs": 10,
            "api_access": True,
        },
        "users": [
            {
                "email": "admin@hurtownia-abc.pl",
                "password": "HurtAdmin123!@#",
                "display_name": "Maria Wisniewska",
                "role": "owner",
            },
            {
                "email": "analityk@hurtownia-abc.pl",
                "password": "Analityk123!@#",
                "display_name": "Tomasz Zielinski",
                "role": "admin",
            },
            {
                "email": "pracownik@hurtownia-abc.pl",
                "password": "Pracownik123!@#",
                "display_name": "Ewa Kaminska",
                "role": "member",
            },
        ],
    },
]


def seed():
    db = SessionLocal()
    created_tenants = 0
    created_users = 0
    skipped = 0

    try:
        for account in SAMPLE_ACCOUNTS:
            tenant_data = account["tenant"]
            existing = db.query(Tenant).filter(Tenant.slug == tenant_data["slug"]).first()
            if existing:
                print(f"  [skip] Tenant '{tenant_data['slug']}' already exists")
                skipped += 1
                continue

            tenant = Tenant(
                name=tenant_data["name"],
                slug=tenant_data["slug"],
                plan=tenant_data["plan"],
                monthly_ean_quota=tenant_data["monthly_ean_quota"],
                max_concurrent_runs=tenant_data["max_concurrent_runs"],
                api_access=tenant_data["api_access"],
            )
            db.add(tenant)
            db.flush()
            created_tenants += 1
            print(f"  [+] Tenant '{tenant.name}' (plan={tenant.plan}, quota={tenant.monthly_ean_quota})")

            for user_data in account["users"]:
                existing_user = db.query(User).filter(User.email == user_data["email"]).first()
                if existing_user:
                    print(f"    [skip] User '{user_data['email']}' already exists")
                    skipped += 1
                    continue

                user = User(
                    tenant_id=tenant.id,
                    email=user_data["email"],
                    password_hash=hash_password(user_data["password"]),
                    display_name=user_data["display_name"],
                    role=user_data["role"],
                )
                db.add(user)
                created_users += 1
                print(f"    [+] User '{user.email}' (role={user.role})")

        db.commit()
        print(f"\nDone: {created_tenants} tenants, {created_users} users created, {skipped} skipped")

        if created_users > 0:
            print("\nSample credentials:")
            for account in SAMPLE_ACCOUNTS:
                for user_data in account["users"]:
                    print(f"  {user_data['email']} / {user_data['password']}  (role={user_data['role']}, tenant={account['tenant']['slug']})")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("Seeding sample accounts...")
    seed()
