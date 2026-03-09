"""
Plantain AI — Database Setup & Migration
Run this ONCE: python setup_db.py
Creates missing tables and adds missing columns without deleting your data.
"""
import mysql.connector, os, urllib.parse
from dotenv import load_dotenv
load_dotenv()

def get_db():
    url = os.getenv('MYSQL_URL') or os.getenv('DATABASE_URL') or ''
    if url.startswith('mysql'):
        p = urllib.parse.urlparse(url)
        return mysql.connector.connect(host=p.hostname,port=p.port or 3306,
            user=p.username,password=p.password,database=p.path.lstrip('/'))
    return mysql.connector.connect(
        host=os.getenv('DB_HOST','localhost'),port=int(os.getenv('DB_PORT',3306)),
        user=os.getenv('DB_USER','root'),password=os.getenv('DB_PASSWORD',''),
        database=os.getenv('DB_NAME','plantain_db'))

def run(cursor, sql, label=""):
    try:
        cursor.execute(sql)
        if label: print(f"  OK  {label}")
    except Exception as e:
        msg = str(e)
        if any(x in msg for x in ["Duplicate","already exists","1060","1050","1061"]):
            if label: print(f"  --  {label} (already exists)")
        else:
            print(f"  ERR {label}: {msg}")

print("\nPlantain AI - Database Setup")
print("=" * 40)
db = get_db()
cursor = db.cursor()

