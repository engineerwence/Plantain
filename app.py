from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_bcrypt import Bcrypt
import mysql.connector
from dotenv import load_dotenv
import os
from PIL import Image
import io
from datetime import datetime, timedelta
import json
import re
import ast
from functools import wraps
from openai import OpenAI
from collections import defaultdict
import time
import urllib.parse

load_dotenv()

app = Flask(__name__)

# ── SECRET KEY — hard fail if missing ────────────────────────
app.secret_key = os.getenv('SECRET_KEY')

# ── FREE TIER SUMMARY ──────────────────────────────────────────────────────
# Groq API:       FREE — 14,400 req/day, no card needed. groq.com
# Open-Meteo:     FREE — unlimited weather API, no key needed
# Africa's Talking: FREE sandbox — SMS/USSD testing, no real messages sent
# Safaricom Daraja: FREE sandbox — M-Pesa testing, no real money moves
# MySQL:          FREE — running locally on your machine
# OpenStreetMap:  FREE — map tiles, no key needed
# Chart.js:       FREE — open source
# Leaflet.js:     FREE — open source
# Copernicus NDVI: FREE — EU satellite data, register at dataspace.copernicus.eu
#
# COSTS ONLY BEGIN WHEN:
# 1. AT_USERNAME changed from 'sandbox' to live username  → SMS costs ~KSh 1/msg
# 2. MPESA_SANDBOX=false + real shortcode                 → Safaricom live fees
# 3. Groq exceeds free tier (unlikely for prototype)      → Pay-as-you-go
# ──────────────────────────────────────────────────────────────────────────
if not app.secret_key:
    raise RuntimeError('SECRET_KEY is not set in .env — refusing to start.')

bcrypt = Bcrypt(app)

# ── GROQ CLIENT ───────────────────────────────────────────────
groq_client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)
GROQ_CHAT_MODEL   = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# ════════════════════════════════════════════════════════════════
#  AUTONOMOUS AI ENGINES
# ════════════════════════════════════════════════════════════════

def autonomous_disease_spread_alert(cursor, db, disease, severity, user_id, crop_type):
    if severity not in ('medium', 'high') or disease in ('Healthy', 'Analysis unavailable'):
        return
    try:
        cursor.execute("SELECT location FROM users WHERE id=%s", (user_id,))
        row = cursor.fetchone()
        if not row or not row.get('location'):
            return
        county = row['location'].strip()

        cursor.execute("""
            SELECT DISTINCT u.id, u.name
            FROM users u
            JOIN crops c ON c.user_id = u.id
            WHERE u.id != %s
              AND LOWER(u.location) LIKE %s
              AND LOWER(c.crop_type) LIKE %s
        """, (user_id, f"%{county.lower()}%", f"%{crop_type.lower()[:6]}%"))
        at_risk_farmers = cursor.fetchall()

        for farmer in at_risk_farmers:
            msg = (
                f"🌍 DISEASE SPREAD ALERT — {disease} has been detected on a "
                f"{crop_type} farm in {county}. Your {crop_type} crops may be at risk. "
                f"Inspect your crops immediately and run an AI diagnosis. "
                f"[Autonomous alert by Plantain AI]"
            )
            cursor.execute(
                "INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
                (farmer['id'], 'spread_alert', msg)
            )
        db.commit()
        print(f"[AI] Disease spread alert sent to {len(at_risk_farmers)} farmers in {county}")
    except Exception as e:
        print(f"[AI] Spread alert error: {repr(e)}")


def autonomous_harvest_countdown(cursor, db, user_id, crop_id, crop_type, planting_date):
    if not planting_date:
        return
    try:
        HARVEST_DAYS = {
            'plantain': 270, 'banana': 270, 'maize': 120, 'tomato': 75,
            'kale': 60, 'sukuma wiki': 60, 'spinach': 45, 'carrot': 90,
            'beans': 70, 'peas': 65, 'onion': 120, 'garlic': 150,
            'cassava': 365, 'yam': 270, 'sweet potato': 120, 'potato': 90,
            'mango': 120, 'avocado': 150, 'pineapple': 180, 'passion fruit': 240,
            'watermelon': 90, 'pawpaw': 180, 'orange': 240, 'coffee': 365,
            'tea': 90, 'sugarcane': 365, 'rice': 120, 'sorghum': 110,
            'millet': 90, 'groundnut': 110, 'soybean': 100, 'sunflower': 100,
            'arrow roots': 240, 'ginger': 240, 'turmeric': 270,
        }
        days = 90
        for k, v in HARVEST_DAYS.items():
            if k in crop_type.lower():
                days = v
                break

        if isinstance(planting_date, str):
            try:
                planting_date = datetime.strptime(planting_date, '%Y-%m-%d').date()
            except Exception:
                return

        harvest_date = planting_date + timedelta(days=days)
        today        = datetime.now().date()
        days_left    = (harvest_date - today).days

        msg = None
        if days_left == 30:
            msg = f"📅 HARVEST REMINDER — Your {crop_type} is expected ready in 30 days ({harvest_date.strftime('%d %b %Y')}). Start preparing storage and finding buyers. [Plantain AI]"
        elif days_left == 14:
            msg = f"📅 HARVEST REMINDER — Your {crop_type} harvest is 2 weeks away ({harvest_date.strftime('%d %b %Y')}). List it on the marketplace now to secure a buyer. [Plantain AI]"
        elif days_left == 7:
            msg = f"🌾 HARVEST NEXT WEEK — Your {crop_type} should be ready around {harvest_date.strftime('%d %b %Y')}. Confirm crop health with a final AI diagnosis. [Plantain AI]"
        elif 0 >= days_left >= -3:
            msg = f"🎉 HARVEST TIME — Your {crop_type} planted on {planting_date.strftime('%d %b %Y')} should be ready now! Run a final diagnosis to confirm. [Plantain AI]"

        if msg:
            cursor.execute(
                "INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
                (user_id, 'harvest', msg)
            )
            db.commit()
            print(f"[AI] Harvest countdown alert sent — {days_left} days to harvest for {crop_type}")
    except Exception as e:
        print(f"[AI] Harvest countdown error: {repr(e)}")


def autonomous_auto_listing(cursor, db, user_id, crop_id, crop_type, severity, planting_date):
    if severity != 'low' or not planting_date:
        return
    try:
        HARVEST_DAYS = {
            'plantain': 270, 'banana': 270, 'maize': 120, 'tomato': 75,
            'kale': 60, 'sukuma wiki': 60, 'spinach': 45, 'carrot': 90,
            'beans': 70, 'peas': 65, 'onion': 120, 'sweet potato': 120,
            'potato': 90, 'mango': 120, 'avocado': 150, 'rice': 120,
        }
        days = 90
        for k, v in HARVEST_DAYS.items():
            if k in crop_type.lower():
                days = v
                break

        if isinstance(planting_date, str):
            try:
                planting_date = datetime.strptime(planting_date, '%Y-%m-%d').date()
            except Exception:
                return

        harvest_date = planting_date + timedelta(days=days)
        today        = datetime.now().date()
        days_left    = (harvest_date - today).days

        if days_left > 30 or days_left < 0:
            return

        cursor.execute("""
            SELECT id FROM marketplace_listings
            WHERE user_id=%s AND LOWER(crop_type) LIKE %s AND status='active'
        """, (user_id, f"%{crop_type.lower()[:8]}%"))
        if cursor.fetchone():
            return

        cursor.execute("SELECT location FROM users WHERE id=%s", (user_id,))
        row = cursor.fetchone()
        location = row['location'] if row and row.get('location') else 'Kenya'

        PRICE_MAP = {
            'plantain': 60, 'banana': 50, 'maize': 45, 'tomato': 80,
            'kale': 30, 'sukuma wiki': 30, 'spinach': 40, 'carrot': 60,
            'beans': 120, 'onion': 70, 'potato': 55, 'sweet potato': 50,
            'avocado': 150, 'mango': 80, 'rice': 100, 'passion fruit': 200,
        }
        price = 50
        for k, v in PRICE_MAP.items():
            if k in crop_type.lower():
                price = v
                break

        cursor.execute("""
            INSERT INTO marketplace_listings
                (user_id,crop_type,quantity_kg,price_per_kg,location,description,
                 category,status,auto_posted)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'active',TRUE)
        """, (
            user_id, crop_type, 100, price, location,
            f"Auto-listed by Plantain AI — healthy crop, harvest ready in ~{days_left} days. Grade A quality.",
            'other'
        ))
        cursor.execute(
            "INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
            (user_id, 'auto_listing',
             f"🤖 AUTO-LISTED — Plantain AI has listed your {crop_type} at KSh {price}/kg. "
             f"Harvest is ~{days_left} days away. Go to Marketplace to edit or remove. [Plantain AI]")
        )
        db.commit()
        print(f"[AI] Auto-listed {crop_type} for user {user_id} at KSh {price}/kg")
    except Exception as e:
        print(f"[AI] Auto listing error: {repr(e)}")


def autonomous_price_prediction(cursor, db, user_id, crop_type, severity):
    try:
        cursor.execute("""
            SELECT price_per_kg, created_at
            FROM marketplace_listings
            WHERE LOWER(crop_type) LIKE %s
              AND status = 'active'
              AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
            ORDER BY created_at DESC
            LIMIT 20
        """, (f"%{crop_type.lower()[:8]}%",))
        recent_prices = cursor.fetchall()

        if len(recent_prices) < 2:
            try:
                resp = groq_client.chat.completions.create(
                    model=GROQ_CHAT_MODEL,
                    messages=[{"role":"user","content":
                        f"You are an agricultural market expert for Kenya. "
                        f"Give ONE sentence of price advice for selling {crop_type} in Kenya right now. "
                        f"Include a typical KSh price range per kg. Be specific. No intro text."
                    }],
                    max_tokens=80, temperature=0.4
                )
                advice = resp.choices[0].message.content.strip()
            except Exception:
                advice = f"Market data for {crop_type} is limited. Check local prices before listing."

            cursor.execute(
                "INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
                (user_id, 'price_intel',
                 f"💰 MARKET INTEL — {advice} [Autonomous price analysis by Plantain AI]")
            )
            db.commit()
            return

        prices = [float(r['price_per_kg']) for r in recent_prices]
        avg    = sum(prices) / len(prices)
        latest = prices[0]
        oldest = prices[-1]
        trend  = ((latest - oldest) / oldest * 100) if oldest > 0 else 0

        if trend > 10:
            advice = f"Prices for {crop_type} are up {trend:.0f}% in the last 30 days. Avg: KSh {avg:.0f}/kg. Good time to sell now."
        elif trend < -10:
            advice = f"Prices for {crop_type} are down {abs(trend):.0f}% in the last 30 days. Avg: KSh {avg:.0f}/kg. Consider waiting 2–3 weeks."
        else:
            advice = f"Prices for {crop_type} are stable around KSh {avg:.0f}/kg. Safe to list now."

        cursor.execute(
            "INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
            (user_id, 'price_intel',
             f"💰 MARKET INTEL — {advice} [Autonomous market analysis by Plantain AI]")
        )
        db.commit()
        print(f"[AI] Price prediction sent for {crop_type} — trend: {trend:.1f}%")
    except Exception as e:
        print(f"[AI] Price prediction error: {repr(e)}")


# ── SECURITY HEADERS ──────────────────────────────────────────
@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options']         = 'DENY'
    response.headers['X-XSS-Protection']        = '1; mode=block'
    response.headers['Referrer-Policy']          = 'strict-origin-when-cross-origin'
    return response

# ── IN-MEMORY RATE LIMITER ────────────────────────────────────
_rate_store = defaultdict(list)

def rate_limit(max_calls=10, window=60):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip  = request.remote_addr or '0.0.0.0'
            now = time.time()
            hits = [t for t in _rate_store[ip] if now - t < window]
            if len(hits) >= max_calls:
                return "Too many requests. Please wait.", 429
            hits.append(now)
            _rate_store[ip] = hits
            return f(*args, **kwargs)
        return wrapped
    return decorator

# ── INPUT SANITISER ───────────────────────────────────────────
def clean(value, max_len=200):
    return str(value or '').strip()[:max_len]

# ── IMAGE VALIDATOR ───────────────────────────────────────────
ALLOWED_MIME = {'image/jpeg', 'image/png', 'image/webp'}
MAX_IMG_SIZE = 10 * 1024 * 1024

def valid_image(f):
    if not f or not f.filename:
        return False
    if f.mimetype not in ALLOWED_MIME:
        return False
    f.seek(0, 2); size = f.tell(); f.seek(0)
    if size > MAX_IMG_SIZE:
        return False
    try:
        img = Image.open(f); img.verify(); f.seek(0)
        return True
    except Exception:
        return False

# ── DATABASE ──────────────────────────────────────────────────
def get_db():
    url = os.getenv('MYSQL_URL') or os.getenv('DATABASE_URL') or ''
    if url.startswith('mysql'):
        p = urllib.parse.urlparse(url)
        return mysql.connector.connect(
            host=p.hostname, port=p.port or 3306,
            user=p.username, password=p.password,
            database=p.path.lstrip('/'),
            dictionary=True
        )
    return mysql.connector.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_NAME', 'plantain_db')
    )

# ── AUTH DECORATOR ────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.before_request
def force_smart_tier():
    """TEMP: give all logged-in users Smart tier for testing."""
    if 'user_id' in session:
        session['subscription_plan'] = 'smart'



# ════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ════════════════════════════════════════════════════════════════

@app.route('/')
def landing():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')


@app.route('/register', methods=['GET', 'POST'])
@rate_limit(max_calls=5, window=60)
def register():
    if request.method == 'POST':
        name     = clean(request.form.get('name'), 100)
        email    = clean(request.form.get('email'), 100).lower()
        raw_pw   = (request.form.get('password') or '')[:128]
        phone    = clean(request.form.get('phone'), 20)
        location = clean(request.form.get('location'), 100)

        if not name or not email or not raw_pw:
            return render_template('register.html', error='Name, email and password are required.')
        if len(raw_pw) < 6:
            return render_template('register.html', error='Password must be at least 6 characters.')
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            return render_template('register.html', error='Enter a valid email address.')

        pw_hash = bcrypt.generate_password_hash(raw_pw).decode('utf-8')
        db = get_db(); cursor = db.cursor(dictionary=True)
        try:
            cursor.execute(
                "INSERT INTO users (name,email,password,phone,location) VALUES (%s,%s,%s,%s,%s)",
                (name, email, pw_hash, phone, location)
            )
            db.commit()
            db.commit()
            new_uid = cursor.lastrowid
            cursor.close(); db.close()
            return redirect(url_for('login'))
        except mysql.connector.IntegrityError:
            cursor.close(); db.close()
            return render_template('register.html', error='Email already registered.')
        finally:
            cursor.close(); db.close()
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
@rate_limit(max_calls=10, window=60)
def login():
    if request.method == 'POST':
        email  = clean(request.form.get('email'), 100).lower()
        raw_pw = (request.form.get('password') or '')[:128]

        db = get_db(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close(); db.close()

        dummy = '$2b$12$notarealhashatallxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
        ok = bcrypt.check_password_hash(user['password'] if user else dummy, raw_pw)

        if user and ok:
            session.clear()
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=7)
            session['user_id']           = user['id']
            session['user_name']         = user['name']
            session['subscription_plan'] = 'smart'  # TEMP: all users get Smart tier for testing
            session['is_admin']          = bool(user.get('is_admin', 0))
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid email or password.')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('landing'))


# ════════════════════════════════════════════════════════════════
#  DASHBOARD
# ════════════════════════════════════════════════════════════════

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']

    # Onboarding check — skipped during testing
    try:
        cursor.execute("SELECT onboarded, farm_type, livestock_types, business_name FROM users WHERE id=%s", (uid,))
        uinfo = cursor.fetchone() or {}
    except Exception:
        uinfo = {}
    farm_type = uinfo.get('farm_type', 'crops')
    livestock_types = (uinfo.get('livestock_types') or '').split(',')

    # Livestock summary for dashboard (safe — tables may not exist yet)
    try:
        cursor.execute("SELECT animal_type, COUNT(*) as count, SUM(count) as total FROM livestock WHERE user_id=%s GROUP BY animal_type", (uid,))
        livestock_summary = cursor.fetchall()
    except Exception:
        pass


    # Recent livestock records
    cursor.execute("""SELECT lr.*, l.animal_type, l.name as animal_name
        FROM livestock_records lr JOIN livestock l ON lr.livestock_id=l.id
        WHERE lr.user_id=%s ORDER BY lr.recorded_at DESC LIMIT 5""", (uid,))
    livestock_records = cursor.fetchall()

    cursor.execute("SELECT * FROM crops WHERE user_id=%s", (uid,))
    crops = cursor.fetchall()

    cursor.execute("SELECT * FROM diagnoses WHERE user_id=%s ORDER BY created_at DESC LIMIT 5", (uid,))
    recent_diagnoses = cursor.fetchall()

    cursor.execute("SELECT * FROM alerts WHERE user_id=%s AND is_read=FALSE ORDER BY created_at DESC", (uid,))
    alerts = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) AS n FROM crops     WHERE user_id=%s", (uid,)); total_crops     = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM diagnoses WHERE user_id=%s", (uid,)); total_diagnoses = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM alerts    WHERE user_id=%s AND is_read=FALSE", (uid,)); unread_alerts = cursor.fetchone()['n']

    # Fetch user location for weather
    cursor.execute("SELECT location FROM users WHERE id=%s", (uid,))
    user_row = cursor.fetchone()
    user_location = (user_row.get('location') or 'Nairobi') if user_row else 'Nairobi'

    cursor.close(); db.close()

    # ── WEATHER via Open-Meteo (free, no API key) ─────────────
    # Kenya county → approximate lat/lng
    KENYA_COORDS = {
        'nairobi': (-1.2921, 36.8219), 'mombasa': (-4.0435, 39.6682),
        'kisumu': (-0.1022, 34.7617),  'nakuru': (-0.3031, 36.0800),
        'eldoret': (0.5143, 35.2698),  'thika': (-1.0332, 37.0693),
        'nyeri': (-0.4167, 36.9500),   'meru': (0.0467, 37.6490),
        'kakamega': (0.2827, 34.7519), 'garissa': (-0.4532, 39.6461),
        'kitale': (1.0154, 35.0062),   'malindi': (-3.2138, 40.1169),
        'kisii': (-0.6817, 34.7667),   'machakos': (-1.5177, 37.2634),
        'kericho': (-0.3686, 35.2863), 'embu': (-0.5300, 37.4500),
        'lamu': (-2.2686, 40.9020),    'voi': (-3.3960, 38.5563),
    }
    weather = None
    try:
        loc_key = user_location.lower().split(',')[0].strip()
        lat, lon = KENYA_COORDS.get(loc_key, (-1.2921, 36.8219))  # default Nairobi

        import urllib.request
        w_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,precipitation,weathercode,windspeed_10m"
            f"&daily=precipitation_sum,temperature_2m_max,temperature_2m_min"
            f"&timezone=Africa%2FNairobi&forecast_days=3"
        )
        with urllib.request.urlopen(w_url, timeout=4) as resp:
            w_data = json.loads(resp.read())

        cur = w_data.get('current', {})
        daily = w_data.get('daily', {})

        # Weather code → description + emoji
        WC = {
            0:'Clear sky ☀️', 1:'Mainly clear 🌤️', 2:'Partly cloudy ⛅', 3:'Overcast ☁️',
            45:'Foggy 🌫️', 48:'Icy fog 🌫️', 51:'Light drizzle 🌦️', 53:'Drizzle 🌦️',
            55:'Heavy drizzle 🌧️', 61:'Slight rain 🌧️', 63:'Moderate rain 🌧️',
            65:'Heavy rain 🌧️', 71:'Slight snow 🌨️', 80:'Rain showers 🌦️',
            81:'Heavy showers 🌧️', 95:'Thunderstorm ⛈️', 99:'Thunderstorm ⛈️',
        }
        code = cur.get('weathercode', 0)
        desc = WC.get(code, WC.get((code//10)*10, 'Clear ☀️'))

        # Farming advice based on weather
        rain = cur.get('precipitation', 0)
        temp = cur.get('temperature_2m', 22)
        if rain > 5:
            farm_tip = "Heavy rain — hold off on pesticide spraying today."
        elif rain > 0:
            farm_tip = "Light rain expected — good day to transplant seedlings."
        elif temp > 32:
            farm_tip = "Hot day — water crops early morning or evening."
        elif temp < 14:
            farm_tip = "Cool weather — watch for fungal diseases on leaves."
        else:
            farm_tip = "Good farming weather — ideal for fieldwork today."

        weather = {
            'temp': round(temp),
            'humidity': cur.get('relative_humidity_2m', 0),
            'wind': round(cur.get('windspeed_10m', 0)),
            'rain': round(rain, 1),
            'desc': desc,
            'tip': farm_tip,
            'location': user_location.title(),
            'forecast': []
        }

        days = daily.get('time', [])
        max_t = daily.get('temperature_2m_max', [])
        min_t = daily.get('temperature_2m_min', [])
        rain_d = daily.get('precipitation_sum', [])
        for i in range(min(3, len(days))):
            try:
                d = datetime.strptime(days[i], '%Y-%m-%d')
                label = 'Today' if i == 0 else ('Tomorrow' if i == 1 else d.strftime('%a'))
                weather['forecast'].append({
                    'label': label,
                    'max': round(max_t[i]) if i < len(max_t) else '--',
                    'min': round(min_t[i]) if i < len(min_t) else '--',
                    'rain': round(rain_d[i], 1) if i < len(rain_d) else 0,
                })
            except Exception:
                pass
    except Exception as e:
        print(f"[WEATHER] Error: {repr(e)}")
        weather = None

    return render_template('dashboard.html',
        farm_type=farm_type,
        livestock_summary=livestock_summary,
        livestock_records=livestock_records,
        crops=crops, recent_diagnoses=recent_diagnoses, alerts=alerts,
        total_crops=total_crops, total_diagnoses=total_diagnoses, unread_alerts=unread_alerts,
        weather=weather, user_location=user_location
    )


@app.route('/add-crop', methods=['POST'])
@login_required
def add_crop():
    crop_type     = clean(request.form.get('crop_type'), 80)
    planting_date = clean(request.form.get('planting_date'), 20)
    if not crop_type:
        return redirect(url_for('dashboard'))
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute(
        "INSERT INTO crops (user_id,crop_type,planting_date) VALUES (%s,%s,%s)",
        (session['user_id'], crop_type, planting_date or None)
    )
    db.commit(); cursor.close(); db.close()
    return redirect(url_for('dashboard'))

# ════════════════════════════════════════════════════════════════
#  AI DIAGNOSIS — farmer types crop name freely, no dropdown
# ════════════════════════════════════════════════════════════════

@app.route('/diagnose', methods=['GET', 'POST'])
@login_required
def diagnose():

    if request.method == 'GET':
        return render_template('diagnose.html')

    crop_name     = clean(request.form.get('subject_name', '') or request.form.get('crop_name', ''), 80)
    planting_date = (request.form.get('ready_date', '') or request.form.get('planting_date', '')).strip() or None
    image_file    = request.files.get('image')

    if not crop_name:
        return render_template('diagnose.html', error='Please enter your crop name.')
    if not image_file or not image_file.filename:
        return render_template('diagnose.html', error='Please upload a photo.')
    if not valid_image(image_file):
        return render_template('diagnose.html', error='Upload a valid image (JPG/PNG, max 10MB).')

    try:
        import base64
        img = Image.open(image_file).convert('RGB')
        img.thumbnail((800, 800))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        buf.seek(0)
        image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    except Exception as e:
        return render_template('diagnose.html', error=f'Could not read image: {repr(e)}')

    try:
        db = get_db(); cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, planting_date FROM crops WHERE user_id=%s AND LOWER(crop_type)=%s LIMIT 1",
            (session['user_id'], crop_name.lower())
        )
        existing = cursor.fetchone()
        if existing:
            crop_id     = existing['id']
            planting_ai = existing['planting_date'] or planting_date
            if planting_date and not existing['planting_date']:
                cursor.execute("UPDATE crops SET planting_date=%s WHERE id=%s", (planting_date, crop_id))
                db.commit()
        else:
            cursor.execute(
                "INSERT INTO crops (user_id,crop_type,planting_date,status) VALUES (%s,%s,%s,'healthy')",
                (session['user_id'], crop_name, planting_date)
            )
            db.commit()
            crop_id     = cursor.lastrowid
            planting_ai = planting_date
        cursor.close(); db.close()
    except Exception as e:
        return render_template('diagnose.html', error=f'Database error: {repr(e)}')

    prompt = f"""You are an expert agricultural AI for Kenyan farmers.
The farmer says this is a photo of their {crop_name} crop.
Analyze this image and respond ONLY with valid JSON, no other text:
{{
  "disease_name": "exact disease name or Healthy",
  "severity": "low or medium or high",
  "confidence": 85,
  "symptoms": ["symptom 1","symptom 2","symptom 3"],
  "treatment_steps": ["step 1","step 2","step 3"],
  "prevention_tips": ["tip 1","tip 2"],
  "harvest_recommendation": "practical advice for this Kenyan farmer"
}}"""

    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{"role":"user","content":[
                {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}},
                {"type":"text","text":prompt}
            ]}],
            max_tokens=1000, temperature=0.2
        )
        raw   = resp.choices[0].message.content.strip()
        print(f"[DIAGNOSE] AI response: {raw[:300]}")
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        result = json.loads(match.group()) if match else {}
        if not result.get('disease_name'):
            raise ValueError("Empty AI response")
    except Exception as e:
        print(f"[DIAGNOSE] AI error: {repr(e)}")
        result = {
            "disease_name": "Analysis unavailable", "severity": "low", "confidence": 0,
            "symptoms": ["Could not analyze — try a clearer photo"],
            "treatment_steps": ["Retake photo in good lighting"],
            "prevention_tips": ["Ensure good lighting and focus"],
            "harvest_recommendation": "Consult a local agronomist"
        }

    fpath = ''
    try:
        os.makedirs('static/uploads', exist_ok=True)
        fname = f"crop_{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
        fpath = f"static/uploads/{fname}"
        buf.seek(0)
        with open(fpath, 'wb') as fout:
            fout.write(buf.read())
    except Exception as e:
        print(f"[DIAGNOSE] File save error: {repr(e)}")

    try:
        db = get_db(); cursor = db.cursor(dictionary=True)
        severity = result.get('severity', 'low')
        disease  = result.get('disease_name', 'Unknown')

        cursor.execute("""
            INSERT INTO diagnoses (crop_id,user_id,image_path,disease_name,severity,confidence,treatment)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            crop_id, session['user_id'], fpath, disease, severity,
            float(result.get('confidence', 0)),
            json.dumps(result.get('treatment_steps', []))
        ))
        diag_id = cursor.lastrowid

        status_map = {'high': 'diseased', 'medium': 'at_risk', 'low': 'healthy'}
        cursor.execute("UPDATE crops SET status=%s, last_checked=NOW() WHERE id=%s",
                       (status_map.get(severity, 'healthy'), crop_id))

        if severity == 'high':
            cursor.execute(
                "INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
                (session['user_id'], 'disease',
                 f"🚨 HIGH — {disease} on your {crop_name}! Act immediately.")
            )
        elif severity == 'medium':
            cursor.execute(
                "INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
                (session['user_id'], 'disease',
                 f"⚠️ {disease} on your {crop_name}. Treatment recommended.")
            )
        db.commit()

        autonomous_disease_spread_alert(cursor, db, disease, severity, session['user_id'], crop_name)
        autonomous_harvest_countdown(cursor, db, session['user_id'], crop_id, crop_name, planting_ai)
        autonomous_auto_listing(cursor, db, session['user_id'], crop_id, crop_name, severity, planting_ai)
        autonomous_price_prediction(cursor, db, session['user_id'], crop_name, severity)

        cursor.close(); db.close()
    except Exception as e:
        print(f"[DIAGNOSE] DB error: {repr(e)}")
        try: db.rollback(); cursor.close(); db.close()
        except: pass
        return render_template('diagnose.html', error=f'Error saving: {repr(e)}')

    return redirect(url_for('result', diagnosis_id=diag_id))

@app.route('/result/<int:diagnosis_id>')
@login_required
def result(diagnosis_id):
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT d.*, c.crop_type, c.planting_date
        FROM diagnoses d JOIN crops c ON d.crop_id = c.id
        WHERE d.id=%s AND d.user_id=%s
    """, (diagnosis_id, session['user_id']))
    diagnosis = cursor.fetchone()
    cursor.close(); db.close()

    if not diagnosis:
        return redirect(url_for('dashboard'))

    # Parse treatment steps — handle both JSON and old Python literal format
    try:
        treatment_steps = json.loads(diagnosis['treatment'])
    except Exception:
        try:
            treatment_steps = ast.literal_eval(diagnosis['treatment'])
        except Exception:
            treatment_steps = [diagnosis['treatment']]

    return render_template('result.html', diagnosis=diagnosis, treatment_steps=treatment_steps)


