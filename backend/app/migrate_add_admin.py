"""
Migration script to add is_admin column to users table.
Run this if you have an existing database.
"""
import psycopg
from app.config import get_settings

def migrate():
    settings = get_settings()
    print(f"Connecting to database: {settings.database_url}")
    
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as c:
            # Check if column exists
            c.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='users' AND column_name='is_admin'
            """)
            
            if c.fetchone():
                print("✓ Column 'is_admin' already exists")
            else:
                print("Adding 'is_admin' column to users table...")
                c.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE")
                conn.commit()
                print("✓ Column 'is_admin' added successfully")
            
            # Update admin user if exists
            c.execute("SELECT id, username FROM users WHERE username = 'admin'")
            admin_user = c.fetchone()
            
            if admin_user:
                c.execute("UPDATE users SET is_admin = TRUE WHERE username = 'admin'")
                conn.commit()
                print(f"✓ Updated user 'admin' (id={admin_user[0]}) to admin role")
            else:
                print("ℹ No user with username 'admin' found. Register with username 'admin' to create an admin user.")
    
    print("\n✓ Migration completed successfully!")

if __name__ == '__main__':
    migrate()