print("\nTable: users")
run(cursor,"""CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    phone VARCHAR(20), location VARCHAR(100),
    farmer_type VARCHAR(50) DEFAULT 'crop',
    subscription_plan ENUM('free','pro','enterprise') DEFAULT 'free',
    plan_activated_at TIMESTAMP NULL,
    farm_scale ENUM('small','mid','large') DEFAULT 'small',
    farm_size_acres FLOAT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","users table")
for col,dfn in [("phone","VARCHAR(20)"),("location","VARCHAR(100)"),
    ("farmer_type","VARCHAR(50) DEFAULT 'crop'"),
    ("subscription_plan","ENUM('free','pro','enterprise') DEFAULT 'free'"),
    ("plan_activated_at","TIMESTAMP NULL"),
    ("farm_scale","ENUM('small','mid','large') DEFAULT 'small'"),
    ("farm_size_acres","FLOAT DEFAULT 0")]:
    run(cursor,f"ALTER TABLE users ADD COLUMN {col} {dfn}",f"users.{col}")

print("\nTable: crops")
run(cursor,"""CREATE TABLE IF NOT EXISTS crops (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL, crop_type VARCHAR(80) NOT NULL,
    planting_date DATE, status ENUM('healthy','at_risk','diseased') DEFAULT 'healthy',
    field_name VARCHAR(100), area_acres FLOAT DEFAULT 0,
    expected_yield_kg FLOAT DEFAULT 0, notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""","crops table")
for col,dfn in [("planting_date","DATE"),
    ("status","ENUM('healthy','at_risk','diseased') DEFAULT 'healthy'"),
    ("field_name","VARCHAR(100)"),("area_acres","FLOAT DEFAULT 0"),
    ("expected_yield_kg","FLOAT DEFAULT 0"),("notes","TEXT")]:
    run(cursor,f"ALTER TABLE crops ADD COLUMN {col} {dfn}",f"crops.{col}")

print("\nTable: diagnoses")
run(cursor,"""CREATE TABLE IF NOT EXISTS diagnoses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    crop_id INT, user_id INT NOT NULL,
    image_path VARCHAR(255), disease_name VARCHAR(100),
    severity ENUM('low','medium','high') DEFAULT 'low',
    confidence DECIMAL(5,2) DEFAULT 0,
    treatment TEXT, ai_summary TEXT, bulk_batch VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""","diagnoses table")
for col,dfn in [("crop_id","INT"),("image_path","VARCHAR(255)"),
    ("disease_name","VARCHAR(100)"),
    ("severity","ENUM('low','medium','high') DEFAULT 'low'"),
    ("confidence","DECIMAL(5,2) DEFAULT 0"),
    ("treatment","TEXT"),("ai_summary","TEXT"),("bulk_batch","VARCHAR(50)")]:
    run(cursor,f"ALTER TABLE diagnoses ADD COLUMN {col} {dfn}",f"diagnoses.{col}")

print("\nTable: alerts")
run(cursor,"""CREATE TABLE IF NOT EXISTS alerts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL, alert_type VARCHAR(50), message TEXT,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""","alerts table")
for col,dfn in [("alert_type","VARCHAR(50)"),("is_read","BOOLEAN DEFAULT FALSE")]:
    run(cursor,f"ALTER TABLE alerts ADD COLUMN {col} {dfn}",f"alerts.{col}")

print("\nTable: marketplace_listings")
run(cursor,"""CREATE TABLE IF NOT EXISTS marketplace_listings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL, crop_type VARCHAR(80),
    quantity_kg FLOAT, price_per_kg FLOAT, location VARCHAR(100),
    description TEXT, image_path VARCHAR(255),
    grade VARCHAR(20) DEFAULT 'A',
    status ENUM('active','sold','expired') DEFAULT 'active',
    is_auto BOOLEAN DEFAULT FALSE, views INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""","marketplace_listings table")
for col,dfn in [("image_path","VARCHAR(255)"),("grade","VARCHAR(20) DEFAULT 'A'"),
    ("is_auto","BOOLEAN DEFAULT FALSE"),("views","INT DEFAULT 0"),
    ("status","ENUM('active','sold','expired') DEFAULT 'active'")]:
    run(cursor,f"ALTER TABLE marketplace_listings ADD COLUMN {col} {dfn}",
        f"marketplace_listings.{col}")

print("\nTable: chat_sessions")
run(cursor,"""CREATE TABLE IF NOT EXISTS chat_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL, role VARCHAR(20), message TEXT,
    chat_session_id BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""","chat_sessions table")
for col,dfn in [("role","VARCHAR(20)"),("chat_session_id","BIGINT")]:
    run(cursor,f"ALTER TABLE chat_sessions ADD COLUMN {col} {dfn}",
        f"chat_sessions.{col}")


print("\nTable: agrovets")
run(cursor,"""CREATE TABLE IF NOT EXISTS agrovets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL, owner_name VARCHAR(100),
    email VARCHAR(100), phone VARCHAR(20),
    county VARCHAR(50), town VARCHAR(100), address VARCHAR(200),
    products TEXT, description TEXT,
    payment_method VARCHAR(50), mpesa_ref VARCHAR(50),
    status ENUM('pending','approved','rejected') DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","agrovets table")

print("\nTable: specialists")
run(cursor,"""CREATE TABLE IF NOT EXISTS specialists (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL, email VARCHAR(100), phone VARCHAR(20),
    county VARCHAR(50), specialization VARCHAR(100),
    experience_yrs INT DEFAULT 0, bio TEXT,
    consult_fee FLOAT DEFAULT 0,
    available_online TINYINT DEFAULT 0,
    available_visit TINYINT DEFAULT 0,
    payment_method VARCHAR(50), mpesa_ref VARCHAR(50),
    status ENUM('pending','approved','rejected') DEFAULT 'pending',
    rating FLOAT DEFAULT 0, consultations INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","specialists table")

print("\nTable: specialist_bookings")
run(cursor,"""CREATE TABLE IF NOT EXISTS specialist_bookings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT, specialist_id INT, message TEXT,
    status ENUM('pending','confirmed','completed','cancelled') DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","specialist_bookings table")


print("\nTable: users (new columns)")
for col,dfn in [
    ("farm_type", "ENUM('crops','livestock','mixed','seller') DEFAULT 'crops'"),
    ("livestock_types", "VARCHAR(200) DEFAULT NULL"),
    ("onboarded", "TINYINT DEFAULT 0"),
    ("business_name", "VARCHAR(100) DEFAULT NULL"),
    ("api_key", "VARCHAR(100) DEFAULT NULL"),
]:
    run(cursor, f"ALTER TABLE users ADD COLUMN {col} {dfn}", f"users.{col}")

print("\nTable: subscription_plan (smart tier)")
run(cursor, """ALTER TABLE users MODIFY COLUMN subscription_plan
    ENUM('free','pro','enterprise','smart') DEFAULT 'free'""", "users.subscription_plan enum+smart")

print("\nTable: livestock")
run(cursor,"""CREATE TABLE IF NOT EXISTS livestock (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT, animal_type VARCHAR(50), breed VARCHAR(100),
    name VARCHAR(100), tag_number VARCHAR(50),
    count INT DEFAULT 1, dob DATE NULL,
    weight_kg FLOAT DEFAULT 0, zone VARCHAR(50),
    health_status ENUM('healthy','sick','recovering','deceased') DEFAULT 'healthy',
    notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","livestock table")

print("\nTable: livestock_records")
run(cursor,"""CREATE TABLE IF NOT EXISTS livestock_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    livestock_id INT, user_id INT,
    record_type ENUM('health','weight','production','breeding','vaccination','deworming','harvest','milk_production','egg_production','honey_harvest','wool_harvest','feed_intake','water_quality','litter_size','colony_strength','mortality'),
    value FLOAT DEFAULT 0, unit VARCHAR(30), notes TEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","livestock_records table")

print("\nTable: seller_profiles")
run(cursor,"""CREATE TABLE IF NOT EXISTS seller_profiles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT UNIQUE, business_name VARCHAR(100),
    phone VARCHAR(20), county VARCHAR(50), town VARCHAR(100),
    description TEXT, categories TEXT,
    delivery_zones TEXT, min_order_ksh FLOAT DEFAULT 0,
    status ENUM('pending','approved','suspended') DEFAULT 'pending',
    rating FLOAT DEFAULT 0, total_orders INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","seller_profiles table")

print("\nTable: seller_products")
run(cursor,"""CREATE TABLE IF NOT EXISTS seller_products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    seller_id INT, user_id INT,
    name VARCHAR(100), category VARCHAR(50),
    description TEXT, price_ksh FLOAT,
    unit VARCHAR(30), stock_qty INT DEFAULT 0,
    image_path VARCHAR(200), is_available TINYINT DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","seller_products table")

print("\nTable: seller_orders")
run(cursor,"""CREATE TABLE IF NOT EXISTS seller_orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    buyer_id INT, seller_id INT, product_id INT,
    qty FLOAT, total_ksh FLOAT, delivery_address TEXT,
    status ENUM('pending','confirmed','dispatched','delivered','cancelled') DEFAULT 'pending',
    payment_method VARCHAR(50), mpesa_ref VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","seller_orders table")

print("\nTable: butchery_connections")
run(cursor,"""CREATE TABLE IF NOT EXISTS butchery_connections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    farmer_id INT, business_name VARCHAR(100),
    phone VARCHAR(20), county VARCHAR(50),
    animal_types VARCHAR(200), price_per_kg FLOAT DEFAULT 0,
    status ENUM('active','inactive') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","butchery_connections table")

print("\nTable: smart_devices")
run(cursor,"""CREATE TABLE IF NOT EXISTS smart_devices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT, device_type VARCHAR(50), device_name VARCHAR(100),
    zone VARCHAR(10) DEFAULT '1', api_key VARCHAR(100),
    status ENUM('on','off','auto') DEFAULT 'off',
    last_triggered TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","smart_devices table")

print("\nTable: smart_schedules")
run(cursor,"""CREATE TABLE IF NOT EXISTS smart_schedules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT, device_type VARCHAR(50), zone VARCHAR(10),
    trigger_type VARCHAR(50), trigger_value VARCHAR(100),
    duration_mins INT DEFAULT 30, active TINYINT DEFAULT 1,
    next_run TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","smart_schedules table")

print("\nTable: sensor_readings")
run(cursor,"""CREATE TABLE IF NOT EXISTS sensor_readings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT, sensor_type VARCHAR(50), value FLOAT,
    zone VARCHAR(10) DEFAULT '1', unit VARCHAR(20),
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""","sensor_readings table")

print("\nTable: admin_settings")
run(cursor,"""CREATE TABLE IF NOT EXISTS admin_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    key_name VARCHAR(100) UNIQUE NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
)""","admin_settings table")

print("\nTable: admin_logs")
run(cursor,"""CREATE TABLE IF NOT EXISTS admin_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    admin_id INT,
    user_id INT,
    action VARCHAR(200),
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""","admin_logs table")

print("\nTable: mpesa_transactions")
run(cursor,"""CREATE TABLE IF NOT EXISTS mpesa_transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    phone VARCHAR(20),
    amount DECIMAL(12,2),
    account_ref VARCHAR(100),
    checkout_request_id VARCHAR(200),
    merchant_request_id VARCHAR(200),
    mpesa_receipt VARCHAR(50),
    txn_type VARCHAR(50) DEFAULT 'subscription',
    status ENUM('pending','completed','failed','cancelled') DEFAULT 'pending',
    result_desc TEXT,
    raw_response TEXT,
    completed_at DATETIME,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
)""","mpesa_transactions table")

try:
    cursor.execute("ALTER TABLE users ADD COLUMN is_admin TINYINT(1) DEFAULT 0")
    db.commit()
    print("  + users.is_admin column added")
except Exception as e:
    if "Duplicate column" in str(e): print("  ~ users.is_admin already exists")
    else: print(f"  ! {e}")


print("\n── v8 New Feature Tables ──")

new_tables_sql = [
    ("credit_scores", """CREATE TABLE IF NOT EXISTS credit_scores (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT UNIQUE,
        score INT DEFAULT 300,
        grade VARCHAR(20),
        factors_json TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"""),
    ("loan_applications", """CREATE TABLE IF NOT EXISTS loan_applications (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, amount_requested DECIMAL(12,2), purpose VARCHAR(100),
        duration_months INT DEFAULT 3, credit_score_at_apply INT,
        status ENUM('pending','approved','rejected','disbursed') DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"""),
    ("cooperatives", """CREATE TABLE IF NOT EXISTS cooperatives (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(200) NOT NULL, county VARCHAR(100), focus VARCHAR(100),
        description TEXT, created_by INT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
    ("cooperative_members", """CREATE TABLE IF NOT EXISTS cooperative_members (
        id INT AUTO_INCREMENT PRIMARY KEY,
        cooperative_id INT, user_id INT,
        role ENUM('admin','member') DEFAULT 'member',
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_member (cooperative_id, user_id))"""),
    ("coop_messages", """CREATE TABLE IF NOT EXISTS coop_messages (
        id INT AUTO_INCREMENT PRIMARY KEY,
        cooperative_id INT, user_id INT, message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
    ("coop_treasury", """CREATE TABLE IF NOT EXISTS coop_treasury (
        id INT AUTO_INCREMENT PRIMARY KEY,
        cooperative_id INT, user_id INT,
        txn_type ENUM('credit','debit') DEFAULT 'credit',
        amount DECIMAL(12,2), description VARCHAR(300),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
    ("farm_boundaries", """CREATE TABLE IF NOT EXISTS farm_boundaries (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, name VARCHAR(200), geojson LONGTEXT,
        center_lat FLOAT, center_lng FLOAT, area_ha FLOAT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"""),
    ("ndvi_readings", """CREATE TABLE IF NOT EXISTS ndvi_readings (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, boundary_id INT, ndvi_value FLOAT,
        health_status VARCHAR(30), source VARCHAR(50),
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
    ("buyer_demands", """CREATE TABLE IF NOT EXISTS buyer_demands (
        id INT AUTO_INCREMENT PRIMARY KEY,
        buyer_id INT, crop_type VARCHAR(100), quantity_kg FLOAT,
        price_offered DECIMAL(10,2), county VARCHAR(100),
        deadline DATE, notes TEXT,
        status ENUM('open','fulfilled','cancelled') DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (buyer_id) REFERENCES users(id) ON DELETE CASCADE)"""),
    ("input_reports", """CREATE TABLE IF NOT EXISTS input_reports (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, product_name VARCHAR(200), batch_no VARCHAR(100),
        seller VARCHAR(200), county VARCHAR(100),
        result_json TEXT, is_community_report TINYINT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
    ("breeding_records", """CREATE TABLE IF NOT EXISTS breeding_records (
        id INT AUTO_INCREMENT PRIMARY KEY,
        livestock_id INT, user_id INT,
        event_type VARCHAR(50), heat_date DATE,
        notes TEXT, recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
    ("scheduled_sms", """CREATE TABLE IF NOT EXISTS scheduled_sms (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, phone VARCHAR(20), message TEXT,
        send_at DATETIME, sent TINYINT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
    ("insurance_policies", """CREATE TABLE IF NOT EXISTS insurance_policies (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, plan_key VARCHAR(50), plan_name VARCHAR(200),
        premium DECIMAL(10,2), cover_amount DECIMAL(12,2),
        acres FLOAT DEFAULT 0, heads INT DEFAULT 0,
        county VARCHAR(100), provider VARCHAR(200),
        status ENUM('pending_payment','active','expired','cancelled') DEFAULT 'pending_payment',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"""),
    ("insurance_claims", """CREATE TABLE IF NOT EXISTS insurance_claims (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, policy_id INT, event_description TEXT,
        status ENUM('submitted','reviewing','approved','rejected','paid') DEFAULT 'submitted',
        payout_amount DECIMAL(12,2),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
    ("carbon_practices", """CREATE TABLE IF NOT EXISTS carbon_practices (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, practice_key VARCHAR(100),
        acres FLOAT DEFAULT 1, notes TEXT,
        logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"""),
    ("carbon_listings", """CREATE TABLE IF NOT EXISTS carbon_listings (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT, tonnes FLOAT, price_per_tonne FLOAT,
        total_value_usd FLOAT, notes TEXT,
        status ENUM('available','sold','cancelled') DEFAULT 'available',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
]

for tname, sql in new_tables_sql:
    run(cursor, sql, f"{tname} table")

db.commit()
print("\n✅ All v8 tables created!")

db.commit(); cursor.close(); db.close()
print("\n" + "="*40)
print("Database ready! All tables OK.")
print("\nNEW tables: admin_settings, admin_logs, mpesa_transactions")
print("NEW column:  users.is_admin")
print("\nMAKE YOURSELF ADMIN:")
print("  UPDATE users SET is_admin=1 WHERE email=\'your@email.com\';")
print("\nNow run: python app.py")
print("="*40+"\n")