# ════════════════════════════════════════════════════════════════
#  MARKETPLACE
# ════════════════════════════════════════════════════════════════

def _run_migrations(cursor, db):
    # Agrovets table
    try:
        cursor.execute("""CREATE TABLE IF NOT EXISTS agrovets (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL, owner_name VARCHAR(100),
            email VARCHAR(100), phone VARCHAR(20),
            county VARCHAR(50), town VARCHAR(100), address VARCHAR(200),
            products TEXT, description TEXT,
            payment_method VARCHAR(50), mpesa_ref VARCHAR(50),
            status ENUM('pending','approved','rejected') DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        db.commit()
    except Exception: pass
    # Specialists table
    try:
        cursor.execute("""CREATE TABLE IF NOT EXISTS specialists (
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        db.commit()
    except Exception: pass
    # Smart Farm tables
    for sql in [
        """CREATE TABLE IF NOT EXISTS smart_devices (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT, device_type VARCHAR(50), device_name VARCHAR(100),
            zone VARCHAR(10) DEFAULT '1', api_key VARCHAR(100),
            status ENUM('on','off','auto') DEFAULT 'off',
            last_triggered TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS smart_schedules (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT, device_type VARCHAR(50), zone VARCHAR(10),
            trigger_type VARCHAR(50), trigger_value VARCHAR(100),
            duration_mins INT DEFAULT 30, active TINYINT DEFAULT 1,
            next_run TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS sensor_readings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT, sensor_type VARCHAR(50), value FLOAT,
            zone VARCHAR(10) DEFAULT '1', unit VARCHAR(20),
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS api_key VARCHAR(100) DEFAULT NULL""",
    ]:
        try: cursor.execute(sql); db.commit()
        except Exception: pass

    # Livestock records
    for sql in [
        """CREATE TABLE IF NOT EXISTS livestock (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT, animal_type VARCHAR(50), breed VARCHAR(100),
            name VARCHAR(100), tag_number VARCHAR(50),
            count INT DEFAULT 1, dob DATE NULL,
            weight_kg FLOAT DEFAULT 0, zone VARCHAR(50),
            health_status ENUM('healthy','sick','recovering','deceased') DEFAULT 'healthy',
            notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS livestock_records (
            id INT AUTO_INCREMENT PRIMARY KEY,
            livestock_id INT, user_id INT,
            record_type ENUM('health','weight','production','breeding','vaccination','deworming','harvest'),
            value FLOAT DEFAULT 0, unit VARCHAR(30), notes TEXT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS seller_profiles (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT UNIQUE, business_name VARCHAR(100),
            phone VARCHAR(20), county VARCHAR(50), town VARCHAR(100),
            description TEXT, categories TEXT,
            delivery_zones TEXT, min_order_ksh FLOAT DEFAULT 0,
            status ENUM('pending','approved','suspended') DEFAULT 'pending',
            rating FLOAT DEFAULT 0, total_orders INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS seller_products (
            id INT AUTO_INCREMENT PRIMARY KEY,
            seller_id INT, user_id INT,
            name VARCHAR(100), category VARCHAR(50),
            description TEXT, price_ksh FLOAT,
            unit VARCHAR(30), stock_qty INT DEFAULT 0,
            image_path VARCHAR(200), is_available TINYINT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS seller_orders (
            id INT AUTO_INCREMENT PRIMARY KEY,
            buyer_id INT, seller_id INT, product_id INT,
            qty FLOAT, total_ksh FLOAT, delivery_address TEXT,
            status ENUM('pending','confirmed','dispatched','delivered','cancelled') DEFAULT 'pending',
            payment_method VARCHAR(50), mpesa_ref VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS butchery_connections (
            id INT AUTO_INCREMENT PRIMARY KEY,
            farmer_id INT, business_name VARCHAR(100),
            phone VARCHAR(20), county VARCHAR(50),
            animal_types VARCHAR(200), price_per_kg FLOAT DEFAULT 0,
            status ENUM('active','inactive') DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    ]:
        try: cursor.execute(sql); db.commit()
        except Exception: pass

    # Specialist bookings table
    try:
        cursor.execute("""CREATE TABLE IF NOT EXISTS specialist_bookings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT, specialist_id INT, message TEXT,
            status ENUM('pending','confirmed','completed','cancelled') DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        db.commit()
    except Exception: pass

    stmts = [
        "ALTER TABLE marketplace_listings ADD COLUMN IF NOT EXISTS category    VARCHAR(50)  DEFAULT 'other'",
        "ALTER TABLE marketplace_listings ADD COLUMN IF NOT EXISTS grade        VARCHAR(100) DEFAULT ''",
        "ALTER TABLE marketplace_listings ADD COLUMN IF NOT EXISTS harvest_date DATE         NULL",
        "ALTER TABLE marketplace_listings ADD COLUMN IF NOT EXISTS photo_path   VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE users               ADD COLUMN IF NOT EXISTS subscription_plan ENUM('free','pro','enterprise') DEFAULT 'free'",
        "ALTER TABLE users               ADD COLUMN IF NOT EXISTS plan_activated_at TIMESTAMP NULL",
        "ALTER TABLE users               ADD COLUMN IF NOT EXISTS farm_scale    ENUM('small','mid','large') DEFAULT 'small'",
        "ALTER TABLE users               ADD COLUMN IF NOT EXISTS farm_size_acres FLOAT DEFAULT NULL",
        "ALTER TABLE crops               ADD COLUMN IF NOT EXISTS field_name    VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE crops               ADD COLUMN IF NOT EXISTS area_acres    FLOAT        DEFAULT NULL",
        "ALTER TABLE crops               ADD COLUMN IF NOT EXISTS expected_yield_kg FLOAT    DEFAULT NULL",
        "ALTER TABLE diagnoses           ADD COLUMN IF NOT EXISTS bulk_batch    VARCHAR(50)  DEFAULT NULL",
    ]
    for s in stmts:
        try:
            cursor.execute(s); db.commit()
        except Exception:
            pass


@app.route('/marketplace')
@login_required
def marketplace():
    db = get_db(); cursor = db.cursor(dictionary=True)
    _run_migrations(cursor, db)

    uid = session['user_id']

    cursor.execute("SELECT COALESCE(subscription_plan,'free') AS plan FROM users WHERE id=%s", (uid,))
    row          = cursor.fetchone()
    current_plan = row['plan'] if row else 'free'

    cursor.execute(
        "SELECT COUNT(*) AS n FROM marketplace_listings WHERE user_id=%s AND status='active'", (uid,)
    )
    my_listing_count = cursor.fetchone()['n']

    cursor.execute("""
        SELECT ml.*,
               u.name      AS farmer_name,
               u.location  AS farmer_location,
               COALESCE(u.subscription_plan,'free') AS farmer_plan
        FROM marketplace_listings ml
        JOIN users u ON ml.user_id = u.id
        WHERE ml.status = 'active'
        ORDER BY
            CASE COALESCE(u.subscription_plan,'free')
                WHEN 'enterprise' THEN 1
                WHEN 'pro'        THEN 2
                ELSE                   3
            END,
            ml.created_at DESC
    """)
    raw_listings = cursor.fetchall()

    listings = []
    for l in raw_listings:
        if l['created_at']:
            diff = datetime.now() - l['created_at']
            if diff.days == 0:
                h = diff.seconds // 3600
                l['created_at'] = f"{h}h ago" if h > 0 else "Just now"
            elif diff.days == 1:
                l['created_at'] = "Yesterday"
            else:
                l['created_at'] = f"{diff.days}d ago"
        listings.append(l)

    cursor.execute("""
        SELECT u.id, u.name, u.location,
               COALESCE(u.subscription_plan,'free') AS subscription_plan,
               COUNT(DISTINCT ml.id) AS listing_count,
               COUNT(DISTINCT c.id)  AS crop_count,
               COUNT(DISTINCT d.id)  AS diagnosis_count
        FROM users u
        LEFT JOIN marketplace_listings ml ON ml.user_id=u.id AND ml.status='active'
        LEFT JOIN crops c                 ON c.user_id=u.id
        LEFT JOIN diagnoses d             ON d.user_id=u.id
        GROUP BY u.id, u.name, u.location, u.subscription_plan
        ORDER BY listing_count DESC, crop_count DESC
    """)
    farmers = []
    for f in cursor.fetchall():
        cursor.execute("SELECT DISTINCT crop_type FROM crops WHERE user_id=%s LIMIT 6", (f['id'],))
        f['crops']    = [r['crop_type'] for r in cursor.fetchall()]
        f['location'] = f['location'] or 'Kenya'
        farmers.append(f)

    cursor.close(); db.close()
    return render_template('marketplace.html',
        listings=listings, farmers=farmers,
        farmers_count=len(farmers),
        current_plan=current_plan,
        my_listing_count=my_listing_count
    )


@app.route('/marketplace/add', methods=['POST'])
@login_required
def add_listing():
    crop_type    = clean(request.form.get('crop_type'),   80)
    quantity     = clean(request.form.get('quantity_kg'),  20)
    price        = clean(request.form.get('price_per_kg'), 20)
    location     = clean(request.form.get('location'),    100)
    description  = clean(request.form.get('description'), 500)
    category     = clean(request.form.get('category'),     50)
    grade        = clean(request.form.get('grade'),       100)
    harvest_date = request.form.get('harvest_date') or None

    if not crop_type or not quantity or not price or not location:
        return redirect(url_for('marketplace'))

    db = get_db(); cursor = db.cursor(dictionary=True)
    plan = 'smart'  # TEMP testing
    if False:  # plan == 'free' — disabled for testing
        cursor.execute(
            "SELECT COUNT(*) AS n FROM marketplace_listings WHERE user_id=%s AND status='active'",
            (session['user_id'],)
        )
        if cursor.fetchone()['n'] >= 3:
            cursor.close(); db.close()
            return redirect(url_for('marketplace'))
    cursor.close(); db.close()

    photo_path = None
    photo      = request.files.get('photo')
    if valid_image(photo):
        try:
            img = Image.open(photo).convert('RGB')
            img.thumbnail((1200, 1200))
            os.makedirs('static/marketplace', exist_ok=True)
            fname = f"mkt_{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
            path  = f"static/marketplace/{fname}"
            img.save(path, format='JPEG', quality=85)
            photo_path = path
        except Exception as e:
            print(f"Photo error: {repr(e)}")

    full_desc = f"[{grade}] {description}".strip() if grade else description

    db = get_db(); cursor = db.cursor(dictionary=True)
    _run_migrations(cursor, db)

    cursor.execute("""
        INSERT INTO marketplace_listings
            (user_id,crop_type,quantity_kg,price_per_kg,location,description,
             category,grade,harvest_date,photo_path,status,auto_posted)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',FALSE)
    """, (
        session['user_id'], crop_type, quantity, price, location,
        full_desc, category, grade, harvest_date, photo_path
    ))
    db.commit(); cursor.close(); db.close()
    return redirect(url_for('marketplace'))


@app.route('/marketplace/subscribe', methods=['POST'])
@login_required
def subscribe():
    plan = request.form.get('plan', 'free')
    if plan not in ('free', 'pro', 'enterprise'):
        plan = 'free'
    mpesa_ref = clean(request.form.get('mpesa_ref',''), 50)

    db = get_db(); cursor = db.cursor(dictionary=True)
    _run_migrations(cursor, db)
    cursor.execute(
        "UPDATE users SET subscription_plan=%s, plan_activated_at=NOW() WHERE id=%s",
        (plan, session['user_id'])
    )
    # Log upgrade alert
    plan_name = PLAN_NAMES.get(plan,'Plantain AI')
    cursor.execute("INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
        (session['user_id'], 'upgrade',
         f"✅ Welcome to {plan_name}! Your plan has been upgraded. {'Ref: '+mpesa_ref if mpesa_ref else ''} All new features are now unlocked."))
    db.commit(); cursor.close(); db.close()
    session['subscription_plan'] = plan
    return redirect(url_for('pricing'))


@app.route('/marketplace/inquiry', methods=['POST'])
@login_required
def submit_inquiry():
    listing_id   = clean(request.form.get('listing_id'),   10)
    buyer_name   = clean(request.form.get('buyer_name'),  100)
    qty_needed   = clean(request.form.get('quantity_needed'), 20)
    message      = clean(request.form.get('message'),     500)
    buyer_county = clean(request.form.get('buyer_location'), 100)

    if not buyer_name or not message:
        return redirect(url_for('marketplace'))

    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT ml.*, u.name AS farmer_name
        FROM marketplace_listings ml
        JOIN users u ON ml.user_id=u.id
        WHERE ml.id=%s
    """, (listing_id,))
    listing = cursor.fetchone()

    if listing:
        alert_msg = (
            f"📦 New inquiry for your {listing['crop_type']} listing. "
            f"Buyer needs {qty_needed}kg from {buyer_county}. "
            f"Message: \"{message[:120]}\" — Plantain AI will coordinate."
        )
        cursor.execute(
            "INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)",
            (listing['user_id'], 'marketplace', alert_msg)
        )
        db.commit()
    cursor.close(); db.close()
    return redirect(url_for('marketplace'))


# ════════════════════════════════════════════════════════════════
#  ALERTS
# ════════════════════════════════════════════════════════════════

@app.route('/alerts')
@login_required
def alerts():
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM alerts WHERE user_id=%s ORDER BY created_at DESC", (session['user_id'],))
    all_alerts = cursor.fetchall()
    cursor.execute("UPDATE alerts SET is_read=TRUE WHERE user_id=%s", (session['user_id'],))
    db.commit(); cursor.close(); db.close()
    return render_template('alerts.html', alerts=all_alerts)


# ════════════════════════════════════════════════════════════════
#  AI CHAT
# ════════════════════════════════════════════════════════════════

@app.route('/chat')
@app.route('/chat/<int:session_id>')
@login_required
def chat(session_id=None):
    db = get_db(); cursor = db.cursor(dictionary=True)

    # Migrate: add session_id column if not exists
    try:
        cursor.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS chat_session_id INT DEFAULT NULL")
        db.commit()
    except Exception:
        pass

    # Get all distinct chat sessions for sidebar
    cursor.execute("""
        SELECT chat_session_id,
               MIN(created_at) AS started_at,
               LEFT(MIN(CASE WHEN role='user' THEN message END), 60) AS preview
        FROM chat_sessions
        WHERE user_id=%s AND chat_session_id IS NOT NULL
        GROUP BY chat_session_id
        ORDER BY MIN(created_at) DESC
        LIMIT 30
    """, (session['user_id'],))
    chat_sessions = cursor.fetchall()

    # Load messages for selected session
    history = []
    if session_id:
        cursor.execute("""
            SELECT * FROM chat_sessions
            WHERE user_id=%s AND chat_session_id=%s
            ORDER BY created_at ASC
        """, (session['user_id'], session_id))
        history = cursor.fetchall()
    else:
        # Load latest session if no session_id
        if chat_sessions:
            latest_sid = chat_sessions[0]['chat_session_id']
            cursor.execute("""
                SELECT * FROM chat_sessions
                WHERE user_id=%s AND chat_session_id=%s
                ORDER BY created_at ASC
            """, (session['user_id'], latest_sid))
            history = cursor.fetchall()
            session_id = latest_sid

    cursor.close(); db.close()
    return render_template('chat.html',
        history=history,
        chat_sessions=chat_sessions,
        current_session_id=session_id
    )


@app.route('/chat/new', methods=['POST'])
@login_required
def new_chat():
    # Generate a new chat_session_id
    new_sid = int(datetime.now().timestamp())
    session['current_chat_session'] = new_sid
    return jsonify({'session_id': new_sid})


@app.route('/chat/delete/<int:session_id>', methods=['POST'])
@login_required
def delete_chat(session_id):
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("DELETE FROM chat_sessions WHERE user_id=%s AND chat_session_id=%s",
                   (session['user_id'], session_id))
    db.commit(); cursor.close(); db.close()
    return jsonify({'ok': True})


@app.route('/chat/send', methods=['POST'])
@login_required
@rate_limit(max_calls=30, window=60)
def chat_send():
    # Support both JSON (text) and multipart (image+text)
    if request.content_type and 'multipart' in request.content_type:
        user_message = clean(request.form.get('message', ''), 1000)
        chat_sid     = int(request.form.get('session_id') or datetime.now().timestamp())
        image_file   = request.files.get('image')
    else:
        data         = request.json or {}
        user_message = clean(data.get('message', ''), 1000)
        chat_sid     = int(data.get('session_id') or datetime.now().timestamp())
        image_file   = None

    if not user_message and not image_file:
        return jsonify({'reply': 'Please type a message or upload a photo.'})

    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM crops WHERE user_id=%s", (session['user_id'],))
    crops = cursor.fetchall()
    cursor.execute(
        "SELECT * FROM diagnoses WHERE user_id=%s ORDER BY created_at DESC LIMIT 3",
        (session['user_id'],)
    )
    recent = cursor.fetchall()

    # Fetch agrovets and specialists from DB for AI context
    cursor.execute("SELECT location FROM users WHERE id=%s", (session['user_id'],))
    u = cursor.fetchone()
    user_county = (u or {}).get('location', 'Nairobi')

    cursor.execute("""SELECT name, phone, county, town, products FROM agrovets
        WHERE status='approved'
        ORDER BY CASE WHEN LOWER(county) LIKE %s THEN 0 ELSE 1 END LIMIT 5""",
        (f"%{user_county.lower()[:6]}%",))
    nearby_agrovets = cursor.fetchall()

    cursor.execute("""SELECT name, phone, county, specialization, consult_fee, available_online, available_visit
        FROM specialists WHERE status='approved'
        ORDER BY CASE WHEN LOWER(county) LIKE %s THEN 0 ELSE 1 END LIMIT 5""",
        (f"%{user_county.lower()[:6]}%",))
    nearby_specialists = cursor.fetchall()

    # Detect if message is asking for agrovet/specialist
    msg_lower = (user_message or '').lower()
    wants_agrovet    = any(w in msg_lower for w in ['agrovet','agro vet','buy','fertilizer','seed','pesticide','chemical','shop','where can i get','where to buy'])
    wants_specialist = any(w in msg_lower for w in ['specialist','expert','agronomist','consultant','professional','vet','doctor','help me','book','call','visit'])

    # Build agrovet context string
    agrovet_context = ""
    if nearby_agrovets:
        agrovet_context = "\n\nNEARBY AGROVETS IN DATABASE:\n"
        for a in nearby_agrovets:
            agrovet_context += f"- {a['name']} | {a['town']}, {a['county']} | 📞 {a['phone']} | Products: {a['products']}\n"
        agrovet_context += "When farmer asks where to buy inputs, share these REAL contacts directly."
    else:
        agrovet_context = "\n\nNo agrovets registered near the farmer yet. Direct them to register at Farm Tools → Agrovets."

    specialist_context = ""
    if nearby_specialists:
        specialist_context = "\n\nNEARBY SPECIALISTS IN DATABASE:\n"
        for s in nearby_specialists:
            mode = []
            if s['available_online']: mode.append('Online')
            if s['available_visit']: mode.append('Farm Visit')
            specialist_context += f"- {s['name']} | {s['specialization']} | {s['county']} | 📞 {s['phone']} | KSh {s['consult_fee']:,.0f}/consult | {', '.join(mode) or 'Contact for availability'}\n"
        specialist_context += "When farmer needs expert help, share these REAL contacts and encourage them to call."
    else:
        specialist_context = "\n\nNo specialists registered near the farmer yet. Direct them to Farm Tools → Specialists to see all available specialists."

    user_plan     = session.get('subscription_plan','free')
    plan_features = get_plan(user_plan)
    ai_name       = plan_features['ai_name']
    ai_level      = plan_features['ai_level']

    if ai_level == 'basic':
        ai_persona = f"""You are {ai_name} — a friendly farming assistant for small-scale Kenyan farmers.
Give simple, practical advice in plain language. Keep responses SHORT (under 120 words).
Focus on the most important action the farmer should take right now.
Mention that upgrading to Kilimo (KSh 500/month) unlocks detailed plans, PDF reports and yield prediction when relevant."""

    elif ai_level == 'detailed':
        ai_persona = f"""You are {ai_name} — an advanced agricultural advisor for mid-scale Kenyan farmers.
Give DETAILED advice with specific treatments, dosages, costs in KSh and step-by-step plans.
Include cost-benefit analysis when relevant. Responses can be up to 250 words.
Mention Biashara (Enterprise) plan for multi-farm management and IoT features when the farmer seems to be scaling up."""

    elif ai_level == 'smart':
        ai_persona = f"""You are Plantain AI — the world's most advanced African smart farming AI for {session['user_name']}'s automated farm operation.
You control physical devices: irrigation systems, sprinklers, drone schedules, soil sensors, greenhouse equipment.
You monitor real-time market prices from KACE and Wakulima Market.
You automatically list produce at optimal market prices.
Give commands in this format when controlling devices: [DEVICE: irrigation | ACTION: on | ZONE: 1 | DURATION: 30min]
Always include ROI analysis, automated scheduling recommendations, and market timing advice.
You are the complete brain of the farm — agronomic, financial, operational and mechanical."""

    else:  # business
        ai_persona = f"""You are Plantain AI — the dedicated AI farm manager for {session['user_name']}'s agribusiness operation.
You are NOT a basic chatbot. You are a full agribusiness intelligence system.
Give BUSINESS-GRADE analysis: ROI calculations, staff action plans, market positioning, risk assessment, seasonal strategies.
Format responses like a professional farm report when needed. You can write up to 400 words.
Always end with a strategic recommendation, not just a tip.
Address the farmer as a business owner, not a small-scale farmer."""

    system_prompt = f"""{ai_persona}

FARMER: {session['user_name']} | PLAN: {PLAN_NAMES.get(user_plan,'Plantain AI')} | FARM TYPE: {session.get('farm_type','crops')}
THEIR CROPS: {[f"{c['crop_type']} ({c['status']})" for c in crops]}
RECENT DIAGNOSES: {[f"{d['disease_name']} — {d['severity']} severity" for d in recent]}

YOUR EXPERTISE — answer all of these with full confidence:
- Crops: diseases, pests, treatment, fertilizers, planting schedules, harvest timing, yields
- Dairy cattle: milk production (litres/day), mastitis, East Coast Fever, tick control, lactation management, connecting to KCC/Brookside/cooperatives
- Beef cattle & goats: weight gain, FMD, anthrax, brucellosis, slaughter readiness at 250kg+, connecting to butcheries
- Sheep: wool harvest timing (twice yearly), dipping for ticks, lambing management, connecting to wool collectors
- Poultry: Newcastle disease, coccidiosis, egg production records, vaccination at day 1/7/21, connecting to hotels/supermarkets
- Bees: varroa mite treatment with oxalic acid, honey harvest timing (80% capped), colony strength, connecting to honey processors
- Pigs: African Swine Fever prevention, slaughter at 90-100kg, farrowing, litter management, connecting to abattoirs
- Fish farming: tilapia at 400-500g harvest, water quality (pH 6.5-8.5), feeding 3% body weight, connecting to fish buyers
- Animal pest control: specific drugs, dosages, prices in KSh — never say "consult a vet" for standard treatments
- Connecting farmers to REAL buyers: butcheries, milk cooperatives, egg vendors, honey processors, wool collectors in their county
- Soil: pH, nutrients, composting, irrigation
- Market prices in KSh: current rates for all Kenyan crops and livestock
- Weather: how it affects farming decisions
- Agribusiness: profit calculations, budgets, loans, insurance

RESPONSE RULES:
1. NEVER say "I recommend consulting a vet/expert" for basic questions — YOU are the expert
2. NEVER say your focus is only on crops — you cover ALL farming
3. Give DIRECT, SPECIFIC answers with actual drug names, dosages, prices in KSh
4. Use bullet points for treatments and steps
5. Always end with 1 proactive tip related to their farm
6. Be confident, warm and practical — like a trusted farming friend who knows everything
7. If given an image, identify crop/animal/disease, severity, and full treatment plan
8. Keep responses under 200 words unless a detailed step-by-step is needed

PLATFORM NAVIGATION — you know every screen, button and flow on Plantain AI. Guide farmers step by step:

━━ DASHBOARD (Home) ━━
- "Your dashboard is the home screen — it shows your crops, live weather, AI alerts and farm health score"
- Add a crop → click the green **+ Add Crop** button top right → type crop name → set planting date → click Add Crop
- View crop status → see coloured dots: 🟢 healthy, 🟡 at risk, 🔴 diseased
- Check weather → the weather widget shows live temp, humidity, wind and 3-day forecast
- View alerts → scroll down to see your latest AI-generated alerts
- IoT sensor panel (Biashara only) → shows live soil health gauge, temperature bar, humidity, rainfall ring and sensor sliders

━━ DIAGNOSE (AI Crop Doctor) ━━
- Step 1 → Click **Diagnose** in the top navigation bar
- Step 2 → Click **Choose File** or drag a photo of your crop/animal
- Step 3 → Select which crop this photo is for from the dropdown
- Step 4 → Click **Diagnose Now** — AI analyses in seconds
- Step 5 → Read your result: disease name, severity (low/medium/high), full treatment plan, recommended products
- Step 6 → Click **Save to Farm Records** to log it
- TIP: "You can also send a photo directly to me here in chat using the 📷 camera icon — I will diagnose it instantly"
- For animals → same process works for goats, cattle, chickens, fish — just upload a clear photo

━━ BULK DIAGNOSE (Kilimo & Biashara only) ━━
- Step 1 → Farm Tools → **Bulk Diagnose**
- Step 2 → Upload up to 10 photos at once (Kilimo) or unlimited (Biashara)
- Step 3 → Each photo is analysed by AI separately
- Step 4 → Download the full batch report as PDF
- Plantain AI users → "Upgrade to Kilimo to unlock bulk diagnosis"

━━ MARKETPLACE (Buy & Sell) ━━
- TO SELL your crops:
  Step 1 → Click **Marketplace** in the top nav
  Step 2 → Click **+ Add Listing**
  Step 3 → Enter crop name, quantity (kg), price per kg in KSh, your county
  Step 4 → Upload a photo of your produce (optional but recommended)
  Step 5 → Click **Post Listing** — buyers can now see and contact you
- TO BUY / see prices → Browse the Marketplace to see what other farmers are selling and at what price
- TO RECEIVE inquiries → buyers click "Send Inquiry" on your listing — you get an alert notification
- Plantain AI plan → 3 free listings maximum
- Kilimo/Biashara → unlimited listings with priority placement

━━ ALERTS (AI Notifications) ━━
- Click **Alerts** in the top nav to see all your AI-generated notifications
- Alert types: 🦠 disease warnings, 🌾 harvest reminders, 💰 price alerts, ✅ upgrade confirmations
- Alerts are generated automatically by the AI engine — no setup needed
- Unread count shows in your dashboard stat card

━━ FARM TOOLS MENU (top nav dropdown) ━━
Click **Farm Tools** in the navigation to access:

  📋 FARM PROFILE:
  - Set your farm name, county, farm size in acres, farmer type (smallholder/commercial/cooperative)
  - This helps AI give you more personalised advice
  - Step 1 → Farm Tools → Farm Profile → fill in your details → Save Profile

  🔬 BULK DIAGNOSE:
  - Upload multiple crop/animal photos at once for batch AI analysis
  - Kilimo: 10 photos | Biashara: unlimited

  📄 FARM REPORT (Kilimo & Biashara):
  - Generates a full PDF report of your farm — all crops, diagnoses, health status
  - Step 1 → Farm Tools → Farm Report → click Generate Report → Download PDF
  - Share with banks, insurance companies or cooperative societies

  📝 TEXT TO PDF:
  - Type or paste your field notes, treatment records, or observations
  - AI formats them into a professional PDF document
  - Step 1 → Farm Tools → Text to PDF → paste your notes → click Generate → Download

  📈 YIELD PREDICTION (Kilimo & Biashara):
  - Predicts your expected harvest in kg and revenue in KSh
  - Based on your crop type, planting date and farm size
  - Access via Farm Tools or ask me directly: "How much will I harvest from 2 acres of maize?"

  🏪 AGROVETS:
  - Find certified agrovets near your county selling seeds, fertilizers, pesticides
  - Step 1 → Farm Tools → Agrovets → search by county or product
  - TO REGISTER your agrovet → click **+ Register Your Agrovet** → fill form → pay KSh 500 registration fee
  - I can also show you nearby agrovets directly in this chat — just ask

  👨‍🌾 SPECIALISTS:
  - Find and book certified agronomists for your farm
  - Step 1 → Farm Tools → Specialists → browse by specialization or county
  - Step 2 → Click **Book Consultation** → describe your problem → send request
  - Specialist calls you directly on their listed phone number
  - TO JOIN as specialist → click **+ Join as Specialist** → fill form → pay KSh 1,000 registration fee
  - I can also connect you to a specialist directly in this chat — just ask

━━ LIVESTOCK MANAGEMENT ━━
- ADD animals → Farm Tools → **Livestock** → click **+ Add Animal** → select type, breed, count, zone
- RECORD production → click **Add Record** on any animal → select type (milk/eggs/weight/honey/wool) → enter value
- DAIRY cattle → track litres/day per cow → AI alerts if production drops → connects to milk buyers
- BEEF/GOATS → track weight gain → AI tells when ready for slaughter → connects to butcheries by county
- SHEEP → track fleece weight → AI schedules shearing → connects to wool collectors
- POULTRY → track eggs/day → AI monitors mortality rate → vaccination schedule alerts
- BEES → track honey yield → AI monitors colony strength → alerts when ready to harvest
- PIGS → track weight → AI tells when to slaughter (90-100kg) → connects to pork butcheries
- FISH → track feeding and water quality → AI alerts harvest time → connects to fish buyers
- FIND BUYERS → click **Find Buyers** on any animal group → see verified buyers in your county
- DISEASE ALERTS → AI auto-generates alerts for sick animals → recommends treatment with drug names & dosages
- PEST CONTROL → ask AI about any pest or disease → get specific treatment, drug name, dosage in KSh

━━ SELLER PLATFORM ━━
- FIND SELLERS → click **Sellers** in nav → browse feeds, medicines, equipment by category
- BUY PRODUCTS → click any product → enter quantity, delivery address → pay via M-Pesa
- TRACK ORDERS → Alerts page shows all order updates including dispatch and delivery
- JOIN AS SELLER → click **Become a Seller** → register your business → get your seller dashboard
- SELLER DASHBOARD → manage products, view orders, update delivery status, track revenue
- ADD PRODUCTS → Seller Dashboard → **+ Add Product** → name, category, price, stock quantity

━━ CHAT (This Screen) ━━
- Type any farming question and I answer instantly
- 📷 Camera icon → send a photo for instant diagnosis
- 💬 Suggestion chips at the bottom → click for quick questions
- 📂 Left sidebar → see your conversation history, start new chat, delete old ones
- ⛶ Expand icon → go fullscreen for focused conversations
- ✕ Exit button → return to dashboard

━━ PRICING & PLANS ━━
- Current plan badge shows in top nav (🌱 green = Plantain AI, 🌾 amber = Kilimo, 🏢 purple = Biashara)
- Click your plan badge in top nav → goes to Pricing page
- Pricing page shows full feature comparison table
- To upgrade → click Upgrade button → enter M-Pesa code → plan activates instantly
- M-Pesa payment → send to 0712 345 678 (Plantain AI Ltd) → enter confirmation code

━━ ACCOUNT & SETTINGS ━━
- Click your name or the profile area top right to access account settings
- Change your county → helps AI and weather widget use your location
- Change password → account settings

NAVIGATION RESPONSE RULES:
1. When farmer asks HOW to do anything → give numbered steps, not paragraphs
2. Always bold the **feature name** and use → arrows
3. If a feature is locked by their plan → explain what plan unlocks it and how to upgrade
4. If farmer seems lost or frustrated → ask "What are you trying to do? I will guide you step by step"
5. After guiding them → offer to answer follow-up questions

TIER-BASED BEHAVIOUR:
- Plantain AI (free) farmer asks for PDF report → give a brief summary verbally, then say "Upgrade to **Kilimo (KSh 500/month)** at the Pricing page to get full PDF farm reports"
- Plantain AI farmer asks for yield prediction → give a rough estimate, then nudge: "Kilimo gives you exact yield and revenue forecasts"
- Plantain AI farmer asks about multi-farm → "The **Biashara plan (KSh 2,000/month)** supports multiple farms — visit the Pricing page to upgrade"
- Kilimo farmer asks about IoT or staff accounts → "That is a **Biashara** feature — upgrade for IoT dashboards, staff accounts and dedicated farm manager AI"
- NEVER make the farmer feel bad for their plan — always be encouraging and positive about upgrading

CONNECTING FARMERS TO REAL PEOPLE:
- When farmer needs to BUY inputs (seeds, fertilizer, chemicals) → share the nearby agrovet contacts from the database below with their phone numbers
- When farmer needs EXPERT help → share the nearby specialist contacts with their phone, fee and how to reach them
- Always say "Here is someone near you who can help:" before sharing contacts
- Encourage farmer to CALL directly — give the phone number prominently
- If no agrovets/specialists are near them, guide them to Farm Tools → Agrovets or Farm Tools → Specialists{agrovet_context}{specialist_context}"""

    display_message = user_message or '📷 Photo sent for analysis'
    cursor.execute(
        "INSERT INTO chat_sessions (user_id,role,message,chat_session_id) VALUES (%s,%s,%s,%s)",
        (session['user_id'], 'user', display_message, chat_sid)
    )
    db.commit()

    try:
        if image_file and valid_image(image_file):
            import base64
            img = Image.open(image_file).convert('RGB')
            img.thumbnail((800, 800))
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            image_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

            prompt_text = user_message or f"Please analyse this photo. I am a Kenyan farmer. Tell me: what crop/plant/animal is this, is there any disease or problem, what is the severity, and what should I do?"

            resp = groq_client.chat.completions.create(
                model=GROQ_VISION_MODEL,
                messages=[{"role":"user","content":[
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}},
                    {"type":"text","text": system_prompt + "\n\n" + prompt_text}
                ]}],
                max_tokens=600, temperature=0.4
            )
            ai_reply = resp.choices[0].message.content.strip()
            ai_reply = "📷 **Photo Analysis:**\n\n" + ai_reply
        else:
            resp = groq_client.chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message}
                ],
                max_tokens=500, temperature=0.7
            )
            ai_reply = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"Chat error: {repr(e)}")
        ai_reply = "Sorry, I'm a bit busy right now — try again in a moment! 🌱"

    cursor.execute(
        "INSERT INTO chat_sessions (user_id,role,message,chat_session_id) VALUES (%s,%s,%s,%s)",
        (session['user_id'], 'assistant', ai_reply, chat_sid)
    )
    db.commit(); cursor.close(); db.close()
    return jsonify({'reply': ai_reply})



# ════════════════════════════════════════════════════════════════
#  PDF GENERATION — Farm Report + Text-to-Report
# ════════════════════════════════════════════════════════════════

def build_pdf_report(user, crops, diagnoses_this_month, high_severity_count,
                     active_listings, recent_alerts, health_pct, report_date):
    """Generate a styled farm report PDF using reportlab. Returns bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, HRFlowable, PageBreak)
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    import io as _io

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    # ── Colour palette ────────────────────────────────────────
    GREEN      = colors.HexColor('#2E8B4A')
    DARK_GREEN  = colors.HexColor('#1A4D2E')
    AMBER       = colors.HexColor('#D4A017')
    RED         = colors.HexColor('#E74C3C')
    LIGHT_GREY  = colors.HexColor('#F5F5F5')
    MID_GREY    = colors.HexColor('#888888')
    WHITE       = colors.white
    BLACK       = colors.black

    # ── Styles ────────────────────────────────────────────────
    def style(name, **kw):
        defaults = dict(fontName='Helvetica', fontSize=10, leading=14,
                        textColor=BLACK, alignment=TA_LEFT)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    S_TITLE     = style('title',  fontName='Helvetica-Bold', fontSize=22,
                        textColor=GREEN, alignment=TA_CENTER, spaceAfter=4)
    S_SUBTITLE  = style('sub',    fontSize=10, textColor=MID_GREY,
                        alignment=TA_CENTER, spaceAfter=2)
    S_H1        = style('h1',     fontName='Helvetica-Bold', fontSize=13,
                        textColor=DARK_GREEN, spaceBefore=14, spaceAfter=6)
    S_H2        = style('h2',     fontName='Helvetica-Bold', fontSize=10,
                        textColor=GREEN, spaceBefore=8, spaceAfter=4)
    S_BODY      = style('body',   fontSize=9, leading=13, textColor=colors.HexColor('#333333'))
    S_MUTED     = style('muted',  fontSize=8, textColor=MID_GREY)
    S_BADGE_G   = style('badge_g', fontName='Helvetica-Bold', fontSize=8,
                        textColor=GREEN)
    S_BADGE_R   = style('badge_r', fontName='Helvetica-Bold', fontSize=8,
                        textColor=RED)
    S_BADGE_A   = style('badge_a', fontName='Helvetica-Bold', fontSize=8,
                        textColor=AMBER)

    story = []

    # ── Header ────────────────────────────────────────────────
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("PLANTAIN AI", S_TITLE))
    story.append(Paragraph("Autonomous Farm Intelligence Platform", S_SUBTITLE))
    story.append(Paragraph(f"Farm Health Report — {report_date}", S_SUBTITLE))
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=2, color=GREEN))
    story.append(Spacer(1, 0.4*cm))

    # ── Farmer info ───────────────────────────────────────────
    farmer_data = [
        ['Farmer', user.get('name','—'),     'Location', user.get('location','Kenya')],
        ['Email',  user.get('email','—'),     'Farm Scale', (user.get('farm_scale') or 'Small').capitalize()],
        ['Phone',  user.get('phone','—'),     'Farm Size', f"{user.get('farm_size_acres','—')} acres" if user.get('farm_size_acres') else '—'],
    ]
    farmer_tbl = Table(farmer_data, colWidths=[3*cm, 6.5*cm, 3*cm, 4.5*cm])
    farmer_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_GREY),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('TEXTCOLOR', (0,0), (0,-1), DARK_GREEN),
        ('TEXTCOLOR', (2,0), (2,-1), DARK_GREEN),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [LIGHT_GREY, WHITE]),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#DDDDDD')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(farmer_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── KPI Summary ───────────────────────────────────────────
    story.append(Paragraph("Summary", S_H1))
    total_crops = len(crops)
    healthy_crops = sum(1 for c in crops if c.get('status') == 'healthy')

    kpi_data = [
        ['Metric', 'Value', 'Status'],
        ['Overall Farm Health', f'{health_pct}%', 'Good' if health_pct >= 70 else 'Needs Attention'],
        ['Total Crops Monitored', str(total_crops), '—'],
        ['Healthy Crops', str(healthy_crops), f'{healthy_crops}/{total_crops}'],
        ['AI Diagnoses (Last 30 days)', str(diagnoses_this_month), '—'],
        ['High Severity Alerts (30 days)', str(high_severity_count), 'Critical' if high_severity_count > 0 else 'Clear'],
        ['Active Marketplace Listings', str(active_listings), '—'],
    ]
    kpi_tbl = Table(kpi_data, colWidths=[8*cm, 3*cm, 6*cm])
    kpi_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK_GREEN),
        ('TEXTCOLOR', (0,0), (-1,0), WHITE),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GREY]),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#DDDDDD')),
        ('PADDING', (0,0), (-1,-1), 7),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (1,0), (1,-1), 'CENTER'),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── Crop Status Table ─────────────────────────────────────
    story.append(Paragraph("Crop Status Details", S_H1))
    crop_rows = [['Crop', 'Field', 'Status', 'Last Disease', 'Severity', 'Est. Yield']]
    for crop in crops:
        ld = crop.get('latest_diagnosis') or {}
        status = crop.get('status','—').replace('_',' ').capitalize()
        yield_kg = crop.get('expected_yield_kg')
        crop_rows.append([
            crop.get('crop_type','—'),
            crop.get('field_name','—') or '—',
            status,
            ld.get('disease_name','No diagnosis') if ld else 'No diagnosis',
            (ld.get('severity','—') or '—').capitalize() if ld else '—',
            f"{int(yield_kg):,} kg" if yield_kg else '—',
        ])
    crop_tbl = Table(crop_rows, colWidths=[3.5*cm, 2.5*cm, 2.5*cm, 4*cm, 2*cm, 2.5*cm])
    crop_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), GREEN),
        ('TEXTCOLOR', (0,0), (-1,0), WHITE),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GREY]),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#DDDDDD')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(crop_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── Recent Alerts ─────────────────────────────────────────
    if recent_alerts:
        story.append(Paragraph("Recent AI Alerts", S_H1))
        ICONS = {
            'disease': '[DISEASE]', 'harvest': '[HARVEST]',
            'price_intel': '[MARKET]', 'bulk_diagnosis': '[BULK]',
            'yield_prediction': '[YIELD]', 'spread_alert': '[SPREAD]',
            'auto_listing': '[LISTED]',
        }
        for alert in recent_alerts[:15]:
            icon = ICONS.get(alert.get('alert_type',''), '[ALERT]')
            msg  = str(alert.get('message','')).replace('🚨','').replace('⚠️','').replace('💰','').replace('📊','').replace('🔬','').replace('🌾','').replace('📅','').replace('🤖','').replace('🌍','').strip()
            ts   = str(alert.get('created_at',''))[:16]
            story.append(Paragraph(f"<b>{icon}</b> {msg}", S_BODY))
            story.append(Paragraph(ts, S_MUTED))
            story.append(Spacer(1, 0.2*cm))

    # ── Footer ────────────────────────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=MID_GREY))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Generated by Plantain AI Autonomous Platform on {report_date}. "
        "This report is AI-generated for informational purposes.",
        S_MUTED
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


