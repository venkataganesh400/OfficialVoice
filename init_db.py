# init_db.py

import os
import sys
from app import app, db, User # Import from your app

print("--- Starting database initialization script ---")

try:
    # The database URI is configured in app.py using environment variables
    with app.app_context():
        print("DATABASE_URI in use:", app.config['SQLALCHEMY_DATABASE_URI'])

        print("Creating all database tables...")
        db.create_all()
        print("Tables created successfully.")

        # Check if admin user exists, if not, create one
        if not User.query.filter_by(is_admin=True).first():
            print("Admin user not found. Creating default admin user...")
            
            admin_user = User(
                first_name="Admin", last_name="User",
                email="admin@officialvoice.com", voter_id="ADMIN001",
                is_admin=True
            )
            
            # Use environment variable for password if available, otherwise use a default
            admin_password = os.environ.get('ADMIN_PASSWORD', 'AdminPass123')
            admin_user.set_password(admin_password)
            
            db.session.add(admin_user)
            db.session.commit()
            print("Admin user created successfully.")
        else:
            print("Admin user already exists. Skipping creation.")

    print("--- Database initialization finished successfully! ---")

except Exception as e:
    print(f"!!! AN ERROR OCCURRED DURING DB INITIALIZATION: {e}", file=sys.stderr)
    # The 'file=sys.stderr' part helps ensure the error message is highlighted in the logs.
    sys.exit(1) # Exit with a non-zero status code to make sure the build fails clearly.