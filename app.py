import os, threading, time, io, json, pandas as pd, requests, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime

app = Flask(__name__)
CORS(app)

# --- MONGODB ---
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client['borsa_takip']
    veriler_col = db['veriler']
    chat_col = db['chat_logs']
    log_col = db['connection_logs']
    client.admin.command('ping') # Bağlantıyı test et
except Exception as e:
    print(f"KRİTİK HATA: MongoDB'ye bağlanılamadı: {e}")

fiyat_deposu = {}

def veriyi_yukle():
    try:
        data = veriler_col.find_one({"_id": "sistem_verisi"})
        if not data:
            data = {"_id": "sistem_verisi", "yonetici_sifre": "admin123", "kullanicilar": {}, "takip_listesi": {}, "portfoyler": {}, "fiyat_yedek": {}}
            veriler_col.insert_one(data)
        return data
    except:
        return {"kullanicilar": {}, "takip_listesi": {}, "portfoyler": {}, "fiyat_yedek": {}}

def veriyi_kaydet(sistem):
    try:
        veriler_col.replace_one({"_id": "sistem_verisi"}, sistem)
    except Exception as e:
        print(f"Kaydetme Hatası: {e}")

# --- FİYAT DÖNGÜSÜ (Geliştirilmiş Hata Yönetimi) ---
def fiyat_cek_zorla(sembol):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}?interval=1m&range=1d"
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            return round(float(r.json()['chart']['result'][0]['meta']['regularMarketPrice']), 2)
    except: pass
    return 0

def fiyatlari_guncelle_loop():
    global fiyat_deposu
    while True:
        try:
            sistem = veriyi_yukle()
            semboller = list(sistem.get("takip_listesi", {}).keys())
            for s in semboller:
                f = fiyat_cek_zorla(s)
                if f > 0:
                    fiyat_deposu[s] = f
                    sistem["fiyat_yedek"][s] = f
                time.sleep(1.5) # Render ban koruması için ideal süre
            veriyi_kaydet(sistem)
        except Exception as e:
            print(f"Döngü hatası: {e}")
        time.sleep(60)

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

# --- ROTALAR ---

@app.route('/')
def ana_sayfa(): return send_file('index.html')

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    s = veriyi_yukle()
    user, sifre, rol = data.get("user"), data.get("sifre"), data.get("rol")
    success = False
    if rol == "yonetici":
        if sifre == s.get("yonetici_sifre"): success = True
    else:
        if user in s.get("kullanicilar", {}) and s["kullanicilar"][user] == sifre: success = True
    
    if success:
        log_col.insert_one({"user": user, "time": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "role": rol})
        return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

@app.route('/borsa-verileri')
def get_data():
    try:
        s = veriyi_yukle()
        mesajlar = list(chat_col.find().sort("_id", -1).limit(30))
        for m in mesajlar: m["_id"] = str(m["_id"])
        veriler = []
        for sembol, hedef in s.get("takip_listesi", {}).items():
            anlik = fiyat_deposu.get(sembol) or s.get("fiyat_yedek", {}).get(sembol, 0)
            veriler.append({"sembol": sembol.replace(".IS",""), "fiyat": anlik, "hedef": hedef, "durum": "AL" if 0 < anlik <= hedef else "BEKLE"})
        return jsonify({"hisseler": veriler, "portfoyler": s.get("portfoyler", {}), "kullanicilar": list(s.get("kullanicilar", {}).keys()), "mesajlar": mesajlar[::-1]})
    except:
        return jsonify({"hisseler": [], "mesajlar": []}), 500

@app.route('/mesaj-gonder', methods=['POST'])
def send_msg():
    data = request.json
    chat_col.insert_one({"user": data['user'], "text": data['text'], "time": datetime.now().strftime("%H:%M")})
    return jsonify({"durum": "ok"})

@app.route('/kullanici-sil', methods=['POST'])
def delete_user():
    data = request.json
    s = veriyi_yukle()
    user = data.get("username")
    if user in s.get("kullanicilar", {}):
        del s["kullanicilar"][user]
        if user in s.get("portfoyler", {}): del s["portfoyler"][user]
        veriyi_kaydet(s)
        return jsonify({"durum": "silindi"})
    return jsonify({"durum": "hata"}), 404

@app.route('/loglari-getir')
def get_logs():
    logs = list(log_col.find().sort("_id", -1).limit(50))
    for l in logs: l["_id"] = str(l["_id"])
    return jsonify(logs)

@app.route('/tabloyu-temizle', methods=['POST'])
def clear_table():
    tablo = request.json.get("tablo")
    if tablo == "chat": chat_col.delete_many({})
    elif tablo == "logs": log_col.delete_many({})
    return jsonify({"durum": "temizlendi"})

@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS") and len(kod) <= 5: kod += ".IS"
    s["takip_listesi"][kod] = float(data.get("hedef", 0))
    # Otomatik Mesaj
    chat_col.insert_one({"user": "SİSTEM", "text": f"📢 SİNYAL: {kod.replace('.IS','')} listeye eklendi.", "time": datetime.now().strftime("%H:%M")})
    veriyi_kaydet(s)
    return jsonify({"durum": "tamam"})

@app.route('/hisse-sil', methods=['POST'])
def delete_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS") and len(kod) <= 5: kod += ".IS"
    if kod in s["takip_listesi"]:
        del s["takip_listesi"][kod]
        veriyi_kaydet(s)
        return jsonify({"durum": "silindi"})
    return jsonify({"durum": "hata"}), 404

@app.route('/adet-guncelle', methods=['POST'])
def update_amount():
    data = request.json
    s = veriyi_yukle()
    u, h = data.get("kullanici"), data.get("hisse").upper()
    if "portfoyler" not in s: s["portfoyler"] = {}
    if u not in s["portfoyler"]: s["portfoyler"][u] = {}
    s["portfoyler"][u][h] = {"adet": int(data.get("adet", 0)), "maliyet": float(data.get("maliyet", 0))}
    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

@app.route('/kullanici-ekle', methods=['POST'])
def add_user():
    data = request.json
    s = veriyi_yukle()
    s["kullanicilar"][data['username']] = data['password']
    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

@app.route('/excel-indir')
def export():
    s = veriyi_yukle()
    rows = []
    for u, assets in s.get("portfoyler", {}).items():
        for st, info in assets.items():
            cur = fiyat_deposu.get(st + ".IS", 0) or s.get("fiyat_yedek", {}).get(st + ".IS", 0)
            rows.append({"Kullanıcı": u, "Hisse": st, "Adet": info['adet'], "Maliyet": info['maliyet'], "Güncel": cur, "K/Z": round((cur-info['maliyet'])*info['adet'], 2)})
    df = pd.DataFrame(rows)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w: df.to_excel(w, index=False)
    out.seek(0)
    return send_file(out, download_name="Ekip_Portfoy.xlsx", as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)