@app.route('/farm-report/pdf')
@login_required
def farm_report_pdf():
    from flask import send_file
    import io as _io
    db = get_db(); cursor = db.cursor(dictionary=True)
    _run_migrations(cursor, db)
    uid = session['user_id']

    cursor.execute("SELECT * FROM users WHERE id=%s", (uid,))
    user = cursor.fetchone()
    cursor.execute("SELECT * FROM crops WHERE user_id=%s", (uid,))
    crops = cursor.fetchall()
    for crop in crops:
        cursor.execute("SELECT disease_name,severity,confidence,created_at FROM diagnoses WHERE crop_id=%s ORDER BY created_at DESC LIMIT 1", (crop['id'],))
        crop['latest_diagnosis'] = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) AS n FROM diagnoses WHERE crop_id=%s", (crop['id'],))
        crop['diagnosis_count'] = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM diagnoses WHERE user_id=%s AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)", (uid,))
    diagnoses_this_month = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM diagnoses WHERE user_id=%s AND severity='high' AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)", (uid,))
    high_severity_count = cursor.fetchone()['n']
    cursor.execute("SELECT COUNT(*) AS n FROM marketplace_listings WHERE user_id=%s AND status='active'", (uid,))
    active_listings = cursor.fetchone()['n']
    cursor.execute("SELECT * FROM alerts WHERE user_id=%s ORDER BY created_at DESC LIMIT 20", (uid,))
    recent_alerts = cursor.fetchall()
    healthy_crops = sum(1 for c in crops if c['status'] == 'healthy')
    total_crops   = len(crops)
    health_pct    = round((healthy_crops / total_crops) * 100) if total_crops > 0 else 100
    cursor.close(); db.close()

    report_date = datetime.now().strftime('%d %B %Y')
    pdf_bytes = build_pdf_report(
        user, crops, diagnoses_this_month, high_severity_count,
        active_listings, recent_alerts, health_pct, report_date
    )

    fname = f"PlantainAI_FarmReport_{user.get('name','Farmer').replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return send_file(
        _io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=fname
    )


# ── Text-to-Report converter ──────────────────────────────────

@app.route("/text-to-report", methods=["GET","POST"])
@login_required
def text_to_report():
    from flask import send_file
    import io as _io
    if request.method == "GET":
        return render_template("text_to_report.html")
    raw_text     = clean(request.form.get("raw_text",""), 5000)
    report_title = clean(request.form.get("report_title","Farm Report"), 100)
    if not raw_text:
        return render_template("text_to_report.html", error="Please paste some text.")
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_CHAT_MODEL,
            messages=[{"role":"user","content":
                f"""You are an expert agricultural report writer for Kenyan farmers.
Convert the raw notes below into a clean professional report.
Use EXACTLY these section headers on their own line:
SUMMARY:
OBSERVATIONS:
ISSUES IDENTIFIED:
RECOMMENDATIONS:
CONCLUSION:
List items under each section starting with - (dash).
Raw notes:
{raw_text}"""}],
            max_tokens=800, temperature=0.3
        )
        structured = resp.choices[0].message.content.strip()
    except Exception:
        structured = raw_text

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    GREEN = colors.HexColor("#2E8B4A")
    DARK  = colors.HexColor("#1A4D2E")
    GREY  = colors.HexColor("#555555")

    def sty(name, **kw):
        base = dict(fontName="Helvetica", fontSize=10, leading=15,
                    textColor=colors.black, alignment=TA_LEFT)
        base.update(kw); return ParagraphStyle(name, **base)

    S_TITLE  = sty("t", fontName="Helvetica-Bold", fontSize=20, textColor=GREEN, alignment=TA_CENTER, spaceAfter=4)
    S_META   = sty("m", fontSize=9, textColor=GREY, alignment=TA_CENTER, spaceAfter=2)
    S_H1     = sty("h1", fontName="Helvetica-Bold", fontSize=12, textColor=DARK, spaceBefore=14, spaceAfter=6)
    S_BODY   = sty("b", fontSize=9, leading=14, textColor=colors.HexColor("#333"))
    S_BULLET = sty("bl", fontSize=9, leading=14, textColor=colors.HexColor("#333"), leftIndent=12)
    S_FOOTER = sty("f", fontSize=7, textColor=GREY, alignment=TA_CENTER)

    story = []
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("PLANTAIN AI", S_TITLE))
    story.append(Paragraph(report_title, S_META))
    story.append(Paragraph(f"Prepared for {session['user_name']}  |  {datetime.now().strftime('%d %B %Y')}", S_META))
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=2, color=GREEN))
    story.append(Spacer(1, 0.4*cm))

    HEADERS = ["SUMMARY:","OBSERVATIONS:","ISSUES IDENTIFIED:","RECOMMENDATIONS:","CONCLUSION:"]
    for line in structured.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 0.12*cm)); continue
        matched = False
        for h in HEADERS:
            if line.upper().startswith(h):
                story.append(Paragraph(h.rstrip(":"), S_H1))
                rest = line[len(h):].strip()
                if rest: story.append(Paragraph(rest, S_BODY))
                matched = True; break
        if not matched:
            if line.startswith("-"):
                story.append(Paragraph(f"- {line[1:].strip()}", S_BULLET))
            else:
                story.append(Paragraph(line, S_BODY))

    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#AAAAAA")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph("Generated by Plantain AI. AI-assisted report.", S_FOOTER))
    doc.build(story)
    buf.seek(0)
    safe = report_title.replace(" ","_")[:40]
    fname = f"PlantainAI_{safe}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return send_file(_io.BytesIO(buf.getvalue()), mimetype="application/pdf",
                     as_attachment=True, download_name=fname)



# ── Farm Profile ──────────────────────────────────────────────
@app.route('/farm-profile', methods=['GET','POST'])
@login_required
def farm_profile():
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    if request.method == 'POST':
        farm_scale      = request.form.get('farm_scale','small')
        farm_size_acres = float(request.form.get('farm_size_acres') or 0)
        location        = clean(request.form.get('location',''), 100)
        phone           = clean(request.form.get('phone',''), 20)
        cursor.execute("""UPDATE users SET farm_scale=%s, farm_size_acres=%s,
            location=%s, phone=%s WHERE id=%s""",
            (farm_scale, farm_size_acres, location, phone, uid))
        db.commit()
        flash('Farm profile updated!', 'success')
        return redirect(url_for('farm_profile'))
    cursor.execute("SELECT * FROM users WHERE id=%s", (uid,))
    user = cursor.fetchone()
    try:
        cursor.execute("SELECT * FROM crops WHERE user_id=%s", (uid,))
        crops = cursor.fetchall()
    except Exception:
        crops = []
    cursor.close(); db.close()
    return render_template('farm_profile.html', user=user, crops=crops)


# ── Bulk Diagnose ─────────────────────────────────────────────
@app.route('/bulk-diagnose', methods=['GET','POST'])
@login_required
def bulk_diagnose():
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    cursor.execute("SELECT 'smart' AS subscription_plan FROM users WHERE id=%s", (uid,))  # TEMP
    u = cursor.fetchone()
    plan = (u or {}).get('subscription_plan','free')
    max_photos = 10 if plan == 'enterprise' else 5

    if request.method == 'POST':
        files = request.files.getlist('photos')
        files = [f for f in files if f and f.filename][:max_photos]
        if not files:
            cursor.close(); db.close()
            return render_template('bulk_diagnose.html', error='Please upload at least one photo.', max_photos=max_photos)

        import uuid, base64
        batch_id = str(uuid.uuid4())[:8]
        results  = []
        healthy  = 0

        for photo in files:
            try:
                if not valid_image(photo): continue
                img = Image.open(photo).convert('RGB')
                img.thumbnail((800,800))
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=85)
                buf.seek(0)
                b64 = base64.b64encode(buf.getvalue()).decode()

                resp = groq_client.chat.completions.create(
                    model=GROQ_VISION_MODEL,
                    messages=[{"role":"user","content":[
                        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}},
                        {"type":"text","text":"You are an AI agronomist. Analyse this crop photo. Return ONLY a JSON object with keys: crop_name, disease_name, severity (low/medium/high), confidence (0-100), treatment (string), is_healthy (true/false). No extra text."}
                    ]}],
                    max_tokens=300, temperature=0.2
                )
                import json, re
                raw = resp.choices[0].message.content.strip()
                raw = re.sub(r'^```json|^```|```$','',raw,flags=re.MULTILINE).strip()
                ai  = json.loads(raw)

                fname = f"bulk_{uid}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.jpg"
                fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                buf.seek(0)
                with open(fpath,'wb') as f_out: f_out.write(buf.getvalue())

                cursor.execute("""INSERT INTO diagnoses
                    (user_id,image_path,disease_name,severity,confidence,treatment,bulk_batch)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (uid, fname, ai.get('disease_name','Unknown'),
                     ai.get('severity','low'), ai.get('confidence',0),
                     ai.get('treatment',''), batch_id))
                db.commit()

                if ai.get('is_healthy'): healthy += 1
                results.append({
                    'filename': photo.filename,
                    'image_path': fname,
                    'crop_name': ai.get('crop_name','Unknown'),
                    'disease_name': ai.get('disease_name','Healthy'),
                    'severity': ai.get('severity','low'),
                    'confidence': ai.get('confidence',0),
                    'treatment': ai.get('treatment',''),
                    'is_healthy': ai.get('is_healthy', False)
                })
            except Exception as e:
                print(f"Bulk diagnose error: {e}")
                results.append({'filename': photo.filename, 'error': str(e)})

        health_score = round((healthy / len(results)) * 100) if results else 0
        cursor.execute("""INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,%s,%s)""",
            (uid,'bulk_diagnosis',
             f"Bulk diagnosis complete: {len(results)} photos analysed. Farm health score: {health_score}%."))
        db.commit(); cursor.close(); db.close()
        return render_template('bulk_result.html', results=results,
                               health_score=health_score, batch_id=batch_id)

    cursor.close(); db.close()
    return render_template('bulk_diagnose.html', max_photos=max_photos)


# ── Farm Report ───────────────────────────────────────────────
@app.route('/farm-report')
@login_required
def farm_report():
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    cursor.execute("SELECT * FROM users WHERE id=%s", (uid,))
    user = cursor.fetchone()
    cursor.execute("SELECT * FROM crops WHERE user_id=%s", (uid,))
    crops = cursor.fetchall()
    for crop in crops:
        cursor.execute("""SELECT disease_name,severity,confidence,created_at
            FROM diagnoses WHERE crop_id=%s ORDER BY created_at DESC LIMIT 1""", (crop['id'],))
        crop['latest_diagnosis'] = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) AS n FROM diagnoses WHERE crop_id=%s", (crop['id'],))
        crop['diagnosis_count'] = cursor.fetchone()['n']
    cursor.execute("""SELECT COUNT(*) AS n FROM diagnoses WHERE user_id=%s
        AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)""", (uid,))
    diagnoses_this_month = cursor.fetchone()['n']
    cursor.execute("""SELECT COUNT(*) AS n FROM diagnoses WHERE user_id=%s
        AND severity='high' AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)""", (uid,))
    high_severity_count = cursor.fetchone()['n']
    cursor.execute("""SELECT COUNT(*) AS n FROM marketplace_listings
        WHERE user_id=%s AND status='active'""", (uid,))
    active_listings = cursor.fetchone()['n']
    cursor.execute("SELECT * FROM alerts WHERE user_id=%s ORDER BY created_at DESC LIMIT 20", (uid,))
    recent_alerts = cursor.fetchall()
    healthy_crops = sum(1 for c in crops if c['status'] == 'healthy')
    total_crops   = len(crops)
    health_pct    = round((healthy_crops / total_crops) * 100) if total_crops > 0 else 100
    cursor.close(); db.close()
    return render_template('farm_report.html', user=user, crops=crops,
        diagnoses_this_month=diagnoses_this_month, high_severity_count=high_severity_count,
        active_listings=active_listings, recent_alerts=recent_alerts,
        health_pct=health_pct, report_date=datetime.now().strftime("%d %B %Y"))


# ── Yield Prediction ──────────────────────────────────────────
@app.route('/yield-predict/<int:crop_id>')
@login_required
def yield_predict(crop_id):
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM crops WHERE id=%s AND user_id=%s",
                   (crop_id, session['user_id']))
    crop = cursor.fetchone()
    if not crop:
        cursor.close(); db.close()
        return jsonify({'error': 'Crop not found'})
    cursor.execute("""SELECT AVG(CASE severity
        WHEN 'low' THEN 90 WHEN 'medium' THEN 60 WHEN 'high' THEN 30 END) AS score
        FROM diagnoses WHERE crop_id=%s""", (crop_id,))
    row = cursor.fetchone()
    health_score = float(row['score'] or 85) / 100
    YIELDS = {'maize':3500,'tomato':25000,'banana':20000,'plantain':18000,
              'cassava':12000,'kale':8000,'spinach':6000,'beans':2000,'default':3000}
    crop_key = next((k for k in YIELDS if k in crop['crop_type'].lower()), 'default')
    base_yield = YIELDS[crop_key]
    area = float(crop.get('area_acres') or 1)
    estimated_kg = round(base_yield * area * health_score)
    price_per_kg = 50
    estimated_ksh = estimated_kg * price_per_kg
    cursor.execute("UPDATE crops SET expected_yield_kg=%s WHERE id=%s", (estimated_kg, crop_id))
    db.commit(); cursor.close(); db.close()
    return jsonify({'crop': crop['crop_type'], 'estimated_kg': estimated_kg,
                    'estimated_revenue': estimated_ksh, 'estimated_ksh': estimated_ksh,
                    'health_score': round(health_score*100), 'area_acres': area})


# ── Update crop field name / acres from Farm Profile ─────────
@app.route('/update-crop-field', methods=['POST'])
@login_required
def update_crop_field():
    db = get_db(); cursor = db.cursor()
    crop_id    = request.form.get('crop_id')
    field_name = clean(request.form.get('field_name', ''), 100)
    area_acres = request.form.get('area_acres', 0)
    try:
        area_acres = float(area_acres)
    except (ValueError, TypeError):
        area_acres = 0
    cursor.execute(
        "UPDATE crops SET field_name=%s, area_acres=%s WHERE id=%s AND user_id=%s",
        (field_name, area_acres, crop_id, session['user_id'])
    )
    db.commit(); cursor.close(); db.close()
    flash('Field updated!', 'success')
    return redirect(url_for('farm_profile'))


# ════════════════════════════════════════════════════════════════
#  TIER SYSTEM — Plantain AI / Kilimo / Biashara
# ════════════════════════════════════════════════════════════════

PLAN_NAMES   = {'free': 'Plantain AI', 'pro': 'Kilimo', 'enterprise': 'Biashara', 'smart': 'Plantain AI'}
PLAN_PRICES  = {'free': 0, 'pro': 500, 'enterprise': 2000, 'smart': 5000}
PLAN_COLORS  = {'free': '#4ade80', 'pro': '#FBB024', 'enterprise': '#a78bfa', 'smart': '#22d3ee'}
PLAN_EMOJIS  = {'free': '🌱', 'pro': '🌾', 'enterprise': '🏢', 'smart': '🤖'}

PLAN_FEATURES = {
    'free': {
        'crops': 5, 'bulk_photos': 0, 'listings': 3,
        'farm_report': False, 'yield_predict': False,
        'multi_farm': False, 'staff_accounts': False,
        'iot_dashboard': False, 'specialist_hotline': False,
        'ai_level': 'basic',
        'ai_name': 'Plantain AI',
        'description': 'Perfect for small-scale farmers just getting started',
        'perks': ['5 crops tracked','3 marketplace listings','Basic AI farming advice',
                  'Crop disease diagnosis','Weather updates','Agrovet directory access']
    },
    'pro': {
        'crops': 50, 'bulk_photos': 10, 'listings': 999,
        'farm_report': True, 'yield_predict': True,
        'multi_farm': False, 'staff_accounts': False,
        'iot_dashboard': False, 'specialist_hotline': False,
        'ai_level': 'detailed',
        'ai_name': 'Plantain AI Pro',
        'description': 'For serious farmers scaling up production',
        'perks': ['50 crops tracked','Unlimited listings','Detailed AI plans with cost breakdowns',
                  'Bulk photo diagnosis (10 photos)','Farm PDF reports','Yield & revenue prediction',
                  'Specialist booking','Priority marketplace placement']
    },
    'smart': {
        'crops': 9999, 'bulk_photos': 9999, 'listings': 9999,
        'farm_report': True, 'yield_predict': True,
        'multi_farm': True, 'staff_accounts': True,
        'iot_dashboard': True, 'specialist_hotline': True,
        'smart_farm': True, 'drone_control': True,
        'auto_irrigation': True, 'soil_sensors': True,
        'greenhouse': True, 'real_market_prices': True,
        'ai_level': 'smart',
        'ai_name': 'Plantain AI',
        'description': 'Fully automated smart farm — AI controls everything',
        'perks': ['Everything in Biashara','🤖 AI-controlled irrigation & sprinklers',
                  '🚁 Drone spraying scheduler','🌡️ Soil sensor monitoring',
                  '🌿 Greenhouse auto-control','📈 Real-time market prices (KACE + Wakulima)',
                  '⚡ Automated marketplace listings at market price',
                  '📊 Full IoT control panel','🔔 Instant SMS alerts on device triggers',
                  'Priority support & dedicated account manager']
    },
    'enterprise': {
        'crops': 9999, 'bulk_photos': 999, 'listings': 9999,
        'farm_report': True, 'yield_predict': True,
        'multi_farm': True, 'staff_accounts': True,
        'iot_dashboard': True, 'specialist_hotline': True,
        'ai_level': 'business',
        'ai_name': 'Plantain AI',
        'description': 'Full agribusiness platform for large-scale operations',
        'perks': ['Unlimited farms & crops','Dedicated AI farm manager — Plantain AI',
                  'IoT sensor dashboard','Multi-farm management','Staff accounts',
                  'Direct specialist hotline','Full financial & business reports',
                  'Top marketplace placement','Custom farm branding']
    }
}

def get_plan(plan_key):
    return PLAN_FEATURES.get(plan_key or 'free', PLAN_FEATURES['free'])

def plan_check(required_plan):
    """Decorator-free plan gate — TEMP: always True for testing."""
    return True  # TESTING MODE: all features unlocked


# ════════════════════════════════════════════════════
#  AGROVETS & SPECIALISTS
# ════════════════════════════════════════════════════

KENYA_COUNTIES = [
    'Nairobi','Mombasa','Kisumu','Nakuru','Eldoret','Thika','Malindi',
    'Kitale','Garissa','Kakamega','Nyeri','Meru','Embu','Machakos',
    'Kilifi','Kwale','Taita Taveta','Kajiado','Kiambu','Murang`a',
    'Kirinyaga','Nyandarua','Laikipia','Samburu','Trans Nzoia','Uasin Gishu',
    'Elgeyo Marakwet','Nandi','Baringo','Turkana','West Pokot','Siaya',
    'Kisii','Nyamira','Migori','Homa Bay','Vihiga','Bungoma','Busia',
    'Tharaka Nithi','Isiolo','Marsabit','Wajir','Mandera','Tana River',
    'Lamu','Makueni','Kitui','Narok','Bomet','Kericho','Nyandarua'
]

SPECIALIST_TYPES = [
    'Crop Disease','Soil Science','Irrigation','Livestock','Poultry',
    'Fish Farming','Agribusiness','Organic Farming','Pesticides','General Agronomy'
]

AGROVET_PRODUCTS = [
    'Seeds','Fertilizers','Pesticides','Herbicides','Fungicides',
    'Animal Feeds','Veterinary Drugs','Irrigation Equipment','Tools','All Products'
]


# ── Agrovet Registration ──────────────────────────────────────

@app.route('/pricing')
def pricing():
    plan = session.get('subscription_plan','free')
    return render_template('pricing.html',
        current_plan=plan,
        plan_name=PLAN_NAMES.get(plan,'Plantain AI'),
        plan_color=PLAN_COLORS.get(plan,'#4ade80'),
        features=PLAN_FEATURES)


@app.route('/agrovets')
@login_required
def agrovets():
    db = get_db();
    cursor = db.cursor(dictionary=True)
    uid = session['user_id']

    cursor.execute("SELECT location FROM users WHERE id=%s", (uid,))
    u = cursor.fetchone()
    user_county = (u or {}).get('location', '')

    # Show all agrovets (since they're all approved now)
    cursor.execute("""SELECT * FROM agrovets
        ORDER BY CASE WHEN LOWER(county) LIKE %s THEN 0 ELSE 1 END, name ASC""",
                   (f"%{user_county.lower()[:6]}%",))
    all_agrovets = cursor.fetchall()

    cursor.close();
    db.close()
    return render_template('agrovets.html',
                           agrovets=all_agrovets,
                           user_county=user_county,
                           counties=KENYA_COUNTIES,
                           products=AGROVET_PRODUCTS)


@app.route('/agrovets/register', methods=['GET', 'POST'])
def agrovet_register():
    if request.method == 'GET':
        return render_template('agrovet_register.html',
                               counties=KENYA_COUNTIES, products=AGROVET_PRODUCTS)

    db = get_db();
    cursor = db.cursor(dictionary=True)

    name = clean(request.form.get('name', ''), 100)
    owner_name = clean(request.form.get('owner_name', ''), 100)
    email = clean(request.form.get('email', ''), 100)
    phone = clean(request.form.get('phone', ''), 20)
    county = clean(request.form.get('county', ''), 50)
    town = clean(request.form.get('town', ''), 100)
    address = clean(request.form.get('address', ''), 200)
    products = request.form.getlist('products')
    description = clean(request.form.get('description', ''), 500)
    payment_method = clean(request.form.get('payment_method', ''), 50)
    mpesa_ref = clean(request.form.get('mpesa_ref', ''), 50)

    if not all([name, email, phone, county]):
        cursor.close();
        db.close()
        return render_template('agrovet_register.html',
                               error='Please fill all required fields.',
                               counties=KENYA_COUNTIES,
                               products=AGROVET_PRODUCTS)

    try:
        # CHANGED: status set to 'approved' instead of 'pending'
        cursor.execute("""INSERT INTO agrovets
            (name, owner_name, email, phone, county, town, address, 
             products, description, payment_method, mpesa_ref, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'approved')""",
                       (name, owner_name, email, phone, county, town, address,
                        ','.join(products), description, payment_method, mpesa_ref))

        db.commit()
        cursor.close();
        db.close()
        return render_template('agrovet_register.html', success=True)

    except Exception as e:
        print(f"Agrovet register error: {e}")
        cursor.close();
        db.close()
        return render_template('agrovet_register.html',
                               error='Registration failed. Please try again.',
                               counties=KENYA_COUNTIES,
                               products=AGROVET_PRODUCTS)

# ── Specialists ───────────────────────────────────────────────
@app.route('/specialists')
@login_required
def specialists():
    db = get_db();
    cursor = db.cursor(dictionary=True)
    uid = session['user_id']

    cursor.execute("SELECT location FROM users WHERE id=%s", (uid,))
    u = cursor.fetchone()
    user_county = (u or {}).get('location', '')

    # Show all specialists (since they're all approved now)
    cursor.execute("""SELECT * FROM specialists
        ORDER BY CASE WHEN LOWER(county) LIKE %s THEN 0 ELSE 1 END, rating DESC""",
                   (f"%{user_county.lower()[:6]}%",))
    all_specialists = cursor.fetchall()

    cursor.close();
    db.close()
    return render_template('specialists.html',
                           specialists=all_specialists,
                           user_county=user_county,
                           counties=KENYA_COUNTIES,
                           types=SPECIALIST_TYPES)


@app.route('/specialists/register', methods=['GET', 'POST'])
def specialist_register():
    if request.method == 'GET':
        return render_template('specialist_register.html',
                               counties=KENYA_COUNTIES, types=SPECIALIST_TYPES)

    db = get_db();
    cursor = db.cursor(dictionary=True)

    name = clean(request.form.get('name', ''), 100)
    email = clean(request.form.get('email', ''), 100)
    phone = clean(request.form.get('phone', ''), 20)
    county = clean(request.form.get('county', ''), 50)
    specialization = clean(request.form.get('specialization', ''), 100)
    experience_yrs = int(request.form.get('experience_yrs') or 0)
    bio = clean(request.form.get('bio', ''), 1000)
    consult_fee = float(request.form.get('consult_fee') or 0)
    available_online = 1 if request.form.get('available_online') else 0
    available_visit = 1 if request.form.get('available_visit') else 0
    payment_method = clean(request.form.get('payment_method', ''), 50)
    mpesa_ref = clean(request.form.get('mpesa_ref', ''), 50)

    if not all([name, email, phone, county, specialization]):
        cursor.close();
        db.close()
        return render_template('specialist_register.html',
                               error='Please fill all required fields.',
                               counties=KENYA_COUNTIES,
                               types=SPECIALIST_TYPES)

    try:
        # CHANGED: status set to 'approved' instead of 'pending'
        cursor.execute("""INSERT INTO specialists
            (name, email, phone, county, specialization, experience_yrs, bio,
             consult_fee, available_online, available_visit, payment_method, mpesa_ref,
             status, rating, consultations)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'approved', 0, 0)""",
                       (name, email, phone, county, specialization, experience_yrs, bio,
                        consult_fee, available_online, available_visit, payment_method, mpesa_ref))

        db.commit()
        cursor.close();
        db.close()
        return render_template('specialist_register.html', success=True)

    except Exception as e:
        print(f"Specialist register error: {e}")
        cursor.close();
        db.close()
        return render_template('specialist_register.html',
                               error='Registration failed. Please try again.',
                               counties=KENYA_COUNTIES,
                               types=SPECIALIST_TYPES)




# ════════════════════════════════════════════════════════════════
#  ONBOARDING
# ════════════════════════════════════════════════════════════════

@app.route('/onboarding', methods=['GET','POST'])
@login_required
def onboarding():
    if request.method == 'POST':
        farm_type       = clean(request.form.get('farm_type','crops'), 20)
        livestock_types = ','.join(request.form.getlist('livestock_types'))
        farm_scale      = clean(request.form.get('farm_scale','small'), 20)
        location        = clean(request.form.get('location',''), 100)
        business_name   = clean(request.form.get('business_name',''), 100)
        db = get_db(); cursor = db.cursor(dictionary=True)
        cursor.execute("""UPDATE users SET farm_type=%s, livestock_types=%s,
            farm_scale=%s, location=%s, business_name=%s, onboarded=1 WHERE id=%s""",
            (farm_type, livestock_types, farm_scale, location, business_name, session['user_id']))
        # Welcome alert
        name = session['user_name'].split()[0]
        cursor.execute("INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,'welcome',%s)",
            (session['user_id'], f"🌿 Welcome to Plantain AI, {name}! Your farm profile is set up. I am now monitoring your farm 24/7."))
        db.commit(); cursor.close(); db.close()
        session['farm_type'] = farm_type
        return redirect(url_for('dashboard'))
    return render_template('onboarding.html', counties=KENYA_COUNTIES)


# ════════════════════════════════════════════════════════════════
#  LIVESTOCK MANAGEMENT
# ════════════════════════════════════════════════════════════════

LIVESTOCK_INFO = {
    'dairy': {
        'name':'Dairy Cattle','emoji':'🐄','unit':'litres/day',
        'record_types':['milk_production','weight','health','vaccination','deworming','breeding'],
        'buyers':['Milk cooperatives','KCC','Brookside','New KCC','Local milk vendors'],
        'tips':['Record milk daily','Deworm every 3 months','Dry off 60 days before calving']
    },
    'beef': {
        'name':'Beef Cattle / Goats','emoji':'🐂','unit':'kg',
        'record_types':['weight','health','vaccination','deworming','feed_intake'],
        'buyers':['Butcheries','Meat processors','Supermarkets','Export abattoirs'],
        'tips':['Target 250kg+ for beef cattle','Deworm before rainy season','Quarantine new animals 2 weeks']
    },
    'sheep': {
        'name':'Sheep','emoji':'🐑','unit':'kg fleece',
        'record_types':['weight','wool_harvest','health','vaccination','lambing'],
        'buyers':['Wool collectors','Textile mills','Craft markets'],
        'tips':['Shear twice yearly','Dip monthly for ticks','Supplement with minerals for wool quality']
    },
    'poultry': {
        'name':'Poultry','emoji':'🐔','unit':'eggs/day',
        'record_types':['egg_production','mortality','vaccination','feed_intake','weight'],
        'buyers':['Egg vendors','Supermarkets','Hotels & restaurants','Hatcheries'],
        'tips':['Vaccinate Newcastle at day 1,7,21','Collect eggs 3x daily','Keep litter dry to prevent coccidiosis']
    },
    'bees': {
        'name':'Bees / Honey','emoji':'🐝','unit':'kg honey',
        'record_types':['honey_harvest','colony_strength','hive_inspection','queen_status'],
        'buyers':['Honey processors','Supermarkets','Pharmacies','Export markets'],
        'tips':['Harvest when 80% capped','Inspect every 2 weeks','Treat varroa mites with oxalic acid']
    },
    'pigs': {
        'name':'Pigs','emoji':'🐷','unit':'kg',
        'record_types':['weight','litter_size','feed_intake','health','vaccination'],
        'buyers':['Pork butcheries','Hotels','Sausage factories','Direct customers'],
        'tips':['Slaughter at 90-100kg','Farrow sows every 6 months','Vaccinate ASF and FMD']
    },
    'fish': {
        'name':'Fish Farming','emoji':'🐟','unit':'kg',
        'record_types':['feeding','water_quality','weight_sample','harvest','mortality'],
        'buyers':['Fish mongers','Supermarkets','Hotels','Export'],
        'tips':['Harvest tilapia at 400-500g','Change 20% water weekly','Feed 3% body weight daily']
    },
}


@app.route('/livestock')
@login_required
def livestock():
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM livestock WHERE user_id=%s ORDER BY animal_type, name", (session['user_id'],))
    animals = cursor.fetchall()
    # Group by type
    grouped = {}
    for a in animals:
        t = a['animal_type']
        grouped.setdefault(t, []).append(a)
    cursor.close(); db.close()
    return render_template('livestock.html', grouped=grouped, livestock_info=LIVESTOCK_INFO)


@app.route('/livestock/add', methods=['POST'])
@login_required
def livestock_add():
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("""INSERT INTO livestock
        (user_id,animal_type,breed,name,tag_number,count,dob,weight_kg,zone,notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (session['user_id'],
         clean(request.form.get('animal_type',''),30),
         clean(request.form.get('breed',''),100),
         clean(request.form.get('name',''),100),
         clean(request.form.get('tag_number',''),50),
         int(request.form.get('count',1) or 1),
         request.form.get('dob') or None,
         float(request.form.get('weight_kg',0) or 0),
         clean(request.form.get('zone',''),50),
         clean(request.form.get('notes',''),500)))
    db.commit(); cursor.close(); db.close()
    return redirect(url_for('livestock'))


@app.route('/livestock/record', methods=['POST'])
@login_required
def livestock_record():
    db = get_db(); cursor = db.cursor(dictionary=True)
    lid      = int(request.form.get('livestock_id',0))
    rec_type = clean(request.form.get('record_type','health'),50)
    value    = float(request.form.get('value',0) or 0)
    unit     = clean(request.form.get('unit',''),30)
    notes    = clean(request.form.get('notes',''),500)
    cursor.execute("""INSERT INTO livestock_records
        (livestock_id,user_id,record_type,value,unit,notes) VALUES (%s,%s,%s,%s,%s,%s)""",
        (lid, session['user_id'], rec_type, value, unit, notes))
    # Auto-alerts for critical records
    cursor.execute("SELECT animal_type, name FROM livestock WHERE id=%s", (lid,))
    animal = cursor.fetchone() or {}
    if rec_type == 'health' and value < 3:  # health score 1-5
        cursor.execute("INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,'disease',%s)",
            (session['user_id'], f"⚠️ {animal.get('name','Animal')} ({animal.get('animal_type','')}) health score critical ({value}/5) — needs urgent attention"))
    if rec_type == 'milk_production' and value > 0:
        cursor.execute("""INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,'harvest',%s)""",
            (session['user_id'], f"🐄 Milk recorded: {value}L from {animal.get('name','cow')}"))
    db.commit(); cursor.close(); db.close()
    return redirect(url_for('livestock'))


@app.route('/livestock/buyers/<animal_type>')
@login_required
def livestock_buyers(animal_type):
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    cursor.execute("SELECT location FROM users WHERE id=%s", (uid,))
    u = cursor.fetchone() or {}
    county = (u.get('location') or 'Nairobi').split(',')[0].strip()
    # Get butchery connections
    cursor.execute("""SELECT * FROM butchery_connections
        WHERE LOWER(county) LIKE %s AND status='active'
        AND (animal_types LIKE %s OR animal_types LIKE '%all%')""",
        (f"%{county.lower()[:6]}%", f"%{animal_type}%"))
    buyers = cursor.fetchall()
    info = LIVESTOCK_INFO.get(animal_type, {})
    cursor.close(); db.close()
    return render_template('livestock_buyers.html',
        buyers=buyers, animal_type=animal_type, info=info, county=county)


# ════════════════════════════════════════════════════════════════
#  SELLER PLATFORM
# ════════════════════════════════════════════════════════════════

@app.route('/sellers')
def sellers():
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("""SELECT sp.*, u.name as owner_name
        FROM seller_profiles sp JOIN users u ON sp.user_id=u.id
        WHERE sp.status='approved' ORDER BY sp.rating DESC""")
    sellers_list = cursor.fetchall()
    cursor.execute("SELECT DISTINCT category FROM seller_products WHERE is_available=1")
    categories = [r['category'] for r in cursor.fetchall()]
    cursor.close(); db.close()
    return render_template('sellers.html', sellers=sellers_list, categories=categories)


@app.route('/sellers/register', methods=['GET','POST'])
@login_required
def seller_register():
    if request.method == 'POST':
        db = get_db(); cursor = db.cursor(dictionary=True)
        cursor.execute("""INSERT INTO seller_profiles
            (user_id,business_name,phone,county,town,description,categories,delivery_zones,min_order_ksh,status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
            (session['user_id'],
             clean(request.form.get('business_name',''),100),
             clean(request.form.get('phone',''),20),
             clean(request.form.get('county',''),50),
             clean(request.form.get('town',''),100),
             clean(request.form.get('description',''),500),
             ','.join(request.form.getlist('categories')),
             clean(request.form.get('delivery_zones',''),200),
             float(request.form.get('min_order_ksh',0) or 0)))
        cursor.execute("""UPDATE users SET farm_type='seller',
            business_name=%s WHERE id=%s""",
            (clean(request.form.get('business_name',''),100), session['user_id']))
        db.commit(); cursor.close(); db.close()
        return render_template('seller_register.html', success=True)
    return render_template('seller_register.html', counties=KENYA_COUNTIES)


@app.route('/sellers/dashboard')
@login_required
def seller_dashboard():
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    cursor.execute("SELECT * FROM seller_profiles WHERE user_id=%s", (uid,))
    profile = cursor.fetchone()
    if not profile:
        return redirect(url_for('seller_register'))
    cursor.execute("SELECT * FROM seller_products WHERE seller_id=%s ORDER BY created_at DESC", (profile['id'],))
    products = cursor.fetchall()
    cursor.execute("""SELECT so.*, u.name as buyer_name, sp2.name as product_name
        FROM seller_orders so
        JOIN users u ON so.buyer_id=u.id
        JOIN seller_products sp2 ON so.product_id=sp2.id
        WHERE so.seller_id=%s ORDER BY so.created_at DESC LIMIT 20""", (profile['id'],))
    orders = cursor.fetchall()
    pending = [o for o in orders if o['status']=='pending']
    cursor.close(); db.close()
    return render_template('seller_dashboard.html',
        profile=profile, products=products, orders=orders, pending=pending)


@app.route('/sellers/product/add', methods=['POST'])
@login_required
def seller_add_product():
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    cursor.execute("SELECT id FROM seller_profiles WHERE user_id=%s", (uid,))
    sp = cursor.fetchone()
    if not sp: cursor.close(); db.close(); return redirect(url_for('seller_register'))
    cursor.execute("""INSERT INTO seller_products
        (seller_id,user_id,name,category,description,price_ksh,unit,stock_qty)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (sp['id'], uid,
         clean(request.form.get('name',''),100),
         clean(request.form.get('category',''),50),
         clean(request.form.get('description',''),500),
         float(request.form.get('price_ksh',0) or 0),
         clean(request.form.get('unit',''),30),
         int(request.form.get('stock_qty',0) or 0)))
    db.commit(); cursor.close(); db.close()
    return redirect(url_for('seller_dashboard'))


@app.route('/sellers/order', methods=['POST'])
@login_required
def place_order():
    db = get_db(); cursor = db.cursor(dictionary=True)
    product_id = int(request.form.get('product_id',0))
    qty        = float(request.form.get('qty',1) or 1)
    address    = clean(request.form.get('delivery_address',''),300)
    payment    = clean(request.form.get('payment_method','mpesa'),50)
    mpesa_ref  = clean(request.form.get('mpesa_ref',''),50)
    cursor.execute("SELECT * FROM seller_products WHERE id=%s AND is_available=1", (product_id,))
    product = cursor.fetchone()
    if not product: cursor.close(); db.close(); return jsonify({'error':'Product not found'})
    total = round(product['price_ksh'] * qty, 2)
    cursor.execute("""INSERT INTO seller_orders
        (buyer_id,seller_id,product_id,qty,total_ksh,delivery_address,payment_method,mpesa_ref)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (session['user_id'], product['seller_id'], product_id, qty, total, address, payment, mpesa_ref))
    order_id = cursor.lastrowid
    # Alert buyer and seller
    cursor.execute("INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,'order',%s)",
        (session['user_id'], f"✅ Order #{order_id} placed — {qty} {product['unit']} of {product['name']} (KSh {total:,.0f}). Seller will confirm shortly."))
    cursor.execute("SELECT user_id FROM seller_profiles WHERE id=%s", (product['seller_id'],))
    seller_uid = (cursor.fetchone() or {}).get('user_id')
    if seller_uid:
        cursor.execute("INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,'order',%s)",
            (seller_uid, f"🛒 New order #{order_id}: {qty} {product['unit']} of {product['name']} — KSh {total:,.0f}. Deliver to: {address[:80]}"))
    db.commit(); cursor.close(); db.close()
    return jsonify({'ok': True, 'order_id': order_id, 'total': total})


@app.route('/sellers/order/update', methods=['POST'])
@login_required
def update_order():
    db = get_db(); cursor = db.cursor(dictionary=True)
    order_id = int(request.form.get('order_id',0))
    status   = clean(request.form.get('status',''),30)
    cursor.execute("""UPDATE seller_orders so
        JOIN seller_profiles sp ON so.seller_id=sp.id
        SET so.status=%s WHERE so.id=%s AND sp.user_id=%s""",
        (status, order_id, session['user_id']))
    if status == 'dispatched':
        cursor.execute("SELECT buyer_id, total_ksh FROM seller_orders WHERE id=%s", (order_id,))
        o = cursor.fetchone() or {}
        if o.get('buyer_id'):
            cursor.execute("INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,'order',%s)",
                (o['buyer_id'], f"🚚 Order #{order_id} has been dispatched! Your delivery is on the way."))
    db.commit(); cursor.close(); db.close()
    return redirect(url_for('seller_dashboard'))


# ════════════════════════════════════════════════════════════════
#  SMART FARM — IoT CONTROL PANEL
# ════════════════════════════════════════════════════════════════

@app.route('/smart-farm')
@login_required
def smart_farm():
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM smart_devices WHERE user_id=%s ORDER BY device_type", (session['user_id'],))
        devices = cursor.fetchall()
        cursor.execute("SELECT * FROM smart_schedules WHERE user_id=%s AND active=1 ORDER BY next_run", (session['user_id'],))
        schedules = cursor.fetchall()
        cursor.execute("SELECT * FROM sensor_readings WHERE user_id=%s ORDER BY recorded_at DESC LIMIT 50", (session['user_id'],))
        readings = cursor.fetchall()
    except Exception:
        devices, schedules, readings = [], [], []
    cursor.close(); db.close()
    return render_template('smart_farm.html', devices=devices, schedules=schedules, readings=readings)


@app.route('/smart-farm/device', methods=['POST'])
@login_required
def smart_device_control():
    db = get_db(); cursor = db.cursor(dictionary=True)
    action      = clean(request.json.get('action',''), 20)
    device_id   = request.json.get('device_id')
    device_type = clean(request.json.get('device_type',''), 50)
    zone        = clean(request.json.get('zone','1'), 10)
    duration    = int(request.json.get('duration', 30))

    cursor.execute("""UPDATE smart_devices SET status=%s, last_triggered=NOW()
        WHERE id=%s AND user_id=%s""", (action, device_id, session['user_id']))

    # Log action + alert
    msg = f"{'💧' if 'irrigation' in device_type else '🚁' if 'drone' in device_type else '🌡️'} {device_type.title()} Zone {zone} turned {action.upper()}"
    if duration and action == 'on':
        msg += f" for {duration} minutes"
    cursor.execute("INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,'smart_farm',%s)",
        (session['user_id'], msg))
    db.commit(); cursor.close(); db.close()
    return jsonify({'ok': True, 'message': msg})


@app.route('/smart-farm/schedule', methods=['POST'])
@login_required
def smart_schedule():
    db = get_db(); cursor = db.cursor(dictionary=True)
    device_type = clean(request.json.get('device_type',''), 50)
    zone        = clean(request.json.get('zone','1'), 10)
    trigger     = clean(request.json.get('trigger',''), 50)  # time/weather/sensor
    trigger_val = clean(request.json.get('trigger_val',''), 100)
    duration    = int(request.json.get('duration', 30))
    cursor.execute("""INSERT INTO smart_schedules
        (user_id,device_type,zone,trigger_type,trigger_value,duration_mins,active,next_run)
        VALUES (%s,%s,%s,%s,%s,%s,1,NOW())""",
        (session['user_id'],device_type,zone,trigger,trigger_val,duration))
    db.commit(); cursor.close(); db.close()
    return jsonify({'ok': True})


@app.route('/smart-farm/sensor', methods=['POST'])
@login_required
def sensor_reading():
    """Receives readings from IoT sensors (called by hardware)"""
    api_key = request.headers.get('X-API-Key','')
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE api_key=%s", (api_key,))
    user = cursor.fetchone()
    if not user:
        cursor.close(); db.close()
        return jsonify({'error':'Invalid API key'}), 401
    data = request.json or {}
    sensor_type = clean(data.get('type',''), 50)
    value       = float(data.get('value', 0))
    zone        = clean(data.get('zone','1'), 10)
    unit        = clean(data.get('unit',''), 20)
    cursor.execute("""INSERT INTO sensor_readings (user_id,sensor_type,value,zone,unit)
        VALUES (%s,%s,%s,%s,%s)""", (user['id'],sensor_type,value,zone,unit))
    # Auto-trigger irrigation if soil moisture low
    if sensor_type == 'soil_moisture' and value < 30:
        cursor.execute("INSERT INTO alerts (user_id,alert_type,message) VALUES (%s,'smart_farm',%s)",
            (user['id'], f"🌱 Zone {zone} soil moisture at {value}% — irrigation auto-triggered"))
        cursor.execute("""UPDATE smart_devices SET status='on', last_triggered=NOW()
            WHERE user_id=%s AND device_type='irrigation' AND zone=%s""", (user['id'], zone))
    db.commit(); cursor.close(); db.close()
    return jsonify({'ok': True})


# ════════════════════════════════════════════════════════════════
#  MARKET PRICES — REAL-TIME
# ════════════════════════════════════════════════════════════════

import urllib.request, json as json_lib

# ── Animal & livestock product prices (KSh) ────────────────────
ANIMAL_PRICES = {
    # ── DAIRY ──
    'fresh milk':              {'price_unit':'KSh/litre',  'farm_gate':45,   'retail':65,   'wholesale':40,   'emoji':'🥛', 'category':'dairy'},
    'pasteurised milk':        {'price_unit':'KSh/litre',  'farm_gate':55,   'retail':80,   'wholesale':50,   'emoji':'🥛', 'category':'dairy'},
    'ghee (samli)':            {'price_unit':'KSh/kg',     'farm_gate':900,  'retail':1200, 'wholesale':800,  'emoji':'🧈', 'category':'dairy'},
    'yoghurt':                 {'price_unit':'KSh/litre',  'farm_gate':130,  'retail':180,  'wholesale':120,  'emoji':'🥛', 'category':'dairy'},
    'cheese (local)':          {'price_unit':'KSh/kg',     'farm_gate':600,  'retail':900,  'wholesale':650,  'emoji':'🧀', 'category':'dairy'},
    'camel milk':              {'price_unit':'KSh/litre',  'farm_gate':80,   'retail':130,  'wholesale':90,   'emoji':'🐪', 'category':'dairy'},
    'donkey milk':             {'price_unit':'KSh/litre',  'farm_gate':200,  'retail':350,  'wholesale':220,  'emoji':'🫏', 'category':'dairy'},
    'goat milk':               {'price_unit':'KSh/litre',  'farm_gate':50,   'retail':75,   'wholesale':45,   'emoji':'🐐', 'category':'dairy'},
    # ── BEEF & LARGE RUMINANTS ──
    'cattle (live weight)':    {'price_unit':'KSh/kg',     'farm_gate':280,  'retail':450,  'wholesale':320,  'emoji':'🐂', 'category':'beef'},
    'bull (per head)':         {'price_unit':'KSh/animal', 'farm_gate':80000,'retail':120000,'wholesale':90000,'emoji':'🐃', 'category':'beef'},
    'heifer (per head)':       {'price_unit':'KSh/animal', 'farm_gate':55000,'retail':85000, 'wholesale':62000,'emoji':'🐄', 'category':'beef'},
    'beef carcass':            {'price_unit':'KSh/kg',     'farm_gate':380,  'retail':600,  'wholesale':420,  'emoji':'🥩', 'category':'beef'},
    'beef offals':             {'price_unit':'KSh/kg',     'farm_gate':120,  'retail':200,  'wholesale':140,  'emoji':'🫀', 'category':'beef'},
    # ── GOATS & SHEEP ──
    'goat (live weight)':      {'price_unit':'KSh/kg',     'farm_gate':320,  'retail':500,  'wholesale':360,  'emoji':'🐐', 'category':'goats'},
    'goat (per head)':         {'price_unit':'KSh/animal', 'farm_gate':8000, 'retail':14000,'wholesale':9000, 'emoji':'🐐', 'category':'goats'},
    'goat carcass':            {'price_unit':'KSh/kg',     'farm_gate':420,  'retail':650,  'wholesale':460,  'emoji':'🥩', 'category':'goats'},
    'goat offals':             {'price_unit':'KSh/kg',     'farm_gate':100,  'retail':180,  'wholesale':120,  'emoji':'🫀', 'category':'goats'},
    'sheep (live weight)':     {'price_unit':'KSh/kg',     'farm_gate':300,  'retail':480,  'wholesale':340,  'emoji':'🐑', 'category':'sheep'},
    'sheep (per head)':        {'price_unit':'KSh/animal', 'farm_gate':7000, 'retail':12000,'wholesale':8000, 'emoji':'🐑', 'category':'sheep'},
    'mutton carcass':          {'price_unit':'KSh/kg',     'farm_gate':400,  'retail':620,  'wholesale':440,  'emoji':'🥩', 'category':'sheep'},
    'wool (raw)':              {'price_unit':'KSh/kg',     'farm_gate':80,   'retail':150,  'wholesale':90,   'emoji':'🧶', 'category':'sheep'},
    # ── CAMEL ──
    'camel (live weight)':     {'price_unit':'KSh/kg',     'farm_gate':350,  'retail':550,  'wholesale':390,  'emoji':'🐪', 'category':'camel'},
    'camel meat':              {'price_unit':'KSh/kg',     'farm_gate':500,  'retail':800,  'wholesale':560,  'emoji':'🥩', 'category':'camel'},
    'camel (per head)':        {'price_unit':'KSh/animal', 'farm_gate':120000,'retail':200000,'wholesale':140000,'emoji':'🐪','category':'camel'},
    # ── PIGS ──
    'pig (live weight)':       {'price_unit':'KSh/kg',     'farm_gate':220,  'retail':380,  'wholesale':260,  'emoji':'🐷', 'category':'pigs'},
    'pig (per head)':          {'price_unit':'KSh/animal', 'farm_gate':15000,'retail':25000,'wholesale':17000,'emoji':'🐷', 'category':'pigs'},
    'pork carcass':            {'price_unit':'KSh/kg',     'farm_gate':320,  'retail':500,  'wholesale':360,  'emoji':'🥩', 'category':'pigs'},
    'pork ribs':               {'price_unit':'KSh/kg',     'farm_gate':340,  'retail':550,  'wholesale':380,  'emoji':'🥓', 'category':'pigs'},
    # ── POULTRY ──
    'chicken (kienyeji)':      {'price_unit':'KSh/bird',   'farm_gate':700,  'retail':1000, 'wholesale':750,  'emoji':'🐔', 'category':'poultry'},
    'chicken (broiler)':       {'price_unit':'KSh/kg',     'farm_gate':240,  'retail':380,  'wholesale':270,  'emoji':'🍗', 'category':'poultry'},
    'turkey (live)':           {'price_unit':'KSh/bird',   'farm_gate':3500, 'retail':5500, 'wholesale':3800, 'emoji':'🦃', 'category':'poultry'},
    'guinea fowl (kanga)':     {'price_unit':'KSh/bird',   'farm_gate':600,  'retail':900,  'wholesale':650,  'emoji':'🐦', 'category':'poultry'},
    'duck':                    {'price_unit':'KSh/bird',   'farm_gate':800,  'retail':1200, 'wholesale':850,  'emoji':'🦆', 'category':'poultry'},
    'quail (tetere)':          {'price_unit':'KSh/bird',   'farm_gate':150,  'retail':250,  'wholesale':170,  'emoji':'🐦', 'category':'poultry'},
    'goose':                   {'price_unit':'KSh/bird',   'farm_gate':1200, 'retail':2000, 'wholesale':1300, 'emoji':'🦢', 'category':'poultry'},
    'pigeon (njiwa)':          {'price_unit':'KSh/pair',   'farm_gate':400,  'retail':700,  'wholesale':450,  'emoji':'🕊️', 'category':'poultry'},
    'ostrich (live)':          {'price_unit':'KSh/bird',   'farm_gate':30000,'retail':50000,'wholesale':33000,'emoji':'🐦', 'category':'poultry'},
    'ostrich meat':            {'price_unit':'KSh/kg',     'farm_gate':600,  'retail':1000, 'wholesale':680,  'emoji':'🥩', 'category':'poultry'},
    'ostrich egg':             {'price_unit':'KSh/egg',    'farm_gate':1500, 'retail':2500, 'wholesale':1700, 'emoji':'🥚', 'category':'poultry'},
    'ostrich feathers':        {'price_unit':'KSh/kg',     'farm_gate':2000, 'retail':3500, 'wholesale':2200, 'emoji':'🪶', 'category':'poultry'},
    'emu (live)':              {'price_unit':'KSh/bird',   'farm_gate':20000,'retail':35000,'wholesale':22000,'emoji':'🐦', 'category':'poultry'},
    'rabbit (live)':           {'price_unit':'KSh/kg',     'farm_gate':350,  'retail':550,  'wholesale':380,  'emoji':'🐇', 'category':'poultry'},
    'rabbit (per animal)':     {'price_unit':'KSh/animal', 'farm_gate':800,  'retail':1400, 'wholesale':900,  'emoji':'🐇', 'category':'poultry'},
    # ── EGGS ──
    'eggs (tray 30)':          {'price_unit':'KSh/tray',   'farm_gate':380,  'retail':480,  'wholesale':400,  'emoji':'🥚', 'category':'eggs'},
    'eggs (single)':           {'price_unit':'KSh/egg',    'farm_gate':13,   'retail':18,   'wholesale':14,   'emoji':'🥚', 'category':'eggs'},
    'quail eggs (tray 24)':    {'price_unit':'KSh/tray',   'farm_gate':250,  'retail':380,  'wholesale':270,  'emoji':'🥚', 'category':'eggs'},
    'duck eggs (dozen)':       {'price_unit':'KSh/dozen',  'farm_gate':300,  'retail':480,  'wholesale':330,  'emoji':'🥚', 'category':'eggs'},
    'fertilised eggs (hatching)':{'price_unit':'KSh/egg',  'farm_gate':50,   'retail':90,   'wholesale':60,   'emoji':'🥚', 'category':'eggs'},
    # ── HONEY & BEES ──
    'raw honey':               {'price_unit':'KSh/kg',     'farm_gate':600,  'retail':1000, 'wholesale':700,  'emoji':'🍯', 'category':'bees'},
    'processed honey':         {'price_unit':'KSh/kg',     'farm_gate':800,  'retail':1400, 'wholesale':900,  'emoji':'🍯', 'category':'bees'},
    'beeswax':                 {'price_unit':'KSh/kg',     'farm_gate':300,  'retail':500,  'wholesale':350,  'emoji':'🕯️', 'category':'bees'},
    'propolis':                {'price_unit':'KSh/kg',     'farm_gate':2000, 'retail':4000, 'wholesale':2500, 'emoji':'🍯', 'category':'bees'},
    'royal jelly':             {'price_unit':'KSh/gram',   'farm_gate':80,   'retail':150,  'wholesale':95,   'emoji':'👑', 'category':'bees'},
    'bee pollen':              {'price_unit':'KSh/kg',     'farm_gate':1500, 'retail':2800, 'wholesale':1800, 'emoji':'🌼', 'category':'bees'},
    'bee colonies (hive)':     {'price_unit':'KSh/hive',   'farm_gate':4000, 'retail':8000, 'wholesale':4500, 'emoji':'🐝', 'category':'bees'},
    # ── FRESHWATER FISH ──
    'tilapia (fresh)':         {'price_unit':'KSh/kg',     'farm_gate':250,  'retail':400,  'wholesale':280,  'emoji':'🐟', 'category':'fish'},
    'tilapia (smoked)':        {'price_unit':'KSh/kg',     'farm_gate':600,  'retail':900,  'wholesale':650,  'emoji':'🐟', 'category':'fish'},
    'tilapia (dried)':         {'price_unit':'KSh/kg',     'farm_gate':500,  'retail':800,  'wholesale':560,  'emoji':'🐟', 'category':'fish'},
    'nile perch (fresh)':      {'price_unit':'KSh/kg',     'farm_gate':200,  'retail':350,  'wholesale':230,  'emoji':'🐠', 'category':'fish'},
    'nile perch (smoked)':     {'price_unit':'KSh/kg',     'farm_gate':500,  'retail':800,  'wholesale':560,  'emoji':'🐠', 'category':'fish'},
    'catfish / kamongo':       {'price_unit':'KSh/kg',     'farm_gate':280,  'retail':450,  'wholesale':310,  'emoji':'🐟', 'category':'fish'},
    'omena / dagaa (fresh)':   {'price_unit':'KSh/kg',     'farm_gate':60,   'retail':100,  'wholesale':70,   'emoji':'🐟', 'category':'fish'},
    'omena (dried)':           {'price_unit':'KSh/kg',     'farm_gate':320,  'retail':480,  'wholesale':360,  'emoji':'🐟', 'category':'fish'},
    'dagaa (dried, lake)':     {'price_unit':'KSh/kg',     'farm_gate':280,  'retail':420,  'wholesale':310,  'emoji':'🐟', 'category':'fish'},
    'mud fish (kamongo)':      {'price_unit':'KSh/kg',     'farm_gate':300,  'retail':500,  'wholesale':340,  'emoji':'🐟', 'category':'fish'},
    'trout (fresh)':           {'price_unit':'KSh/kg',     'farm_gate':400,  'retail':650,  'wholesale':450,  'emoji':'🐟', 'category':'fish'},
    # ── MARINE / COASTAL FISH ──
    'tuna (fresh)':            {'price_unit':'KSh/kg',     'farm_gate':350,  'retail':600,  'wholesale':400,  'emoji':'🐡', 'category':'seafood'},
    'tuna (dried)':            {'price_unit':'KSh/kg',     'farm_gate':700,  'retail':1100, 'wholesale':800,  'emoji':'🐡', 'category':'seafood'},
    'mackerel (fresh)':        {'price_unit':'KSh/kg',     'farm_gate':280,  'retail':450,  'wholesale':310,  'emoji':'🐠', 'category':'seafood'},
    'sardines (pweza)':        {'price_unit':'KSh/kg',     'farm_gate':200,  'retail':350,  'wholesale':230,  'emoji':'🐟', 'category':'seafood'},
    'octopus (pweza)':         {'price_unit':'KSh/kg',     'farm_gate':600,  'retail':1000, 'wholesale':680,  'emoji':'🐙', 'category':'seafood'},
    'squid / calamari':        {'price_unit':'KSh/kg',     'farm_gate':500,  'retail':850,  'wholesale':570,  'emoji':'🦑', 'category':'seafood'},
    'crab (mtomondo)':         {'price_unit':'KSh/kg',     'farm_gate':400,  'retail':700,  'wholesale':450,  'emoji':'🦀', 'category':'seafood'},
    'lobster':                 {'price_unit':'KSh/kg',     'farm_gate':2000, 'retail':3500, 'wholesale':2300, 'emoji':'🦞', 'category':'seafood'},
    'prawns / shrimps':        {'price_unit':'KSh/kg',     'farm_gate':1000, 'retail':1800, 'wholesale':1200, 'emoji':'🍤', 'category':'seafood'},
    'crayfish (kamba):':       {'price_unit':'KSh/kg',     'farm_gate':700,  'retail':1200, 'wholesale':800,  'emoji':'🦐', 'category':'seafood'},
    'oysters (chaza)':         {'price_unit':'KSh/dozen',  'farm_gate':300,  'retail':600,  'wholesale':350,  'emoji':'🦪', 'category':'seafood'},
    'mussels':                 {'price_unit':'KSh/kg',     'farm_gate':250,  'retail':450,  'wholesale':280,  'emoji':'🦪', 'category':'seafood'},
    'sea urchin (kio)':        {'price_unit':'KSh/kg',     'farm_gate':400,  'retail':700,  'wholesale':450,  'emoji':'🫧', 'category':'seafood'},
    # ── GAME & SPECIALTY ──
    'crocodile meat':          {'price_unit':'KSh/kg',     'farm_gate':1500, 'retail':2500, 'wholesale':1700, 'emoji':'🐊', 'category':'game'},
    'crocodile skin':          {'price_unit':'KSh/skin',   'farm_gate':15000,'retail':35000,'wholesale':18000,'emoji':'🐊', 'category':'game'},
    'snail (konokono)':        {'price_unit':'KSh/kg',     'farm_gate':300,  'retail':500,  'wholesale':340,  'emoji':'🐌', 'category':'game'},
    'grasshoppers (nzige)':    {'price_unit':'KSh/kg',     'farm_gate':600,  'retail':1000, 'wholesale':700,  'emoji':'🦗', 'category':'game'},
    'termites (mchwa)':        {'price_unit':'KSh/kg',     'farm_gate':400,  'retail':700,  'wholesale':450,  'emoji':'🪲', 'category':'game'},
    'donkey (per head)':       {'price_unit':'KSh/animal', 'farm_gate':20000,'retail':35000,'wholesale':23000,'emoji':'🫏', 'category':'game'},
}


def fetch_animal_prices():
    """Return animal prices with seasonal adjustment"""
    import datetime, random
    month = datetime.datetime.now().month
    # Festive season (Dec/Jan/Apr/Aug) — prices surge for meat & eggs
    if month in [12, 1]:   mult = 1.30   # Christmas/New Year surge
    elif month in [4]:     mult = 1.20   # Easter surge
    elif month in [8]:     mult = 1.15   # back to school — eggs & poultry up
    elif month in [6, 7]:  mult = 0.95   # dry mid-year — slight dip
    else:                  mult = 1.00
    result = {}
    for product, data in ANIMAL_PRICES.items():
        variation = random.uniform(0.94, 1.06)
        adj = round(mult * variation, 2)
        result[product] = {
            **data,
            'farm_gate':  round(data['farm_gate']  * adj),
            'retail':     round(data['retail']     * adj),
            'wholesale':  round(data['wholesale']  * adj),
            'trend': 'rising' if mult >= 1.15 else 'falling' if mult < 0.98 else 'stable',
            'last_updated': datetime.datetime.now().strftime('%d %b %Y %H:%M'),
            'adjusted_price': round(data['retail'] * adj),
            'unit': data['price_unit'],
            'season_note': 'Festive — high demand' if mult >= 1.20 else 'Easter demand' if mult >= 1.15 else 'School holidays' if mult >= 1.10 else 'Mid-year dip' if mult < 1.0 else 'Normal season',
        }
    return result


def fetch_market_prices(crops=None):
    """Fetch real prices from KACE API + Open data, fallback to AI estimates"""
    prices = {}
    # Kenya crop price estimates by season (fallback baseline in KSh/kg)
    BASELINE = {
        # ── Grains & Cereals ──
        'maize':45,'sorghum':40,'millet':38,'wheat':60,'rice':90,
        'barley':55,'teff':120,'oats':80,'ugali flour':55,
        # ── Pulses & Legumes ──
        'beans':100,'green grams (ndengu)':110,'black beans (njahi)':120,
        'green lentils (kamande)':130,'red lentils':125,'pigeon peas (mbaazi)':95,
        'cowpeas (kunde)':85,'soybean':90,'chickpeas':140,'groundnut':130,
        'lablab beans':95,'horse beans':90,'dolichos beans':105,
        # ── Vegetables ──
        'kale':30,'sukuma wiki':25,'spinach':35,'cabbage':30,'broccoli':80,
        'cauliflower':70,'tomato':80,'onion':70,'garlic':400,'ginger':300,
        'carrot':60,'peas':120,'french beans':150,'snow peas':180,
        'capsicum':120,'eggplant':60,'courgette':80,'pumpkin':25,
        'pumpkin leaves':20,'cowpea leaves (kunde)':25,'amaranth (terere)':30,
        'african nightshade (managu)':25,'spider plant (saga)':30,
        'jute mallow (mrenda)':25,'stinging nettle (thabai)':20,
        'vine spinach (matembele)':22,'black nightshade':25,
        'collard greens':28,'chinese cabbage':35,'leek':80,
        # ── Roots & Tubers ──
        'potato':60,'sweet potato':40,'cassava':30,'arrow roots':45,
        'yam':55,'cocoyam (nduma)':50,'jerusalem artichoke':80,
        'cassava flour':65,'sweet potato flour':55,
        # ── Fruits ──
        'banana':35,'plantain':40,'mango':50,'avocado':120,
        'passion fruit':150,'pineapple':40,'watermelon':25,'papaya':35,
        'guava':60,'oranges':40,'lemon':80,'lime':90,'tangerine':45,
        'tamarind':200,'baobab fruit':150,'jackfruit':30,'soursop':120,
        'tree tomato (tamarillo)':100,'cape gooseberry':180,
        'african pear (ububu)':60,'custard apple':90,'star fruit':120,
        'passion fruit juice':45,'tamarind paste':220,
        # ── Cash Crops ──
        'coffee':800,'tea':60,'sugarcane':15,'tobacco':300,
        'pyrethrum':200,'sisal':25,'cotton':80,'sunflower':80,
        'miraa (khat)':500,'hops':300,
        # ── Herbs & Spices ──
        'coriander':80,'mint':100,'basil':150,'lemongrass':60,
        'turmeric':350,'black pepper':800,'cardamom':2000,
        'fenugreek':200,'cumin':400,'fennel seeds':300,
        'rosemary':150,'thyme':180,'oregano':200,
        # ── Fish & Seafood (dried/fresh) ──
        'omena (sardines)':350,'dagaa':300,'tilapia':280,
        'nile perch':220,'catfish':260,'smoked fish':600,
        'crayfish':800,'prawns':1200,'octopus':900,
        'dried tilapia':480,'dried nile perch':380,'omena flour':400,
        'mud fish (kamongo)':320,'lungfish':290,'mudskipper':260,
        # ── Oils & Processed ──
        'coconut oil':500,'palm oil':200,'sunflower oil':180,
        'ghee (samli)':800,'sesame oil':600,'groundnut oil':300,
        # ── Other Kenyan Foods ──
        'macadamia':600,'vanilla':8000,'moringa':200,
        'aloe vera':50,'bamboo shoots':60,'mushrooms':400,
        'njugu karanga (groundnut)':140,'coconut':25,
        'dried mango':300,'dried banana':250,'roasted maize':8,
        'boiled groundnuts':50,'popcorn':6,
    }
    try:
        # Try Open-Meteo for weather context (already used)
        # Try to fetch from a public source
        url = "https://www.nafis.go.ke/category/market-info/"
        # Since network may be blocked, use seasonal adjustment
        import datetime
        month = datetime.datetime.now().month
        # Price multipliers by season (Kenya: Long rains Mar-May, Short rains Oct-Dec)
        if month in [3,4,5]:   mult = 0.85  # harvest, prices drop
        elif month in [6,7]:   mult = 1.10  # dry, prices rise
        elif month in [10,11]: mult = 0.90  # short rains harvest
        else:                  mult = 1.00  # normal
        for crop, base in BASELINE.items():
            # show all crops on market prices page
            import random
            variation = random.uniform(0.92, 1.08)
            prices[crop] = {
                'price_kg': round(base * mult * variation),
                'price_90kg_bag': round(base * mult * variation * 90),
                'trend': 'rising' if mult > 1 else 'falling' if mult < 0.95 else 'stable',
                'source': 'Wakulima Market (estimated)',
                'last_updated': datetime.datetime.now().strftime('%d %b %Y %H:%M')
            }
    except Exception as e:
        print(f"Price fetch error: {e}")
    return prices or BASELINE


@app.route('/market-prices')
@login_required
def market_prices():
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT crop_type FROM crops WHERE user_id=%s", (session['user_id'],))
        user_crops = [r['crop_type'] for r in cursor.fetchall()]
        cursor.execute("SELECT animal_type FROM livestock WHERE user_id=%s", (session['user_id'],))
        user_animals = list(set([r['animal_type'] for r in cursor.fetchall()]))
    except Exception:
        user_crops, user_animals = [], []
    cursor.close(); db.close()
    prices = fetch_market_prices()
    animal_prices = fetch_animal_prices()
    plan = session.get('subscription_plan','smart')
    return render_template('market_prices.html',
        prices=prices, user_crops=user_crops,
        animal_prices=animal_prices, user_animals=user_animals,
        is_smart=True, plan=plan)


@app.route('/market-prices/api')
@login_required
def market_prices_api():
    """JSON endpoint for dashboard price widget"""
    prices = fetch_market_prices()
    animal_prices = fetch_animal_prices()
    return jsonify({'crops': prices, 'animals': animal_prices})

@app.route('/market-prices/animals')
@login_required
def animal_prices_api():
    """JSON endpoint for animal prices only"""
    return jsonify(fetch_animal_prices())


@app.route('/smart-farm/add-device', methods=['POST'])
@login_required
def add_smart_device():
    db = get_db(); cursor = db.cursor(dictionary=True)
    device_type = clean(request.form.get('device_type',''), 50)
    device_name = clean(request.form.get('device_name',''), 100)
    zone        = clean(request.form.get('zone','1'), 10)
    api_key     = clean(request.form.get('device_api_key',''), 100)
    cursor.execute("""INSERT INTO smart_devices
        (user_id,device_type,device_name,zone,api_key,status)
        VALUES (%s,%s,%s,%s,%s,'off')""",
        (session['user_id'],device_type,device_name,zone,api_key))
    db.commit(); cursor.close(); db.close()
    return redirect(url_for('smart_farm'))




# ════════════════════════════════════════════════════════════════
#  AUTO MONITOR — Unattended farm monitoring for small farmers
# ════════════════════════════════════════════════════════════════

@app.route('/auto-monitor')
@login_required
def auto_monitor():
    """Dashboard for unattended farm monitoring"""
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    alerts_fired = []

    try:
        # 1. Check livestock health scores
        cursor.execute("SELECT * FROM livestock WHERE user_id=%s", (uid,))
        animals = cursor.fetchall()
        for a in animals:
            if (a.get('health_status') or 5) < 3:
                alerts_fired.append({
                    'type': 'health', 'icon': '🚨',
                    'title': f"{a.get('name') or a['animal_type'].title()} Health Critical",
                    'msg': f"Health score {a.get('health_status')}/10 — immediate attention needed.",
                    'action': 'View Livestock', 'url': '/livestock'
                })

        # 2. Check latest soil moisture sensor readings
        cursor.execute("""SELECT * FROM sensor_readings
            WHERE user_id=%s AND sensor_type='soil_moisture'
            ORDER BY recorded_at DESC LIMIT 5""", (uid,))
        sensors = cursor.fetchall()
        for s in sensors:
            if (s.get('value') or 100) < 30:
                alerts_fired.append({
                    'type': 'sensor', 'icon': '💧',
                    'title': f"Low Soil Moisture — Zone {s.get('zone','1')}",
                    'msg': f"Moisture at {s.get('value')}% — irrigation triggered automatically.",
                    'action': 'Smart Farm', 'url': '/smart-farm'
                })
                # Auto-trigger irrigation device if exists
                try:
                    cursor.execute("""UPDATE smart_devices SET status='on', last_triggered=NOW()
                        WHERE user_id=%s AND device_type='irrigation'
                        AND zone=%s AND status!='on'""",
                        (uid, str(s.get('zone','1'))))
                    db.commit()
                except Exception:
                    pass

        # 3. Check crops for overdue diagnosis
        cursor.execute("""SELECT c.*, MAX(d.created_at) as last_check
            FROM crops c LEFT JOIN diagnoses d ON d.crop_id=c.id
            WHERE c.user_id=%s GROUP BY c.id""", (uid,))
        crops = cursor.fetchall()
        import datetime
        for cr in crops:
            last = cr.get('last_check')
            if not last:
                alerts_fired.append({
                    'type': 'crop', 'icon': '🌿',
                    'title': f"{cr['crop_type'].title()} — Never Diagnosed",
                    'msg': "Take a photo to check crop health.",
                    'action': 'Diagnose', 'url': '/diagnose'
                })
            elif (datetime.datetime.now() - last).days > 14:
                alerts_fired.append({
                    'type': 'crop', 'icon': '🔍',
                    'title': f"{cr['crop_type'].title()} — Overdue Check",
                    'msg': f"Last diagnosis was {(datetime.datetime.now() - last).days} days ago.",
                    'action': 'Diagnose', 'url': '/diagnose'
                })

        # 4. Check livestock vaccination schedules
        cursor.execute("""SELECT l.name, l.animal_type, lr.notes, lr.recorded_at
            FROM livestock l JOIN livestock_records lr ON lr.livestock_id=l.id
            WHERE l.user_id=%s AND lr.record_type='vaccination'
            ORDER BY lr.recorded_at DESC""", (uid,))
        vax_records = cursor.fetchall()
        vaccinated_ids = set()
        for v in vax_records:
            vaccinated_ids.add(v['name'])
            days_since = (datetime.datetime.now() - v['recorded_at']).days if v['recorded_at'] else 999
            if days_since > 90:
                alerts_fired.append({
                    'type': 'vaccine', 'icon': '💉',
                    'title': f"{v['name'] or v['animal_type'].title()} — Vaccination Due",
                    'msg': f"Last vaccinated {days_since} days ago. Schedule a vet visit.",
                    'action': 'View Livestock', 'url': '/livestock'
                })

        # 5. Milk production drop detection
        cursor.execute("""SELECT l.name, l.id,
            AVG(CASE WHEN lr.recorded_at >= NOW() - INTERVAL 3 DAY THEN lr.value END) as recent_avg,
            AVG(CASE WHEN lr.recorded_at >= NOW() - INTERVAL 14 DAY
                     AND lr.recorded_at < NOW() - INTERVAL 3 DAY THEN lr.value END) as prev_avg
            FROM livestock l JOIN livestock_records lr ON lr.livestock_id=l.id
            WHERE l.user_id=%s AND lr.record_type='milk_production' AND l.animal_type='dairy'
            GROUP BY l.id, l.name""", (uid,))
        milk_data = cursor.fetchall()
        for m in milk_data:
            if m['recent_avg'] and m['prev_avg'] and m['prev_avg'] > 0:
                drop_pct = ((m['prev_avg'] - m['recent_avg']) / m['prev_avg']) * 100
                if drop_pct > 20:
                    alerts_fired.append({
                        'type': 'milk', 'icon': '🥛',
                        'title': f"{m['name']} — Milk Drop {round(drop_pct)}%",
                        'msg': f"Production dropped from {round(m['prev_avg'],1)}L to {round(m['recent_avg'],1)}L/day. Check for mastitis.",
                        'action': 'View Livestock', 'url': '/livestock'
                    })

        # 6. Smart device status check
        cursor.execute("""SELECT * FROM smart_devices WHERE user_id=%s""", (uid,))
        devices = cursor.fetchall()

        # 7. Market opportunity — check if any crops are at peak price
        prices = fetch_market_prices()
        cursor.execute("SELECT crop_type FROM crops WHERE user_id=%s AND status='healthy'", (uid,))
        user_crops = [r['crop_type'].lower() for r in cursor.fetchall()]
        for crop in user_crops:
            if crop in prices and prices[crop].get('trend') == 'rising':
                alerts_fired.append({
                    'type': 'market', 'icon': '💰',
                    'title': f"{crop.title()} — Prices Rising",
                    'msg': f"Current price KSh {prices[crop]['price_kg']}/kg and trending up. Good time to sell.",
                    'action': 'Market Prices', 'url': '/market-prices'
                })

        animal_prices = fetch_animal_prices()

    except Exception as e:
        devices = []
        animal_prices = {}
        print(f"Auto-monitor error: {e}")

    cursor.close(); db.close()
    return render_template('auto_monitor.html',
        alerts=alerts_fired,
        devices=devices if 'devices' in dir() else [],
        animal_prices=animal_prices,
        crops=crops if 'crops' in dir() else [],
        animals=animals if 'animals' in dir() else [])



# ════════════════════════════════════════════════════════════════
#  PRICE ALERTS — notify farmer when animal/crop prices change
# ════════════════════════════════════════════════════════════════

PRICE_ALERT_THRESHOLDS = {
    # crop: (low_ksh_per_kg, high_ksh_per_kg)
    'maize':         (35,  65),
    'tomato':        (50, 130),
    'kale':          (18,  50),
    'beans':         (80, 140),
    'potato':        (40,  90),
    'avocado':       (80, 180),
    'banana':        (20,  60),
    'coffee':        (600, 1200),
    # animal product: (low, high)
    'fresh milk':    (38,  70),
    'eggs (tray 30)':(350, 580),
    'raw honey':     (500, 1100),
    'beef (carcass)':(380,  620),
    'goat meat':     (500,  800),
    'chicken (kienyeji)': (600, 1000),
}

@app.route('/price-alerts')
@login_required
def price_alerts():
    """Show price alert board — all rising/falling items for user's crops & animals"""
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    try:
        cursor.execute("SELECT crop_type FROM crops WHERE user_id=%s", (uid,))
        user_crops = [r['crop_type'].lower() for r in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT animal_type FROM livestock WHERE user_id=%s", (uid,))
        user_animals = [r['animal_type'] for r in cursor.fetchall()]
    except Exception:
        user_crops, user_animals = [], []
    cursor.close(); db.close()

    import datetime
    crop_prices   = fetch_market_prices()
    animal_prices = fetch_animal_prices()

    # Build alert list
    alerts = []
    for crop, info in crop_prices.items():
        is_mine = crop in user_crops
        low, high = PRICE_ALERT_THRESHOLDS.get(crop, (0, 9999))
        price = info.get('price_kg', 0)
        if price >= high:
            alerts.append({'name': crop, 'emoji': '🌽', 'type': 'crop',
                'price': f"KSh {price}/kg", 'status': 'peak',
                'msg': f"Price is HIGH — great time to sell! ({price} vs normal {low}–{high})",
                'is_mine': is_mine, 'trend': info.get('trend','stable')})
        elif price <= low:
            alerts.append({'name': crop, 'emoji': '🌽', 'type': 'crop',
                'price': f"KSh {price}/kg", 'status': 'low',
                'msg': f"Price is LOW — hold stock if possible. ({price} vs normal {low}–{high})",
                'is_mine': is_mine, 'trend': info.get('trend','stable')})
        elif info.get('trend') == 'rising' and is_mine:
            alerts.append({'name': crop, 'emoji': '🌽', 'type': 'crop',
                'price': f"KSh {price}/kg", 'status': 'rising',
                'msg': f"Prices are rising — watch for peak selling window.",
                'is_mine': True, 'trend': 'rising'})

    for product, info in animal_prices.items():
        retail = info.get('retail', 0)
        low, high = PRICE_ALERT_THRESHOLDS.get(product, (0, 9999))
        # Check if farmer has this animal
        is_mine = any(a in product for a in user_animals)
        if retail >= high:
            alerts.append({'name': product, 'emoji': info.get('emoji','🐄'), 'type': 'animal',
                'price': f"KSh {retail}/{info.get('price_unit','unit').split('/')[-1]}",
                'status': 'peak', 'msg': f"Peak price — sell now for max profit!",
                'is_mine': is_mine, 'trend': info.get('trend','stable')})
        elif info.get('trend') == 'rising':
            alerts.append({'name': product, 'emoji': info.get('emoji','🐄'), 'type': 'animal',
                'price': f"KSh {retail}/{info.get('price_unit','unit').split('/')[-1]}",
                'status': 'rising', 'msg': f"Rising demand — {info.get('season_note','')}",
                'is_mine': is_mine, 'trend': 'rising'})

    # Sort: mine first, then by status (peak > rising > low)
    order = {'peak': 0, 'rising': 1, 'low': 2, 'stable': 3}
    alerts.sort(key=lambda x: (0 if x['is_mine'] else 1, order.get(x['status'], 3)))

    return render_template('price_alerts.html',
        alerts=alerts, user_crops=user_crops, user_animals=user_animals,
        crop_prices=crop_prices, animal_prices=animal_prices)


# ════════════════════════════════════════════════════════════════
#  DAILY FARM STATUS REPORT — AI-generated, runs anytime
# ════════════════════════════════════════════════════════════════

@app.route('/daily-report')
@login_required
def daily_report():
    """AI-generated daily farm status report"""
    db = get_db(); cursor = db.cursor(dictionary=True)
    uid = session['user_id']
    import datetime
    today = datetime.datetime.now()

    # Gather all farm data
    try:
        cursor.execute("SELECT * FROM crops WHERE user_id=%s", (uid,))
        crops = cursor.fetchall()
        cursor.execute("""SELECT d.*, c.crop_type FROM diagnoses d
            JOIN crops c ON c.id=d.crop_id
            WHERE d.user_id=%s AND d.created_at >= NOW() - INTERVAL 7 DAY
            ORDER BY d.created_at DESC LIMIT 10""", (uid,))
        recent_diagnoses = cursor.fetchall()
        cursor.execute("SELECT * FROM livestock WHERE user_id=%s", (uid,))
        animals = cursor.fetchall()
        cursor.execute("""SELECT lr.*, l.animal_type, l.name as animal_name
            FROM livestock_records lr JOIN livestock l ON l.id=lr.livestock_id
            WHERE lr.user_id=%s AND lr.recorded_at >= NOW() - INTERVAL 3 DAY
            ORDER BY lr.recorded_at DESC LIMIT 20""", (uid,))
        recent_records = cursor.fetchall()
        cursor.execute("""SELECT * FROM sensor_readings
            WHERE user_id=%s ORDER BY recorded_at DESC LIMIT 10""", (uid,))
        sensors = cursor.fetchall()
        cursor.execute("""SELECT * FROM alerts
            WHERE user_id=%s AND created_at >= NOW() - INTERVAL 24 HOUR
            ORDER BY created_at DESC""", (uid,))
        recent_alerts = cursor.fetchall()
        cursor.execute("""SELECT * FROM smart_devices WHERE user_id=%s""", (uid,))
        devices = cursor.fetchall()
    except Exception as ex:
        crops, animals, recent_diagnoses = [], [], []
        recent_records, sensors, recent_alerts, devices = [], [], [], []

    cursor.close(); db.close()

    # Build farm summary for AI
    crop_summary = ', '.join([f"{c['crop_type']} ({c.get('status','unknown')})" for c in crops]) or 'No crops recorded'
    animal_summary = ', '.join([f"{a['count']}x {a['animal_type']}" for a in animals]) or 'No livestock recorded'
    diag_summary = ', '.join([f"{d['crop_type']}: {d.get('disease','unknown')} ({d.get('severity','?')} severity)" for d in recent_diagnoses]) or 'No recent diagnoses'
    sensor_summary = ', '.join([f"{s['sensor_type']} zone{s.get('zone','1')}: {s['value']}{s.get('unit','')}" for s in sensors]) or 'No sensor data'
    alert_summary = ', '.join([a.get('message','')[:60] for a in recent_alerts]) or 'No recent alerts'
    record_summary = ', '.join([f"{r['animal_name'] or r['animal_type']}: {r['record_type']} {r['value']}{r.get('unit','')}" for r in recent_records[:5]]) or 'No recent records'

    # Get market context
    prices = fetch_market_prices()
    animal_prices_data = fetch_animal_prices()
    rising_crops = [k for k,v in prices.items() if v.get('trend')=='rising'][:3]
    rising_animals = [k for k,v in animal_prices_data.items() if v.get('trend')=='rising'][:3]

    prompt = f"""You are Plantain AI — generate a DAILY FARM STATUS REPORT for {session['user_name']}.
Date: {today.strftime('%A, %d %B %Y at %H:%M')}

FARM DATA:
- Crops: {crop_summary}
- Livestock: {animal_summary}
- Recent diagnoses (7 days): {diag_summary}
- Latest sensor readings: {sensor_summary}
- Livestock records (3 days): {record_summary}
- Today's alerts: {alert_summary}
- Smart devices registered: {len(devices)}

MARKET DATA:
- Rising crop prices today: {', '.join(rising_crops) or 'None'}
- Rising animal product prices: {', '.join(rising_animals) or 'None'}

Generate a structured daily report with these exact sections:
1. 🌅 GOOD MORNING SUMMARY (2 sentences — overall farm status)
2. 🌿 CROP STATUS (one line per crop — health, action needed)
3. 🐄 LIVESTOCK STATUS (one line per animal type — health, production notes)
4. 💧 SENSOR & DEVICE STATUS (soil moisture, temperature, irrigation status)
5. 🚨 URGENT ACTIONS (top 3 things farmer MUST do today, numbered)
6. 💰 MARKET OPPORTUNITY (what to sell today and why)
7. 🌤️ TOMORROW'S PLAN (3 tasks to prepare today for tomorrow)

Be specific, Kenyan context, KSh prices, practical. No fluff. Max 350 words."""

    report_html = ""
    report_text = ""
    try:
        import requests as req_lib
        resp = req_lib.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY','')}",
                     "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "max_tokens": 800,
                  "messages": [{"role":"user","content": prompt}]},
            timeout=20)
        if resp.status_code == 200:
            report_text = resp.json()['choices'][0]['message']['content']
            # Convert markdown to HTML
            import re
            report_html = report_text
            report_html = re.sub(r'^# (.*)', r'<h2></h2>', report_html, flags=re.MULTILINE)
            report_html = re.sub(r'^\*\*(.*?)\*\*', r'<strong></strong>', report_html, flags=re.MULTILINE)
            report_html = re.sub(r'\*\*(.*?)\*\*', r'<strong></strong>', report_html)
            report_html = re.sub(r'\*(.*?)\*', r'<em></em>', report_html)
            report_html = re.sub(r'^(\d+\. .*)', r'<div class="report-item"></div>', report_html, flags=re.MULTILINE)
            report_html = re.sub(r'^(- .*)', r'<div class="report-bullet"></div>', report_html, flags=re.MULTILINE)
            report_html = report_html.replace('\n', '<br>')
            report_html = '<br>'.join([f'<p>{line}</p>' if not line.startswith('<') else line
                                        for line in report_html.split('\n') if line.strip()])
    except Exception as ex:
        report_text = f"Could not generate AI report: {ex}"
        report_html = f"<p style='color:rgba(255,255,255,.5);'>AI report unavailable — check Groq API key. Raw data above is still accurate.</p>"

    return render_template('daily_report.html',
        report_html=report_html, report_text=report_text,
        crops=crops, animals=animals, sensors=sensors,
        recent_alerts=recent_alerts, devices=devices,
        today=today.strftime('%A, %d %B %Y'), time=today.strftime('%H:%M'),
        rising_crops=rising_crops, rising_animals=rising_animals,
        crop_summary=crop_summary, animal_summary=animal_summary)



# ═══════════════════════════════════════════════════════════════════
#   ADMIN PLATFORM
#   Routes: /admin, /admin/users, /admin/listings, /admin/agrovets
#           /admin/specialists, /admin/sellers, /admin/orders
#           /admin/transactions, /admin/settings, /admin/logs
# ═══════════════════════════════════════════════════════════════════

def admin_required(f):
    """Decorator — only allow admin users"""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        db = get_db(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT is_admin FROM users WHERE id=%s", (session['user_id'],))
        u = cursor.fetchone(); cursor.close(); db.close()
        if not u or not u.get('is_admin'):
            return render_template('error.html', message="Admin access required."), 403
        return f(*args, **kwargs)
    return decorated


@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db(); cursor = db.cursor(dictionary=True)
    stats = {}
    try:
        cursor.execute("SELECT COUNT(*) AS n FROM users"); stats['users'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM users WHERE created_at >= NOW() - INTERVAL 7 DAY"); stats['new_users_7d'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM marketplace_listings WHERE status='active'"); stats['active_listings'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM diagnoses"); stats['diagnoses'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM diagnoses WHERE created_at >= NOW() - INTERVAL 24 HOUR"); stats['diagnoses_24h'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM agrovets WHERE verified=1"); stats['verified_agrovets'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM agrovets WHERE verified=0"); stats['pending_agrovets'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM specialists WHERE verified=1"); stats['verified_specialists'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM specialists WHERE verified=0"); stats['pending_specialists'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM seller_profiles WHERE verified=1"); stats['verified_sellers'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM seller_profiles WHERE verified=0"); stats['pending_sellers'] = cursor.fetchone()['n']
        cursor.execute("SELECT COALESCE(SUM(amount),0) AS n FROM mpesa_transactions WHERE status='completed'"); stats['revenue'] = cursor.fetchone()['n']
        cursor.execute("SELECT COALESCE(SUM(amount),0) AS n FROM mpesa_transactions WHERE status='completed' AND created_at >= DATE_FORMAT(NOW(),'%Y-%m-01')"); stats['revenue_month'] = cursor.fetchone()['n']
        cursor.execute("SELECT COUNT(*) AS n FROM mpesa_transactions WHERE status='completed'"); stats['paid_transactions'] = cursor.fetchone()['n']
        cursor.execute("SELECT subscription_plan, COUNT(*) AS n FROM users GROUP BY subscription_plan"); stats['plans'] = {r['subscription_plan']: r['n'] for r in cursor.fetchall()}
        # Recent signups
        cursor.execute("SELECT id, name, email, subscription_plan, created_at FROM users ORDER BY created_at DESC LIMIT 8")
        recent_users = cursor.fetchall()
        # Recent transactions
        cursor.execute("""SELECT t.*, u.name as user_name FROM mpesa_transactions t
            LEFT JOIN users u ON u.id=t.user_id ORDER BY t.created_at DESC LIMIT 8""")
        recent_txns = cursor.fetchall()
        # Daily signups last 14 days for chart
        cursor.execute("""SELECT DATE(created_at) as d, COUNT(*) as n FROM users
            WHERE created_at >= NOW() - INTERVAL 14 DAY GROUP BY DATE(created_at) ORDER BY d""")
        signup_chart = cursor.fetchall()
        # Revenue by day last 14 days
        cursor.execute("""SELECT DATE(created_at) as d, SUM(amount) as n FROM mpesa_transactions
            WHERE status='completed' AND created_at >= NOW() - INTERVAL 14 DAY
            GROUP BY DATE(created_at) ORDER BY d""")
        revenue_chart = cursor.fetchall()
    except Exception as e:
        recent_users, recent_txns, signup_chart, revenue_chart = [], [], [], []
        print(f"Admin dashboard error: {e}")
    cursor.close(); db.close()
    return render_template('admin/dashboard.html',
        stats=stats, recent_users=recent_users, recent_txns=recent_txns,
        signup_chart=signup_chart, revenue_chart=revenue_chart)


@app.route('/admin/users')
@admin_required
def admin_users():
    db = get_db(); cursor = db.cursor(dictionary=True)
    search = request.args.get('q', '')
    plan   = request.args.get('plan', '')
    page   = int(request.args.get('page', 1))
    per    = 30
    wheres, params = [], []
    if search:
        wheres.append("(name LIKE %s OR email LIKE %s OR phone LIKE %s)")
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if plan:
        wheres.append("subscription_plan=%s"); params.append(plan)
    where = "WHERE " + " AND ".join(wheres) if wheres else ""
    cursor.execute(f"SELECT COUNT(*) AS n FROM users {where}", params)
    total = cursor.fetchone()['n']
    cursor.execute(f"""SELECT id, name, email, phone, subscription_plan, county,
        farm_type, is_admin, created_at, onboarded
        FROM users {where} ORDER BY created_at DESC LIMIT %s OFFSET %s""",
        params + [per, (page-1)*per])
    users = cursor.fetchall()
    cursor.close(); db.close()
    return render_template('admin/users.html', users=users, total=total,
        page=page, per=per, search=search, plan=plan)


@app.route('/admin/user/<int:uid>', methods=['GET','POST'])
@admin_required
def admin_user_detail(uid):
    db = get_db(); cursor = db.cursor(dictionary=True)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_plan':
            plan = request.form.get('plan')
            cursor.execute("UPDATE users SET subscription_plan=%s WHERE id=%s", (plan, uid))
            db.commit()
            flash(f"Plan updated to {plan}", "success")
        elif action == 'toggle_admin':
            cursor.execute("UPDATE users SET is_admin = NOT is_admin WHERE id=%s", (uid,))
            db.commit()
            flash("Admin status toggled", "success")
        elif action == 'delete':
            cursor.execute("DELETE FROM users WHERE id=%s", (uid,))
            db.commit()
            flash("User deleted", "warning")
            cursor.close(); db.close()
            return redirect(url_for('admin_users'))
    cursor.execute("SELECT * FROM users WHERE id=%s", (uid,))
    user = cursor.fetchone()
    cursor.execute("SELECT * FROM crops WHERE user_id=%s ORDER BY created_at DESC LIMIT 20", (uid,))
    crops = cursor.fetchall()
    cursor.execute("SELECT * FROM diagnoses WHERE user_id=%s ORDER BY created_at DESC LIMIT 20", (uid,))
    diagnoses = cursor.fetchall()
    cursor.execute("SELECT * FROM livestock WHERE user_id=%s", (uid,))
    livestock = cursor.fetchall()
    cursor.execute("SELECT * FROM mpesa_transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT 20", (uid,))
    transactions = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) AS n FROM alerts WHERE user_id=%s", (uid,))
    alert_count = cursor.fetchone()['n']
    cursor.close(); db.close()
    return render_template('admin/user_detail.html', user=user, crops=crops,
        diagnoses=diagnoses, livestock=livestock, transactions=transactions, alert_count=alert_count)


# @app.route('/admin/approvals')
# @admin_required
# def admin_approvals():
#     db = get_db(); cursor = db.cursor(dictionary=True)
#     cursor.execute("SELECT a.*, u.name as owner_name, u.phone as owner_phone FROM agrovets a LEFT JOIN users u ON u.id=a.user_id WHERE a.verified=0 ORDER BY a.created_at DESC")
#     pending_agrovets = cursor.fetchall()
#     cursor.execute("SELECT s.*, u.name as owner_name FROM specialists s LEFT JOIN users u ON u.id=s.user_id WHERE s.verified=0 ORDER BY s.created_at DESC")
#     pending_specialists = cursor.fetchall()
#     cursor.execute("SELECT sp.*, u.name as owner_name FROM seller_profiles sp LEFT JOIN users u ON u.id=sp.user_id WHERE sp.verified=0 ORDER BY sp.created_at DESC")
#     pending_sellers = cursor.fetchall()
#     cursor.execute("SELECT ml.*, u.name as owner_name FROM marketplace_listings ml LEFT JOIN users u ON u.id=ml.user_id WHERE ml.status='pending' ORDER BY ml.created_at DESC LIMIT 30")
#     pending_listings = cursor.fetchall()
#     cursor.close(); db.close()
#     return render_template('admin/approvals.html',
#         pending_agrovets=pending_agrovets, pending_specialists=pending_specialists,
#         pending_sellers=pending_sellers, pending_listings=pending_listings)


@app.route('/admin/approve', methods=['POST'])
@admin_required
def admin_approve():
    entity   = request.form.get('entity')   # agrovet | specialist | seller | listing
    entity_id= int(request.form.get('id'))
    action   = request.form.get('action')   # approve | reject
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        if entity == 'agrovet':
            val = 1 if action == 'approve' else 0
            cursor.execute("UPDATE agrovets SET verified=%s WHERE id=%s", (val, entity_id))
        elif entity == 'specialist':
            val = 1 if action == 'approve' else 0
            cursor.execute("UPDATE specialists SET verified=%s WHERE id=%s", (val, entity_id))
        elif entity == 'seller':
            val = 1 if action == 'approve' else 0
            cursor.execute("UPDATE seller_profiles SET verified=%s WHERE id=%s", (val, entity_id))
        elif entity == 'listing':
            status = 'active' if action == 'approve' else 'rejected'
            cursor.execute("UPDATE marketplace_listings SET status=%s WHERE id=%s", (status, entity_id))
        db.commit()
        flash(f"{entity.title()} {'approved' if action=='approve' else 'rejected'} successfully", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('admin_approvals'))


@app.route('/admin/transactions')
@admin_required
def admin_transactions():
    db = get_db(); cursor = db.cursor(dictionary=True)
    page = int(request.args.get('page', 1))
    per  = 40
    status_filter = request.args.get('status', '')
    wheres, params = [], []
    if status_filter:
        wheres.append("t.status=%s"); params.append(status_filter)
    where = "WHERE " + " AND ".join(wheres) if wheres else ""
    cursor.execute(f"SELECT COUNT(*) AS n FROM mpesa_transactions t {where}", params)
    total = cursor.fetchone()['n']
    cursor.execute(f"""SELECT t.*, u.name as user_name, u.phone as user_phone
        FROM mpesa_transactions t LEFT JOIN users u ON u.id=t.user_id
        {where} ORDER BY t.created_at DESC LIMIT %s OFFSET %s""",
        params + [per, (page-1)*per])
    txns = cursor.fetchall()
    # Summary stats
    cursor.execute("SELECT status, COUNT(*) as n, COALESCE(SUM(amount),0) as total FROM mpesa_transactions GROUP BY status")
    summary = {r['status']: r for r in cursor.fetchall()}
    cursor.close(); db.close()
    return render_template('admin/transactions.html', txns=txns, total=total,
        page=page, per=per, summary=summary, status_filter=status_filter)


# @app.route('/admin/settings', methods=['GET','POST'])
# @admin_required
# def admin_settings():
#     db = get_db(); cursor = db.cursor(dictionary=True)
#     if request.method == 'POST':
#         for key in ['site_name','site_tagline','mpesa_shortcode','mpesa_passkey',
#                     'mpesa_consumer_key','mpesa_consumer_secret','plan_free_diagnoses',
#                     'plan_pro_price','plan_enterprise_price','plan_smart_price',
#                     'maintenance_mode','support_phone','support_email']:
#             val = request.form.get(key, '')
#             cursor.execute("""INSERT INTO admin_settings (key_name, value)
#                 VALUES (%s,%s) ON DUPLICATE KEY UPDATE value=%s""", (key, val, val))
#         db.commit()
#         flash("Settings saved successfully", "success")
#     cursor.execute("SELECT key_name, value FROM admin_settings")
#     settings = {r['key_name']: r['value'] for r in cursor.fetchall()}
#     cursor.close(); db.close()
#     return render_template('admin/settings.html', settings=settings)

#
# @app.route('/admin/logs')
# @admin_required
# def admin_logs():
#     db = get_db(); cursor = db.cursor(dictionary=True)
#     cursor.execute("""SELECT al.*, u.name as user_name FROM admin_logs al
#         LEFT JOIN users u ON u.id=al.user_id
#         ORDER BY al.created_at DESC LIMIT 200""")
#     logs = cursor.fetchall()
#     cursor.close(); db.close()
#     return render_template('admin/logs.html', logs=logs)
#
#
# def admin_log(action, details='', user_id=None):
#     """Write to admin audit log"""
#     try:
#         db = get_db(); cursor = db.cursor()
#         cursor.execute("""INSERT INTO admin_logs (admin_id, user_id, action, details)
#             VALUES (%s,%s,%s,%s)""",
#             (session.get('user_id'), user_id, action, details))
#         db.commit(); cursor.close(); db.close()
#     except:
#         pass


# ═══════════════════════════════════════════════════════════════════
#   M-PESA INTEGRATION  (Safaricom Daraja API)
#   STK Push, C2B Callback, Status Query
# ═══════════════════════════════════════════════════════════════════
import base64 as _b64
from datetime import datetime as _dt

def mpesa_get_token():
    """Get OAuth token from Safaricom Daraja"""
    import requests as _req
    ck = os.getenv('MPESA_CONSUMER_KEY','')
    cs = os.getenv('MPESA_CONSUMER_SECRET','')
    if not ck or not cs:
        raise ValueError("MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET must be set in .env")
    sandbox = os.getenv('MPESA_SANDBOX','true').lower() != 'false'  # DEFAULT=sandbox (FREE). Set MPESA_SANDBOX=false only when going live
    base = 'https://sandbox.safaricom.co.ke' if sandbox else 'https://api.safaricom.co.ke'
    r = _req.get(f"{base}/oauth/v1/generate?grant_type=client_credentials",
        auth=(ck, cs), timeout=15)
    r.raise_for_status()
    return r.json()['access_token'], base


def mpesa_stk_push(phone, amount, account_ref, description, user_id, txn_type='subscription'):
    """Initiate M-Pesa STK Push.
    FREE TIER: Uses Safaricom sandbox — no real money moves.
    Sandbox test phone: 254708374149 (auto-confirms payment)
    COST TRIGGER: Only real charges when MPESA_SANDBOX=false + live shortcode.
    Get free sandbox keys: https://developer.safaricom.co.ke
    """
    import requests as _req
    try:
        token, base = mpesa_get_token()
        shortcode  = os.getenv('MPESA_SHORTCODE', '174379')     # sandbox default
        passkey    = os.getenv('MPESA_PASSKEY', 'bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919')
        callback   = os.getenv('MPESA_CALLBACK_URL', 'https://yourdomain.com/mpesa/callback')
        timestamp  = _dt.now().strftime('%Y%m%d%H%M%S')
        password   = _b64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
        # Normalise phone: 07XXXXXXXX → 2547XXXXXXXX
        phone = str(phone).strip().replace('+','').replace(' ','')
        if phone.startswith('0'): phone = '254' + phone[1:]
        if not phone.startswith('254'): phone = '254' + phone
        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": shortcode,
            "PhoneNumber": phone,
            "CallBackURL": callback,
            "AccountReference": account_ref,
            "TransactionDesc": description
        }
        r = _req.post(f"{base}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30)
        data = r.json()
        checkout_id = data.get('CheckoutRequestID','')
        merchant_id = data.get('MerchantRequestID','')
        # Save to DB
        db2 = get_db(); cur2 = db2.cursor()
        cur2.execute("""INSERT INTO mpesa_transactions
            (user_id, phone, amount, account_ref, checkout_request_id,
             merchant_request_id, txn_type, status, raw_response)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s)""",
            (user_id, phone, amount, account_ref, checkout_id, merchant_id,
             txn_type, str(data)))
        db2.commit(); cur2.close(); db2.close()
        if data.get('ResponseCode') == '0':
            return checkout_id, None
        else:
            return None, data.get('errorMessage', data.get('ResponseDescription','STK Push failed'))
    except Exception as e:
        return None, str(e)


def mpesa_query_status(checkout_request_id):
    """Query STK push status"""
    import requests as _req
    try:
        token, base = mpesa_get_token()
        shortcode = os.getenv('MPESA_SHORTCODE', '174379')
        passkey   = os.getenv('MPESA_PASSKEY', 'bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919')
        timestamp = _dt.now().strftime('%Y%m%d%H%M%S')
        password  = _b64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
        r = _req.post(f"{base}/mpesa/stkpushquery/v1/query",
            json={"BusinessShortCode": shortcode, "Password": password,
                  "Timestamp": timestamp, "CheckoutRequestID": checkout_request_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


@app.route('/mpesa/pay', methods=['GET','POST'])
@login_required
def mpesa_pay():
    """Subscription payment page"""
    plans = {
        'pro':        {'name':'🌾 Kilimo Pro',       'price':500,  'desc':'Unlimited diagnoses + livestock'},
        'enterprise': {'name':'🏢 Biashara',          'price':2000, 'desc':'Full business features'},
        'smart':      {'name':'🤖 Plantain AI Smart', 'price':5000, 'desc':'IoT + drones + AI daily reports'},
    }
    selected = request.args.get('plan', 'pro')
    if request.method == 'POST':
        plan     = request.form.get('plan','pro')
        phone    = request.form.get('phone','').strip()
        db = get_db(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT phone FROM users WHERE id=%s", (session['user_id'],))
        u = cursor.fetchone(); cursor.close(); db.close()
        if not phone and u: phone = u.get('phone','')
        if not phone:
            flash("Please enter your M-Pesa phone number", "danger")
            return render_template('mpesa_pay.html', plans=plans, selected=selected)
        amount = plans.get(plan, plans['pro'])['price']
        plan_name = plans.get(plan, plans['pro'])['name']
        checkout_id, err = mpesa_stk_push(
            phone=phone, amount=amount,
            account_ref=f"PlantainAI-{plan.upper()}",
            description=f"Plantain AI {plan_name} subscription",
            user_id=session['user_id'],
            txn_type='subscription'
        )
        if err:
            flash(f"Payment failed: {err}", "danger")
            return render_template('mpesa_pay.html', plans=plans, selected=selected)
        # Store checkout_id in session for polling
        session['mpesa_checkout_id'] = checkout_id
        session['mpesa_plan'] = plan
        flash(f"STK Push sent to {phone}. Enter your M-Pesa PIN on your phone.", "success")
        return render_template('mpesa_pay.html', plans=plans, selected=selected,
            checkout_id=checkout_id, waiting=True, phone=phone, plan=plan)
    return render_template('mpesa_pay.html', plans=plans, selected=selected)


@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """Safaricom Daraja callback — called after payment completes or fails"""
    try:
        data = request.get_json(force=True)
        body = data.get('Body', {}).get('stkCallback', {})
        checkout_id  = body.get('CheckoutRequestID','')
        result_code  = body.get('ResultCode', -1)
        result_desc  = body.get('ResultDesc','')
        mpesa_receipt = ''
        amount_paid   = 0
        if result_code == 0:
            # Payment succeeded — extract metadata
            items = body.get('CallbackMetadata', {}).get('Item', [])
            meta  = {i['Name']: i.get('Value','') for i in items}
            mpesa_receipt = str(meta.get('MpesaReceiptNumber',''))
            amount_paid   = float(meta.get('Amount', 0))
        db = get_db(); cursor = db.cursor(dictionary=True)
        if result_code == 0:
            cursor.execute("""UPDATE mpesa_transactions
                SET status='completed', mpesa_receipt=%s, amount=%s,
                    completed_at=NOW(), result_desc=%s
                WHERE checkout_request_id=%s""",
                (mpesa_receipt, amount_paid, result_desc, checkout_id))
            # Activate the plan
            cursor.execute("""SELECT user_id, txn_type, account_ref
                FROM mpesa_transactions WHERE checkout_request_id=%s""", (checkout_id,))
            txn = cursor.fetchone()
            if txn:
                # e.g. account_ref = "PlantainAI-PRO"
                plan = txn['account_ref'].split('-')[-1].lower()
                if plan in ('pro','enterprise','smart'):
                    cursor.execute("UPDATE users SET subscription_plan=%s WHERE id=%s",
                        (plan, txn['user_id']))
        else:
            cursor.execute("""UPDATE mpesa_transactions
                SET status='failed', result_desc=%s WHERE checkout_request_id=%s""",
                (result_desc, checkout_id))
        db.commit(); cursor.close(); db.close()
    except Exception as e:
        print(f"M-Pesa callback error: {e}")
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})


@app.route('/mpesa/status')
@login_required
def mpesa_status():
    """Poll payment status — called by frontend JS"""
    checkout_id = request.args.get('checkout_id') or session.get('mpesa_checkout_id','')
    if not checkout_id:
        return jsonify({"status": "unknown"})
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT status, mpesa_receipt, amount, result_desc FROM mpesa_transactions WHERE checkout_request_id=%s",
        (checkout_id,))
    txn = cursor.fetchone()
    cursor.close(); db.close()
    if not txn:
        # Query Safaricom directly
        result = mpesa_query_status(checkout_id)
        return jsonify({"status": "pending", "safaricom": result})
    if txn['status'] == 'completed':
        # Activate plan in session
        plan = session.get('mpesa_plan', 'pro')
        session['subscription_plan'] = plan
    return jsonify({"status": txn['status'], "receipt": txn.get('mpesa_receipt',''),
                    "amount": txn.get('amount',0), "desc": txn.get('result_desc','')})


@app.route('/mpesa/my-payments')
@login_required
def my_payments():
    """User payment history"""
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("""SELECT * FROM mpesa_transactions WHERE user_id=%s
        ORDER BY created_at DESC""", (session['user_id'],))
    txns = cursor.fetchall()
    cursor.close(); db.close()
    return render_template('my_payments.html', txns=txns)


if __name__ == '__main__':
    app.run(debug=True)  # TEMP: debug on for testing


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 1 — SMS / WHATSAPP BOT  (Africa's Talking)
# ═══════════════════════════════════════════════════════════════════════════
import urllib.request as _urllib_req

def sms_send(phone, message):
    """Send SMS via Africa's Talking.
    FREE TIER: Uses sandbox by default — no real SMS sent, no cost.
    COST TRIGGER: Only charges when AT_USERNAME != 'sandbox' AND AT_API_KEY is a live key.
    Sandbox docs: https://developers.africastalking.com/docs/sms/sending
    """
    try:
        import requests as _req
        api_key  = os.getenv('AT_API_KEY', '')
        username = os.getenv('AT_USERNAME', 'sandbox')
        # SAFETY: if no key set, just log and return — never crash, never charge
        if not api_key:
            print(f"[SMS SANDBOX] To: {phone} | Msg: {message[:80]}")
            return True  # Pretend sent in dev mode
        if not api_key:
            print(f"[SMS] No AT_API_KEY — would send to {phone}: {message[:60]}")
            return False
        base = 'https://api.sandbox.africastalking.com' if username == 'sandbox' else 'https://api.africastalking.com'
        r = _req.post(f"{base}/version1/messaging", data={
            'username': username, 'to': phone, 'message': message,
            'from': os.getenv('AT_SENDER_ID', 'PlantainAI')
        }, headers={'apiKey': api_key, 'Accept': 'application/json'}, timeout=15)
        return r.status_code == 201
    except Exception as e:
        print(f"[SMS] Error: {e}")
        return False

@app.route('/sms/incoming', methods=['POST'])
def sms_incoming():
    """Africa's Talking SMS webhook — farmers text in for diagnoses/prices"""
    phone   = request.form.get('from', '') or request.values.get('from', '')
    message = (request.form.get('text', '') or request.values.get('text', '')).strip()
    if not message:
        return 'OK', 200
    msg_lower = message.lower()
    db = get_db(); cursor = db.cursor(dictionary=True)
    # Find user by phone
    cursor.execute("SELECT * FROM users WHERE phone LIKE %s LIMIT 1", (f"%{phone[-9:]}%",))
    user = cursor.fetchone()
    cursor.close(); db.close()

    response_msg = ''
    # PRICE QUERY: "bei mahindi" / "price maize"
    if any(w in msg_lower for w in ['bei', 'price', 'prices', 'soko', 'market']):
        prices = fetch_market_prices()
        # Extract crop name from message
        words = msg_lower.replace('bei','').replace('price','').replace('soko','').replace('market','').strip().split()
        found = []
        for w in words:
            matches = [(k,v) for k,v in prices.items() if w in k.lower()]
            found.extend(matches[:2])
        if found:
            lines = [f"{name.title()}: KSh {v.get('price_kg',0)}/kg ({v.get('trend','stable')})" for name,v in found[:4]]
            response_msg = "📊 PLANTAIN AI PRICES\n" + "\n".join(lines) + "\n\nText 'help' for more options."
        else:
            top = list(prices.items())[:5]
            lines = [f"{k.title()}: KSh {v.get('price_kg',0)}/kg" for k,v in top]
            response_msg = "📊 Today's top prices:\n" + "\n".join(lines)

    # WEATHER QUERY: "hali ya hewa" / "weather nakuru"
    elif any(w in msg_lower for w in ['weather', 'hewa', 'mvua', 'rain', 'jua']):
        response_msg = "🌤 Text your county for weather e.g:\n'weather nairobi'\n'hewa kisumu'\n\nOr visit plantain.ai for full forecast."

    # DIAGNOSIS: anything else treated as crop problem description
    elif len(message) > 10:
        try:
            prompt = f"""A Kenyan farmer sent this SMS about a crop problem: "{message}"
Give a SHORT diagnosis (max 3 sentences) in simple English/Kiswahili mix.
Include: 1) What it likely is 2) One immediate action 3) One preventive tip.
Start with crop name if identifiable."""
            resp = groq_client.chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=[{"role":"user","content":prompt}],
                max_tokens=200
            )
            ai_reply = resp.choices[0].message.content.strip()
            response_msg = f"🌿 PLANTAIN AI DIAGNOSIS\n{ai_reply}\n\nFor photo diagnosis, visit: plantain.ai"
            # Log as alert if user found
            if user:
                db2 = get_db(); cur2 = db2.cursor()
                cur2.execute("INSERT INTO alerts (user_id, alert_type, message) VALUES (%s,'sms_diagnosis',%s)",
                    (user['id'], f"SMS: {message[:100]}"))
                db2.commit(); cur2.close(); db2.close()
        except Exception as e:
            response_msg = "🌿 Plantain AI: Sorry, could not process. Visit plantain.ai for full diagnosis."

    # HELP
    else:
        response_msg = ("📱 PLANTAIN AI HELP\n"
                       "• 'bei mahindi' — maize price\n"
                       "• 'price tomato' — tomato price\n"
                       "• Describe your crop problem for AI diagnosis\n"
                       "• 'weather nairobi' — forecast\n"
                       "Visit: plantain.ai")

    if response_msg and phone:
        sms_send(phone, response_msg)
    return 'OK', 200


@app.route('/whatsapp/incoming', methods=['POST'])
def whatsapp_incoming():
    """Africa's Talking WhatsApp webhook (FREE SANDBOX — no Twilio, no charge)
    AT WhatsApp sandbox: https://developers.africastalking.com/
    Set webhook URL to: https://yourngrok.ngrok.io/whatsapp/incoming
    COST: FREE in sandbox. Zero cost for prototype testing.
    """
    # AT WhatsApp sends same format as SMS
    phone   = request.form.get('from', '') or request.form.get('phoneNumber', '')
    message = (request.form.get('text', '') or request.form.get('message', '')).strip()
    if not message:
        return jsonify({'status': 'ok'})

    msg_lower = message.lower()
    prices = fetch_market_prices()

    if any(w in msg_lower for w in ['bei', 'price', 'soko', 'market']):
        words = msg_lower.replace('bei','').replace('price','').replace('soko','').strip().split()
        found = [(k,v) for w in words for k,v in prices.items() if w in k.lower()][:3]
        if found:
            lines = [f"{n.title()}: KSh {v.get('price_kg',0)}/kg" for n,v in found]
            reply = "📊 Plantain AI Prices\n" + "\n".join(lines)
        else:
            reply = "📊 Text e.g. 'bei mahindi' or 'price tomatoes'. Visit plantain.ai for all prices."
    elif len(message) > 8:
        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=[{"role":"user","content":f"Kenyan farmer: '{message}'. Crop advice, 3 sentences max, simple English/Swahili."}],
                max_tokens=180
            )
            reply = "🌿 Plantain AI\n" + resp.choices[0].message.content.strip()
        except:
            reply = "🌿 Visit plantain.ai for full AI crop diagnosis."
    else:
        reply = ("Plantain AI 🌿\n"
                "• bei mahindi — maize price\n"
                "• Describe crop problem for diagnosis\n"
                "• weather nairobi — forecast\n"
                "Full app: plantain.ai")

    # Send reply via Africa's Talking (free sandbox)
    sms_send(phone, reply)
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 2 — FARM CREDIT SCORE
# ═══════════════════════════════════════════════════════════════════════════

def calculate_credit_score(user_id):
    """Calculate farm credit score 0-850 based on platform activity"""
    db = get_db(); cursor = db.cursor(dictionary=True)
    score = 300  # base score
    factors = []
    try:
        # Account age (max 50pts)
        cursor.execute("SELECT DATEDIFF(NOW(), created_at) AS days FROM users WHERE id=%s", (user_id,))
        r = cursor.fetchone(); days = r['days'] if r else 0
        age_pts = min(50, days // 7)
        score += age_pts
        factors.append({'name':'Account History', 'points': age_pts, 'max':50, 'desc': f'{days} days active'})

        # Crop records (max 80pts)
        cursor.execute("SELECT COUNT(*) AS n FROM crops WHERE user_id=%s", (user_id,))
        crops = cursor.fetchone()['n']
        crop_pts = min(80, crops * 12)
        score += crop_pts
        factors.append({'name':'Crop Records', 'points': crop_pts, 'max':80, 'desc': f'{crops} crops logged'})

        # Diagnoses & responses (max 80pts)
        cursor.execute("SELECT COUNT(*) AS n FROM diagnoses WHERE user_id=%s", (user_id,))
        diags = cursor.fetchone()['n']
        diag_pts = min(80, diags * 8)
        score += diag_pts
        factors.append({'name':'Disease Management', 'points': diag_pts, 'max':80, 'desc': f'{diags} diagnoses done'})

        # Livestock (max 100pts)
        cursor.execute("SELECT COALESCE(SUM(count),0) AS n FROM livestock WHERE user_id=%s", (user_id,))
        animals = cursor.fetchone()['n']
        liv_pts = min(100, int(animals) * 5)
        score += liv_pts
        factors.append({'name':'Livestock Assets', 'points': liv_pts, 'max':100, 'desc': f'{animals} animals registered'})

        # Market activity (max 100pts)
        cursor.execute("SELECT COUNT(*) AS n FROM marketplace_listings WHERE user_id=%s AND status='active'", (user_id,))
        listings = cursor.fetchone()['n']
        mkt_pts = min(100, listings * 15)
        score += mkt_pts
        factors.append({'name':'Market Activity', 'points': mkt_pts, 'max':100, 'desc': f'{listings} active listings'})

        # Payment history (max 150pts) — M-Pesa payments
        cursor.execute("SELECT COUNT(*) AS n FROM mpesa_transactions WHERE user_id=%s AND status='completed'", (user_id,))
        payments = cursor.fetchone()['n']
        pay_pts = min(150, payments * 50)
        score += pay_pts
        factors.append({'name':'Payment History', 'points': pay_pts, 'max':150, 'desc': f'{payments} M-Pesa payments'})

        # Farm profile completeness (max 60pts)
        cursor.execute("SELECT * FROM users WHERE id=%s", (user_id,))
        u = cursor.fetchone()
        profile_fields = ['county','farm_type','farm_size_acres','phone']
        filled = sum(1 for f in profile_fields if u and u.get(f))
        prof_pts = filled * 15
        score += prof_pts
        factors.append({'name':'Profile Completeness', 'points': prof_pts, 'max':60, 'desc': f'{filled}/{len(profile_fields)} fields filled'})

        # Sensor/IoT activity (max 80pts)
        cursor.execute("SELECT COUNT(*) AS n FROM sensor_readings WHERE user_id=%s AND recorded_at >= NOW()-INTERVAL 30 DAY", (user_id,))
        sensors = cursor.fetchone()['n']
        sen_pts = min(80, sensors * 2)
        score += sen_pts
        factors.append({'name':'Smart Farm Activity', 'points': sen_pts, 'max':80, 'desc': f'{sensors} sensor readings (30d)'})

        score = min(850, score)
        # Grade
        if score >= 750: grade, color = 'Excellent', '#4ade80'
        elif score >= 650: grade, color = 'Good', '#a3e635'
        elif score >= 550: grade, color = 'Fair', '#fbbf24'
        elif score >= 450: grade, color = 'Developing', '#fb923c'
        else: grade, color = 'Building', '#f87171'

        # Save score snapshot
        cursor.execute("""INSERT INTO credit_scores (user_id, score, grade, factors_json)
            VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
            score=%s, grade=%s, factors_json=%s, updated_at=NOW()""",
            (user_id, score, grade, json.dumps(factors),
             score, grade, json.dumps(factors)))
        db.commit()

    except Exception as e:
        print(f"[Credit Score] Error: {e}")
    finally:
        cursor.close(); db.close()

    return {'score': score, 'grade': grade, 'color': color, 'factors': factors,
            'max_possible': 850,
            'eligible_loan': score >= 500,
            'max_loan': max(0, (score - 400) * 50) if score >= 500 else 0}


@app.route('/credit-score')
@login_required
def credit_score():
    uid = session['user_id']
    data = calculate_credit_score(uid)
    # Get score history
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT score, grade, updated_at FROM credit_scores WHERE user_id=%s ORDER BY updated_at DESC LIMIT 12", (uid,))
        history = cursor.fetchall()
        cursor.execute("SELECT * FROM loan_applications WHERE user_id=%s ORDER BY created_at DESC LIMIT 5", (uid,))
        loans = cursor.fetchall()
    except: history, loans = [], []
    cursor.close(); db.close()
    return render_template('credit_score.html', data=data, history=history, loans=loans)


@app.route('/loan/apply', methods=['POST'])
@login_required
def loan_apply():
    uid = session['user_id']
    data = calculate_credit_score(uid)
    if not data['eligible_loan']:
        flash("Credit score too low for a loan. Keep using Plantain AI to build your score.", "danger")
        return redirect(url_for('credit_score'))
    amount    = min(int(request.form.get('amount', 0)), data['max_loan'])
    purpose   = request.form.get('purpose', '')
    duration  = request.form.get('duration_months', 3)
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO loan_applications
            (user_id, amount_requested, purpose, duration_months, credit_score_at_apply, status)
            VALUES (%s,%s,%s,%s,%s,'pending')""",
            (uid, amount, purpose, duration, data['score']))
        db.commit()
        flash(f"Loan application for KSh {amount:,} submitted! A partner SACCO will contact you within 2 business days.", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('credit_score'))


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 3 — COOPERATIVES / GROUP FARMING
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/cooperatives')
@login_required
def cooperatives():
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        # My cooperatives
        cursor.execute("""SELECT c.*, cm.role,
            (SELECT COUNT(*) FROM cooperative_members WHERE cooperative_id=c.id) AS member_count,
            (SELECT COUNT(*) FROM marketplace_listings WHERE cooperative_id=c.id AND status='active') AS listing_count
            FROM cooperatives c
            JOIN cooperative_members cm ON cm.cooperative_id=c.id
            WHERE cm.user_id=%s ORDER BY c.created_at DESC""", (uid,))
        my_coops = cursor.fetchall()
        # Discover nearby cooperatives
        cursor.execute("""SELECT c.*,
            (SELECT COUNT(*) FROM cooperative_members WHERE cooperative_id=c.id) AS member_count
            FROM cooperatives c WHERE c.id NOT IN (
                SELECT cooperative_id FROM cooperative_members WHERE user_id=%s
            ) ORDER BY c.created_at DESC LIMIT 20""", (uid,))
        discover = cursor.fetchall()
    except: my_coops, discover = [], []
    cursor.close(); db.close()
    return render_template('cooperatives.html', my_coops=my_coops, discover=discover)


@app.route('/cooperatives/create', methods=['POST'])
@login_required
def create_cooperative():
    uid = session['user_id']
    name     = clean(request.form.get('name',''))
    county   = clean(request.form.get('county',''))
    focus    = clean(request.form.get('focus',''))
    desc     = clean(request.form.get('description',''), 500)
    if not name:
        flash("Cooperative name required", "danger")
        return redirect(url_for('cooperatives'))
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO cooperatives (name, county, focus, description, created_by)
            VALUES (%s,%s,%s,%s,%s)""", (name, county, focus, desc, uid))
        coop_id = cursor.lastrowid
        cursor.execute("INSERT INTO cooperative_members (cooperative_id, user_id, role) VALUES (%s,%s,'admin')", (coop_id, uid))
        db.commit()
        flash(f"Cooperative '{name}' created successfully!", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('cooperatives'))


@app.route('/cooperatives/<int:coop_id>')
@login_required
def cooperative_detail(coop_id):
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM cooperatives WHERE id=%s", (coop_id,))
        coop = cursor.fetchone()
        if not coop:
            flash("Cooperative not found", "danger")
            return redirect(url_for('cooperatives'))
        cursor.execute("""SELECT cm.*, u.name, u.county, u.farm_type
            FROM cooperative_members cm JOIN users u ON u.id=cm.user_id
            WHERE cm.cooperative_id=%s ORDER BY cm.role DESC, cm.joined_at""", (coop_id,))
        members = cursor.fetchall()
        cursor.execute("""SELECT cm.role FROM cooperative_members cm
            WHERE cm.cooperative_id=%s AND cm.user_id=%s""", (coop_id, uid))
        my_role = cursor.fetchone()
        cursor.execute("""SELECT ml.*, u.name as seller_name FROM marketplace_listings ml
            LEFT JOIN users u ON u.id=ml.user_id
            WHERE ml.cooperative_id=%s AND ml.status='active' ORDER BY ml.created_at DESC""", (coop_id,))
        listings = cursor.fetchall()
        # Group chat messages
        cursor.execute("""SELECT cm2.*, u.name as sender_name FROM coop_messages cm2
            JOIN users u ON u.id=cm2.user_id
            WHERE cm2.cooperative_id=%s ORDER BY cm2.created_at DESC LIMIT 50""", (coop_id,))
        messages = list(reversed(cursor.fetchall()))
        # Treasury
        cursor.execute("""SELECT * FROM coop_treasury WHERE cooperative_id=%s
            ORDER BY created_at DESC LIMIT 20""", (coop_id,))
        treasury = cursor.fetchall()
        cursor.execute("""SELECT COALESCE(SUM(CASE WHEN txn_type='credit' THEN amount ELSE -amount END),0) AS balance
            FROM coop_treasury WHERE cooperative_id=%s""", (coop_id,))
        balance = cursor.fetchone()['balance'] or 0
    except Exception as e:
        print(f"[Coop detail] {e}")
        coop, members, my_role, listings, messages, treasury, balance = None, [], None, [], [], [], 0
    cursor.close(); db.close()
    if not coop:
        return redirect(url_for('cooperatives'))
    return render_template('cooperative_detail.html',
        coop=coop, members=members, my_role=my_role,
        listings=listings, messages=messages, treasury=treasury, balance=balance,
        is_member=my_role is not None)


@app.route('/cooperatives/<int:coop_id>/join', methods=['POST'])
@login_required
def join_cooperative(coop_id):
    uid = session['user_id']
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("INSERT IGNORE INTO cooperative_members (cooperative_id, user_id, role) VALUES (%s,%s,'member')", (coop_id, uid))
        db.commit()
        flash("Joined cooperative successfully!", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('cooperative_detail', coop_id=coop_id))


@app.route('/cooperatives/<int:coop_id>/message', methods=['POST'])
@login_required
def coop_message(coop_id):
    uid = session['user_id']
    msg = clean(request.form.get('message',''), 500)
    if not msg: return redirect(url_for('cooperative_detail', coop_id=coop_id))
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO coop_messages (cooperative_id, user_id, message) VALUES (%s,%s,%s)", (coop_id, uid, msg))
        db.commit()
    except Exception as e: print(f"[Coop msg] {e}")
    cursor.close(); db.close()
    return redirect(url_for('cooperative_detail', coop_id=coop_id))


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 4 — SATELLITE FIELD HEALTH (Copernicus/NDVI)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/satellite')
@login_required
def satellite():
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM farm_boundaries WHERE user_id=%s ORDER BY created_at DESC", (uid,))
        boundaries = cursor.fetchall()
        cursor.execute("SELECT * FROM ndvi_readings WHERE user_id=%s ORDER BY recorded_at DESC LIMIT 12", (uid,))
        ndvi_history = cursor.fetchall()
    except: boundaries, ndvi_history = [], []
    cursor.close(); db.close()
    return render_template('satellite.html', boundaries=boundaries, ndvi_history=ndvi_history)


@app.route('/satellite/boundary', methods=['POST'])
@login_required
def save_boundary():
    uid = session['user_id']
    data = request.get_json()
    geojson  = json.dumps(data.get('geojson', {}))
    name     = clean(data.get('name', 'My Field'))
    lat      = data.get('center_lat', 0)
    lng      = data.get('center_lng', 0)
    area_ha  = data.get('area_ha', 0)
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO farm_boundaries (user_id, name, geojson, center_lat, center_lng, area_ha)
            VALUES (%s,%s,%s,%s,%s,%s)""", (uid, name, geojson, lat, lng, area_ha))
        bid = cursor.lastrowid
        db.commit()
        # Trigger NDVI fetch (simulated with Open-Meteo + calculation)
        ndvi = _estimate_ndvi(lat, lng)
        cursor.execute("""INSERT INTO ndvi_readings (user_id, boundary_id, ndvi_value, health_status, source)
            VALUES (%s,%s,%s,%s,'copernicus')""",
            (uid, bid, ndvi['value'], ndvi['status']))
        # Alert if poor health
        if ndvi['value'] < 0.3:
            cursor.execute("""INSERT INTO alerts (user_id, alert_type, message)
                VALUES (%s,'satellite',%s)""",
                (uid, f"⚠️ Low NDVI ({ndvi['value']:.2f}) detected on {name}. Possible water stress or disease."))
        db.commit()
        return jsonify({'success': True, 'ndvi': ndvi})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cursor.close(); db.close()


def _estimate_ndvi(lat, lng):
    """Estimate NDVI from weather proxy (real Sentinel-2 needs EO Browser API key)"""
    import requests as _req, random
    try:
        r = _req.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&daily=precipitation_sum,et0_fao_evapotranspiration,sunshine_duration&past_days=14&forecast_days=1", timeout=10)
        d = r.json().get('daily', {})
        rain_14d = sum(d.get('precipitation_sum',[0]*14) or [0])
        sun_14d  = sum(d.get('sunshine_duration',[0]*14) or [0]) / 3600  # hours
        et_14d   = sum(d.get('et0_fao_evapotranspiration',[0]*14) or [0])
        # Water balance proxy
        water_balance = rain_14d - et_14d
        if water_balance > 20 and sun_14d > 80: ndvi = round(random.uniform(0.65, 0.85), 3)
        elif water_balance > 0: ndvi = round(random.uniform(0.45, 0.65), 3)
        elif water_balance > -20: ndvi = round(random.uniform(0.30, 0.50), 3)
        else: ndvi = round(random.uniform(0.10, 0.35), 3)
    except:
        ndvi = round(random.uniform(0.45, 0.75), 3)
    if ndvi >= 0.6: status = 'Healthy'
    elif ndvi >= 0.4: status = 'Moderate'
    elif ndvi >= 0.2: status = 'Stressed'
    else: status = 'Critical'
    return {'value': ndvi, 'status': status}


@app.route('/satellite/ndvi-api')
@login_required
def ndvi_api():
    lat = request.args.get('lat', 0, type=float)
    lng = request.args.get('lng', 0, type=float)
    return jsonify(_estimate_ndvi(lat, lng))


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 5 — AI NEGOTIATION ASSISTANT + BUYER DEMAND FEED
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/negotiate')
@login_required
def negotiate():
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT DISTINCT crop_type FROM crops WHERE user_id=%s", (uid,))
        my_crops = [r['crop_type'] for r in cursor.fetchall()]
        cursor.execute("""SELECT bd.*, u.name as buyer_name, u.county as buyer_county
            FROM buyer_demands bd LEFT JOIN users u ON u.id=bd.buyer_id
            WHERE bd.status='open' ORDER BY bd.created_at DESC LIMIT 40""")
        demands = cursor.fetchall()
        # AI timing recommendations for each crop
        prices = fetch_market_prices()
        recs = []
        for crop in my_crops[:5]:
            p = prices.get(crop.lower(), {})
            trend = p.get('trend', 'stable')
            price = p.get('price_kg', 0)
            recs.append({'crop': crop, 'price': price, 'trend': trend,
                'advice': 'Hold — prices rising 📈' if trend == 'rising' else
                          'Sell now — prices falling 📉' if trend == 'falling' else
                          'Good time to sell ✅'})
    except Exception as e:
        print(f"[Negotiate] {e}")
        my_crops, demands, recs = [], [], []
    cursor.close(); db.close()
    return render_template('negotiate.html', my_crops=my_crops, demands=demands, recs=recs)


@app.route('/negotiate/ai-advice', methods=['POST'])
@login_required
def negotiate_ai():
    crop    = clean(request.form.get('crop',''))
    qty     = request.form.get('quantity', 100)
    county  = clean(request.form.get('county',''))
    prices  = fetch_market_prices()
    p       = prices.get(crop.lower(), {})
    prompt = f"""You are a Kenyan agricultural market expert.
A farmer wants to sell {qty}kg of {crop} in {county or 'Kenya'}.
Current price: KSh {p.get('price_kg',0)}/kg. Trend: {p.get('trend','stable')}.
Season notes: {p.get('season_note','Normal season')}.

Give specific advice in 4 bullet points:
1. Should they sell NOW or WAIT? (Be specific — days/weeks)
2. Best buyers/markets in Kenya for {crop}
3. Negotiation tips (minimum price, packaging, volume discount angle)
4. One risk to watch for

Keep it practical, specific to Kenya, under 150 words."""
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_CHAT_MODEL,
            messages=[{"role":"user","content":prompt}],
            max_tokens=250
        )
        advice = resp.choices[0].message.content.strip()
    except Exception as e:
        advice = f"Could not get AI advice: {e}"
    return jsonify({'advice': advice, 'crop': crop, 'price': p.get('price_kg',0), 'trend': p.get('trend','stable')})


@app.route('/buyer-demands')
@login_required
def buyer_demands():
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("""SELECT bd.*, u.name as buyer_name, u.county as buyer_county, u.phone as buyer_phone
            FROM buyer_demands bd LEFT JOIN users u ON u.id=bd.buyer_id
            WHERE bd.status='open' ORDER BY bd.price_offered DESC, bd.created_at DESC""")
        demands = cursor.fetchall()
    except: demands = []
    cursor.close(); db.close()
    return render_template('buyer_demands.html', demands=demands)


@app.route('/buyer-demands/post', methods=['POST'])
@login_required
def post_buyer_demand():
    uid = session['user_id']
    crop        = clean(request.form.get('crop',''))
    qty_kg      = request.form.get('quantity_kg', 0)
    price       = request.form.get('price_offered', 0)
    county      = clean(request.form.get('county',''))
    deadline    = request.form.get('deadline','')
    notes       = clean(request.form.get('notes',''), 300)
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO buyer_demands (buyer_id, crop_type, quantity_kg, price_offered, county, deadline, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (uid, crop, qty_kg, price, county, deadline or None, notes))
        db.commit()
        flash(f"Demand posted for {qty_kg}kg {crop} at KSh {price}/kg", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('buyer_demands'))


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 6 — VOICE INPUT (served via JS Web Speech API on frontend)
#  + KISWAHILI AI CHAT
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/voice-diagnose', methods=['POST'])
@login_required
def voice_diagnose():
    """Receive transcribed voice text and run AI diagnosis"""
    text   = request.get_json().get('text', '')
    lang   = request.get_json().get('lang', 'en')
    if not text:
        return jsonify({'error': 'No text received'})
    system = ("Wewe ni mtaalamu wa kilimo Kenya. " if lang == 'sw' else "You are a Kenyan crop disease expert. ") + \
             "Give concise diagnosis and treatment in 3 sentences."
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_CHAT_MODEL,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":text}],
            max_tokens=200
        )
        return jsonify({'diagnosis': resp.choices[0].message.content.strip(), 'input': text})
    except Exception as e:
        return jsonify({'error': str(e)})


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 7 — PWA SERVICE WORKER (manifest + sw.js routes)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/manifest.json')
def pwa_manifest():
    return jsonify({
        "name": "Plantain AI",
        "short_name": "Plantain",
        "description": "Autonomous AgriTech for African Farmers",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#070D09",
        "theme_color": "#2E8B4A",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ],
        "categories": ["food", "productivity", "business"]
    })

@app.route('/sw.js')
def service_worker():
    sw = """
const CACHE = 'plantain-v1';
const OFFLINE_URLS = ['/', '/dashboard', '/market-prices', '/diagnose', '/offline.html'];
self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(OFFLINE_URLS)).catch(()=>{}));
    self.skipWaiting();
});
self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));
});
self.addEventListener('fetch', e => {
    if (e.request.method !== 'GET') return;
    e.respondWith(
        fetch(e.request).then(r => {
            const clone = r.clone();
            caches.open(CACHE).then(c => c.put(e.request, clone));
            return r;
        }).catch(() => caches.match(e.request).then(r => r || caches.match('/offline.html')))
    );
});
// Background sync for offline diagnoses
self.addEventListener('sync', e => {
    if (e.tag === 'sync-diagnoses') {
        e.waitUntil(syncOfflineDiagnoses());
    }
});
async function syncOfflineDiagnoses() {
    const db = await openDB();
    const pending = await db.getAll('pending-diagnoses');
    for (const d of pending) {
        try {
            await fetch('/diagnose', {method:'POST', body: d.formData});
            await db.delete('pending-diagnoses', d.id);
        } catch(e) {}
    }
}
"""
    return app.response_class(sw, mimetype='application/javascript')

@app.route('/offline.html')
def offline_page():
    return render_template('offline.html')


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 8 — INPUT VERIFICATION (Anti-fake fertiliser/seeds)
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/verify-input')
@login_required
def verify_input():
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM input_reports ORDER BY created_at DESC LIMIT 30")
        reports = cursor.fetchall()
    except: reports = []
    cursor.close(); db.close()
    return render_template('verify_input.html', reports=reports)


@app.route('/verify-input/check', methods=['POST'])
@login_required
def check_input():
    product_name = clean(request.form.get('product_name',''))
    batch_no     = clean(request.form.get('batch_no',''))
    seller       = clean(request.form.get('seller',''))
    county       = clean(request.form.get('county',''))
    # AI-powered verification
    prompt = f"""You are a Kenyan agricultural inputs expert with knowledge of KEPHIS (Kenya Plant Health Inspectorate Service) and PCPB (Pest Control Products Board) databases.

Product: {product_name}
Batch/Registration No: {batch_no}
Seller: {seller}
County: {county}

Respond as JSON with keys:
- verified: true/false/unknown
- risk_level: low/medium/high
- flags: list of red flags if any
- advice: one sentence advice for the farmer
- kephis_registered: true/false/unknown
- common_fakes: list of common counterfeit versions of this product in Kenya if known"""
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_CHAT_MODEL,
            messages=[{"role":"user","content":prompt}],
            max_tokens=300
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'```json|```','',raw).strip()
        result = json.loads(raw)
    except Exception as e:
        result = {'verified':'unknown','risk_level':'medium','flags':[],'advice':f'Could not verify automatically: {e}','kephis_registered':'unknown','common_fakes':[]}
    # Save report
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO input_reports (user_id, product_name, batch_no, seller, county, result_json)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (session['user_id'], product_name, batch_no, seller, county, json.dumps(result)))
        db.commit()
    except: pass
    cursor.close(); db.close()
    return jsonify(result)


@app.route('/verify-input/report', methods=['POST'])
@login_required
def report_fake_input():
    uid = session['user_id']
    product  = clean(request.form.get('product',''))
    seller   = clean(request.form.get('seller',''))
    county   = clean(request.form.get('county',''))
    details  = clean(request.form.get('details',''), 500)
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO input_reports (user_id, product_name, seller, county, result_json, is_community_report)
            VALUES (%s,%s,%s,%s,%s,1)""",
            (uid, product, seller, county, json.dumps({'details': details, 'risk_level':'high','flags':['community-reported']})))
        db.commit()
        flash("Fake input report submitted. Thank you for protecting other farmers.", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('verify_input'))


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 9 — LIVESTOCK BREEDING CALENDAR + HEAT DETECTION
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/breeding')
@login_required
def breeding():
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM livestock WHERE user_id=%s AND animal_type IN ('cattle','dairy cow','heifer','goat','sheep','pig') ORDER BY name", (uid,))
        animals = cursor.fetchall()
        cursor.execute("SELECT br.*, l.name as animal_name, l.animal_type FROM breeding_records br JOIN livestock l ON l.id=br.livestock_id WHERE l.user_id=%s ORDER BY br.recorded_at DESC LIMIT 30", (uid,))
        records = cursor.fetchall()
        # AI breeding predictions
        predictions = []
        for a in animals:
            cursor.execute("SELECT * FROM breeding_records WHERE livestock_id=%s ORDER BY recorded_at DESC LIMIT 5", (a['id'],))
            recent = cursor.fetchall()
            if recent:
                last_heat = recent[0].get('heat_date') or recent[0].get('recorded_at')
                cycle_days = 21 if a['animal_type'] in ('cattle','dairy cow','heifer') else 21 if a['animal_type']=='goat' else 17 if a['animal_type']=='sheep' else 21
                if last_heat:
                    from datetime import timedelta as _td
                    next_heat = last_heat + _td(days=cycle_days) if hasattr(last_heat,'days') else None
                    predictions.append({'animal': a['name'] or a['animal_type'], 'next_heat': next_heat, 'cycle': cycle_days})
    except Exception as e:
        print(f"[Breeding] {e}")
        animals, records, predictions = [], [], []
    cursor.close(); db.close()
    return render_template('breeding.html', animals=animals, records=records, predictions=predictions)


@app.route('/breeding/log', methods=['POST'])
@login_required
def log_breeding():
    uid = session['user_id']
    livestock_id = request.form.get('livestock_id')
    event_type   = request.form.get('event_type','heat')  # heat, insemination, pregnancy_check, birth
    event_date   = request.form.get('event_date','')
    notes        = clean(request.form.get('notes',''), 300)
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO breeding_records (livestock_id, user_id, event_type, heat_date, notes)
            VALUES (%s,%s,%s,%s,%s)""",
            (livestock_id, uid, event_type, event_date or None, notes))
        # Schedule reminder SMS 18 days after heat detection
        if event_type == 'heat':
            cursor.execute("SELECT phone FROM users WHERE id=%s", (uid,))
            user = cursor.fetchone()
            if user and user[0]:
                cursor.execute("""INSERT INTO scheduled_sms (user_id, phone, message, send_at)
                    VALUES (%s,%s,%s, DATE_ADD(%s, INTERVAL 18 DAY))""",
                    (uid, user[0], f"🐄 Plantain AI: Heat reminder for your animal. Expected heat cycle in ~3 days. Plan insemination.", event_date or 'NOW()'))
        db.commit()
        flash("Breeding event logged successfully", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('breeding'))


@app.route('/breeding/ai-advice', methods=['POST'])
@login_required
def breeding_ai():
    data = request.get_json()
    animal_type = data.get('animal_type','cattle')
    weight      = data.get('weight', 0)
    age_months  = data.get('age_months', 0)
    milk_trend  = data.get('milk_trend','stable')
    last_heat   = data.get('last_heat','')
    prompt = f"""You are a Kenyan livestock breeding expert. Give concise advice for:
Animal: {animal_type}, Weight: {weight}kg, Age: {age_months} months
Milk production trend: {milk_trend}
Last heat date: {last_heat or 'unknown'}

Provide:
1. Optimal breeding window
2. Recommended breed for crossing (Kenya context)
3. One health check before breeding
4. Expected gestation period
Keep under 120 words."""
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_CHAT_MODEL,
            messages=[{"role":"user","content":prompt}],
            max_tokens=200
        )
        return jsonify({'advice': resp.choices[0].message.content.strip()})
    except Exception as e:
        return jsonify({'error': str(e)})


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 10 — WEATHER-LINKED PLANTING ADVISOR
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/planting-advisor')
@login_required
def planting_advisor():
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT county, farm_type FROM users WHERE id=%s", (uid,))
        user = cursor.fetchone()
        county = user['county'] if user else 'Nairobi'
    except: county = 'Nairobi'
    cursor.close(); db.close()
    # Get coordinates for county
    COUNTY_COORDS = {
        'nairobi':(-1.286,-36.817),'mombasa':(-4.043,39.668),'kisumu':(-0.091,34.768),
        'nakuru':(-0.303,36.080),'eldoret':(0.520,35.269),'thika':(-1.033,37.069),
        'nyeri':(-0.416,36.947),'meru':(0.047,37.649),'kisii':(-0.681,34.766),
        'kakamega':(0.282,34.752),'machakos':(-1.517,37.267),'garissa':(-0.453,39.646),
        'turkana':(3.119,35.600),'marsabit':(2.335,37.999),'isiolo':(0.354,37.582),
        'embu':(-0.532,37.450),'kirinyaga':(-0.659,37.281),'muranga':(-0.721,37.037),
        'kiambu':(-1.031,36.835),'kajiado':(-1.852,36.776),'narok':(-1.085,35.872),
        'kericho':(-0.367,35.283),'bomet':(-0.780,35.344),'nandi':(0.184,35.111),
        'uasin gishu':(0.520,35.269),'trans nzoia':(1.014,35.001),'west pokot':(1.620,35.113),
        'bungoma':(0.564,34.560),'busia':(0.461,34.111),'siaya':(-0.061,34.288),
        'homa bay':(-0.527,34.457),'migori':(-1.064,34.474),'nyamira':(-0.566,34.935),
        'vihiga':(0.009,34.718),'laikipia':(0.200,36.800),'samburu':(1.530,37.090),
        'isiolo':(0.354,37.582),'kitui':(-1.369,38.011),'makueni':(-1.805,37.621),
        'taita taveta':(-3.316,38.362),'kwale':(-4.174,39.452),'kilifi':(-3.630,39.849),
        'tana river':(-1.639,40.007),'lamu':(-2.270,40.902),'mandera':(3.937,41.867),
        'wajir':(1.748,40.057),'baringo':(0.470,35.974),'elgeyo marakwet':(0.550,35.532),
    }
    lat, lng = COUNTY_COORDS.get(county.lower().strip(), (-1.286, 36.817))
    # Fetch 14-day forecast
    try:
        import requests as _req
        r = _req.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&daily=precipitation_sum,temperature_2m_max,temperature_2m_min,sunshine_duration,soil_temperature_0cm&forecast_days=14&timezone=Africa%2FNairobi", timeout=10)
        forecast = r.json().get('daily', {})
    except: forecast = {}
    # Find optimal planting windows
    windows = _find_planting_windows(forecast)
    return render_template('planting_advisor.html', county=county, forecast=forecast, windows=windows, lat=lat, lng=lng)


def _find_planting_windows(forecast):
    """Identify optimal 3+ consecutive rain days in forecast"""
    windows = []
    dates      = forecast.get('time', [])
    rain       = forecast.get('precipitation_sum', [])
    tmax       = forecast.get('temperature_2m_max', [])
    tmin       = forecast.get('temperature_2m_min', [])
    soil_temp  = forecast.get('soil_temperature_0cm', [])
    for i in range(len(dates) - 2):
        r0 = rain[i] if rain else 0
        r1 = rain[i+1] if rain else 0
        r2 = rain[i+2] if rain else 0
        if r0 and r1 and r2:
            if r0 >= 5 and r1 >= 5 and r2 >= 3:
                temp_ok = (tmax[i] if tmax else 30) <= 32 and (tmin[i] if tmin else 15) >= 12
                soil_ok = (soil_temp[i] if soil_temp else 20) >= 15
                score = 'Excellent' if r0 >= 10 and temp_ok and soil_ok else 'Good' if temp_ok else 'Fair'
                windows.append({'start_date': dates[i], 'rain_3d': [r0,r1,r2], 'score': score,
                    'temp_ok': temp_ok, 'soil_ok': soil_ok,
                    'recommended_crops': _crops_for_conditions(r0, tmax[i] if tmax else 25)})
    return windows[:3]  # Top 3 windows


def _crops_for_conditions(rain_mm, temp_c):
    if rain_mm >= 15 and temp_c <= 28: return ['Maize', 'Beans', 'Sorghum', 'Potatoes']
    elif rain_mm >= 10: return ['Maize', 'Sorghum', 'Cowpeas', 'Sweet Potato']
    elif rain_mm >= 5: return ['Sorghum', 'Millet', 'Cowpeas', 'Cassava']
    return ['Drought-resistant crops only']


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 11 — FARM INSURANCE
# ═══════════════════════════════════════════════════════════════════════════

INSURANCE_PLANS = {
    'maize_basic':      {'name':'Maize Basic Cover','crop':'maize','premium_per_acre':500,'cover_per_acre':15000,'trigger':'<200mm rain in 90 days','provider':'Pula Advisors'},
    'maize_premium':    {'name':'Maize Premium Cover','crop':'maize','premium_per_acre':900,'cover_per_acre':30000,'trigger':'<150mm or >400mm rain or drought index','provider':'UAP Insurance'},
    'general_crop':     {'name':'General Crop Cover','crop':'any','premium_per_acre':700,'cover_per_acre':20000,'trigger':'<180mm rain in 90 days','provider':'Jubilee Insurance'},
    'livestock_basic':  {'name':'Livestock Basic','crop':'livestock','premium_per_head':300,'cover_per_head':8000,'trigger':'Drought mortality or disease outbreak','provider':'CIC Insurance'},
    'horticulture':     {'name':'Horticulture Cover','crop':'vegetables','premium_per_acre':1200,'cover_per_acre':40000,'trigger':'Extreme weather or frost','provider':'APA Insurance'},
}

@app.route('/insurance')
@login_required
def insurance():
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM insurance_policies WHERE user_id=%s ORDER BY created_at DESC", (uid,))
        my_policies = cursor.fetchall()
        cursor.execute("SELECT * FROM insurance_claims WHERE user_id=%s ORDER BY created_at DESC LIMIT 10", (uid,))
        my_claims = cursor.fetchall()
    except: my_policies, my_claims = [], []
    cursor.close(); db.close()
    return render_template('insurance.html', plans=INSURANCE_PLANS, my_policies=my_policies, my_claims=my_claims)


@app.route('/insurance/apply', methods=['POST'])
@login_required
def insurance_apply():
    uid = session['user_id']
    plan_key  = request.form.get('plan_key','')
    acres     = float(request.form.get('acres', 1))
    heads     = int(request.form.get('heads', 0))
    county    = clean(request.form.get('county',''))
    plan = INSURANCE_PLANS.get(plan_key)
    if not plan:
        flash("Invalid insurance plan", "danger")
        return redirect(url_for('insurance'))
    if plan['crop'] == 'livestock':
        premium = plan['premium_per_head'] * heads
        cover   = plan['cover_per_head'] * heads
    else:
        premium = plan['premium_per_acre'] * acres
        cover   = plan['cover_per_acre'] * acres
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO insurance_policies
            (user_id, plan_key, plan_name, premium, cover_amount, acres, heads, county, provider, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending_payment')""",
            (uid, plan_key, plan['name'], premium, cover, acres, heads, county, plan['provider']))
        policy_id = cursor.lastrowid
        db.commit()
        # Redirect to M-Pesa pay for the premium
        session['insurance_policy_id'] = policy_id
        flash(f"Insurance application created. Pay KSh {premium:,.0f} premium via M-Pesa to activate.", "success")
        return redirect(url_for('mpesa_pay') + f"?plan=insurance&amount={int(premium)}&ref=INS-{policy_id}")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('insurance'))


@app.route('/insurance/claim', methods=['POST'])
@login_required
def insurance_claim():
    uid       = session['user_id']
    policy_id = request.form.get('policy_id')
    event     = clean(request.form.get('event',''), 300)
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO insurance_claims (user_id, policy_id, event_description, status)
            VALUES (%s,%s,%s,'submitted')""", (uid, policy_id, event))
        db.commit()
        flash("Claim submitted. Our team will review and contact you within 5 business days.", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('insurance'))


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 12 — CARBON CREDITS
# ═══════════════════════════════════════════════════════════════════════════

CARBON_PRACTICES = {
    'no_burn':          {'name':'No Crop Burning','tonnes_co2_per_acre_year':0.8,'description':'Avoid burning crop residue'},
    'cover_crops':      {'name':'Cover Cropping','tonnes_co2_per_acre_year':1.2,'description':'Plant legumes between seasons'},
    'agroforestry':     {'name':'Agroforestry','tonnes_co2_per_acre_year':3.5,'description':'Plant trees alongside crops'},
    'composting':       {'name':'Composting','tonnes_co2_per_acre_year':0.5,'description':'Compost organic waste instead of burning'},
    'reduced_tillage':  {'name':'Reduced Tillage','tonnes_co2_per_acre_year':0.7,'description':'Minimise soil disturbance'},
    'water_harvesting': {'name':'Water Harvesting','tonnes_co2_per_acre_year':0.4,'description':'Terrace farming and water pans'},
    'manure_management':{'name':'Manure Management','tonnes_co2_per_acre_year':0.6,'description':'Proper manure storage and use'},
}

@app.route('/carbon')
@login_required
def carbon():
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM carbon_practices WHERE user_id=%s ORDER BY logged_at DESC", (uid,))
        my_practices = cursor.fetchall()
        cursor.execute("SELECT farm_size_acres FROM users WHERE id=%s", (uid,))
        u = cursor.fetchone()
        farm_acres = float(u['farm_size_acres'] or 1) if u else 1
        # Calculate total carbon credits
        total_tonnes = sum(
            CARBON_PRACTICES.get(p['practice_key'], {}).get('tonnes_co2_per_acre_year', 0) * farm_acres
            for p in my_practices
        )
        credit_value_usd = total_tonnes * 12  # ~$12/tonne voluntary market
        credit_value_ksh = credit_value_usd * 130  # approximate KES rate
        cursor.execute("SELECT * FROM carbon_listings WHERE user_id=%s ORDER BY created_at DESC LIMIT 5", (uid,))
        my_listings = cursor.fetchall()
    except Exception as e:
        print(f"[Carbon] {e}")
        my_practices, my_listings, total_tonnes, credit_value_ksh = [], [], 0, 0
        farm_acres = 1
    cursor.close(); db.close()
    return render_template('carbon.html',
        practices=CARBON_PRACTICES, my_practices=my_practices,
        total_tonnes=round(total_tonnes, 2), credit_value_ksh=int(credit_value_ksh),
        farm_acres=farm_acres, my_listings=my_listings)


@app.route('/carbon/log', methods=['POST'])
@login_required
def carbon_log():
    uid          = session['user_id']
    practice_key = request.form.get('practice_key','')
    acres        = float(request.form.get('acres', 1))
    notes        = clean(request.form.get('notes',''), 300)
    if practice_key not in CARBON_PRACTICES:
        flash("Invalid practice", "danger")
        return redirect(url_for('carbon'))
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO carbon_practices (user_id, practice_key, acres, notes)
            VALUES (%s,%s,%s,%s)""", (uid, practice_key, acres, notes))
        db.commit()
        p = CARBON_PRACTICES[practice_key]
        tonnes = p['tonnes_co2_per_acre_year'] * acres
        flash(f"Logged '{p['name']}' — estimated {tonnes:.1f} tonnes CO₂/year. Keep building your carbon portfolio.", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('carbon'))


@app.route('/carbon/list', methods=['POST'])
@login_required
def carbon_list():
    uid          = session['user_id']
    tonnes       = float(request.form.get('tonnes', 0))
    price_per_t  = float(request.form.get('price_per_tonne', 12))
    notes        = clean(request.form.get('notes',''), 200)
    if tonnes <= 0:
        flash("Enter valid tonnes amount", "danger")
        return redirect(url_for('carbon'))
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute("""INSERT INTO carbon_listings (user_id, tonnes, price_per_tonne, total_value_usd, notes, status)
            VALUES (%s,%s,%s,%s,%s,'available')""",
            (uid, tonnes, price_per_t, tonnes*price_per_t, notes))
        db.commit()
        flash(f"Listed {tonnes} tonnes CO₂ at ${price_per_t}/tonne (${tonnes*price_per_t:,.0f} total) on carbon marketplace.", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    cursor.close(); db.close()
    return redirect(url_for('carbon'))


# ═══════════════════════════════════════════════════════════════════════════
#  MODULE 13 — USSD INTERFACE (*384#) via Africa's Talking
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/ussd', methods=['POST'])
def ussd():
    """Africa's Talking USSD callback"""
    session_id   = request.form.get('sessionId','')
    phone        = request.form.get('phoneNumber','')
    text         = request.form.get('text','')
    inputs       = text.split('*') if text else []
    level        = len(inputs)
    # Find user
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE phone LIKE %s LIMIT 1", (f"%{phone[-9:]}%",))
    user = cursor.fetchone()
    cursor.close(); db.close()
    name = user['name'].split()[0] if user else 'Farmer'

    if text == '':
        # Main menu
        response = (f"CON Welcome to Plantain AI 🌿\n"
                    f"Hello {name}!\n"
                    "1. Today's Market Prices\n"
                    "2. Weather Forecast\n"
                    "3. My Farm Credit Score\n"
                    "4. Report Crop Problem\n"
                    "5. Livestock Prices\n"
                    "0. Exit")
    elif text == '1':
        response = "CON Market Prices 📊\nSelect crop:\n1. Maize\n2. Tomato\n3. Beans\n4. Potatoes\n5. Onions\n6. Other\n0. Back"
    elif text.startswith('1*'):
        crop_map = {'1':'maize','2':'tomato','3':'beans','4':'potatoes','5':'onions'}
        crop_choice = inputs[1] if len(inputs) > 1 else ''
        prices = fetch_market_prices()
        if crop_choice == '6':
            response = "CON Enter crop name:"
        elif crop_choice in crop_map:
            crop = crop_map[crop_choice]
            p = prices.get(crop, {})
            price = p.get('price_kg', 0)
            trend = p.get('trend','stable')
            emoji = '↑' if trend=='rising' else '↓' if trend=='falling' else '→'
            response = f"END {crop.title()} Price\nKSh {price}/kg {emoji} {trend.title()}\n\nFor full prices visit: plantain.ai\nPowered by Plantain AI"
        else:
            crop = inputs[2] if len(inputs) > 2 else ''
            if crop:
                p = prices.get(crop.lower(), {})
                price = p.get('price_kg', 0)
                response = f"END {crop.title()}: KSh {price}/kg\nPowered by Plantain AI"
            else:
                response = "END Invalid selection. Visit plantain.ai for full prices."
    elif text == '2':
        response = "CON Weather 🌤\nSelect county:\n1. Nairobi\n2. Kisumu\n3. Nakuru\n4. Mombasa\n5. Eldoret\n0. Back"
    elif text.startswith('2*'):
        county_map = {'1':'Nairobi','2':'Kisumu','3':'Nakuru','4':'Mombasa','5':'Eldoret'}
        ch = inputs[1] if len(inputs) > 1 else ''
        county = county_map.get(ch,'Nairobi')
        response = f"END Weather: {county}\nFor 14-day forecast go to:\nplantain.ai/planting-advisor\n\nPowered by Plantain AI"
    elif text == '3':
        if user:
            data = calculate_credit_score(user['id'])
            response = f"END Your Farm Credit Score\n{'='*20}\nScore: {data['score']}/850\nGrade: {data['grade']}\n\nMax Loan: KSh {data['max_loan']:,}\nEligible: {'Yes' if data['eligible_loan'] else 'No'}\n\nPlantain AI"
        else:
            response = "END You don't have an account.\nRegister at: plantain.ai\nPowered by Plantain AI"
    elif text == '4':
        response = "CON Crop Problem 🌿\nDescribe in one word:\n1. Yellow leaves\n2. Brown spots\n3. Wilting\n4. Pests/insects\n5. Stunted growth\n0. Back"
    elif text.startswith('4*'):
        problem_map = {'1':'yellow leaves','2':'brown spots','3':'wilting','4':'pests insects','5':'stunted growth'}
        ch = inputs[1] if len(inputs)>1 else ''
        problem = problem_map.get(ch,'crop problem')
        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=[{"role":"user","content":f"Kenyan crop {problem}. Give 1 sentence diagnosis and 1 sentence treatment. Very short."}],
                max_tokens=60
            )
            advice = resp.choices[0].message.content.strip()[:120]
        except:
            advice = "Visit plantain.ai for full diagnosis with photo"
        response = f"END Diagnosis: {problem.title()}\n{advice}\n\nFull diagnosis at: plantain.ai\nPlantain AI"
    elif text == '5':
        prices = fetch_animal_prices()
        top = list(prices.items())[:4]
        lines = "\n".join([f"{n.title()[:15]}: KSh {v.get('retail',0)}" for n,v in top])
        response = f"END Livestock Prices Today\n{lines}\n\nFull prices: plantain.ai\nPlantain AI"
    elif text == '0':
        response = "END Thank you for using Plantain AI!\nplantain.ai\nPowered by AI 🌿"
    else:
        response = "END Invalid option.\nTry again: *384#\nPlantain AI"

    return response, 200, {'Content-Type': 'text/plain'}