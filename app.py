import os, threading, time, io, json, pandas as pd, requests, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- AYARLAR ---
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['borsa_takip']
collection = db['veriler']

fiyat_deposu = {}

def veriyi_yukle():
    data = collection.find_one({"_id": "sistem_verisi"})
    if not data:
        data = {"_id": "sistem_verisi", "yonetici_sifre": "admin123", "kullanicilar": {}, "takip_listesi": {}, "portfoyler": {}, "mesajlar": [], "grup_sifre": "1234"}
        collection.insert_one(data)
    # Eksik anahtar tamiri
    keys = ["yonetici_sifre", "kullanicilar", "takip_listesi", "portfoyler", "mesajlar", "grup_sifre"]
    for k in keys:
        if k not in data: data[k] = ({} if k != "mesajlar" else [])
    return data

def veriyi_kaydet(sistem):
    collection.replace_one({"_id": "sistem_verisi"}, sistem)

# --- BİST VERİSİ İÇİN YEDEK MOTOR (Scraper) ---
def alternatif_fiyat_cek(sembol):
    try:
        # Sembolü temizle (THYAO.IS -> THYAO)
        s = sembol.replace(".IS", "").upper()
        # Yahoo Finance'in web sayfasından çekmeyi dene (API yerine)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        price = data['chart']['result'][0]['meta']['regularMarketPrice']
        return round(float(price), 2)
    except:
        return 0

def fiyatlari_guncelle_loop():
    global fiyat_deposu
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    
    while True:
        try:
            sistem = veriyi_yukle()
            semboller = list(sistem.get("takip_listesi", {}).keys())
            for s in semboller:
                # Önce Yahoo Ticker dene
                try:
                    t = yf.Ticker(s, session=session)
                    df = t.history(period="1d", interval="1m")
                    if not df.empty:
                        fiyat_deposu[s] = round(float(df['Close'].iloc[-1]), 2)
                    else:
                        # Başarısız olursa alternatif motoru çalıştır
                        alt_price = alternatif_fiyat_cek(s)
                        if alt_price > 0: fiyat_deposu[s] = alt_price
                except:
                    continue
                time.sleep(1)
        except: pass
        time.sleep(60)

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

# --- ROTALAR ---
@app.route('/')
def ana_sayfa(): return send_file('index.html')

@app.route('/borsa-verileri')
def get_data():
    s = veriyi_yukle()
    veriler = []
    for sembol, hedef in s.get("takip_listesi", {}).items():
        anlik = fiyat_deposu.get(sembol, 0)
        veriler.append({
            "sembol": sembol.replace(".IS",""), "fiyat": anlik, "hedef": hedef,
            "durum": "AL" if 0 < anlik <= hedef else "BEKLE"
        })
    return jsonify({"hisseler": veriler, "portfoyler": s.get("portfoyler", {}), "mesajlar": s.get("mesajlar", [])[-20:]})

@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS"): kod += ".IS"
    s["takip_listesi"][kod] = float(data.get("hedef", 0))
    s["mesajlar"].append({"user": "SİSTEM", "text": f"📢 SİNYAL: {kod.replace('.IS','')} - Hedef: {data.get('hedef')}", "time": time.strftime("%H:%M")})
    veriyi_kaydet(s)
    return jsonify({"durum": "tamam"})

@app.route('/hisse-sil', methods=['POST'])
def delete_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    full_kod = kod if kod.endswith(".IS") else kod + ".IS"
    if full_kod in s["takip_listesi"]:
        del s["takip_listesi"][full_kod]
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

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    s = veriyi_yukle()
    if data.get("rol") == "yonetici":
        if data.get("sifre") == s.get("yonetici_sifre"): return jsonify({"durum": "basarili"})
    elif data.get("user") in s.get("kullanicilar") and s["kullanicilar"][data.get("user")] == data.get("sifre"):
        return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

@app.route('/kullanici-ekle', methods=['POST'])
def add_user():
    data = request.json
    s = veriyi_yukle()
    s["kullanicilar"][data['username']] = data['password']
    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

@app.route('/mesaj-gonder', methods=['POST'])
def msg():
    data = request.json
    s = veriyi_yukle()
    s["mesajlar"].append({"user": data['user'], "text": data['text'], "time": time.strftime("%H:%M")})
    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

@app.route('/excel-indir')
def export():
    s = veriyi_yukle()
    rows = []
    for u, assets in s.get("portfoyler", {}).items():
        for st, info in assets.items():
            cur = fiyat_deposu.get(st + ".IS", 0)
            rows.append({"Kullanıcı": u, "Hisse": st, "Adet": info['adet'], "Maliyet": info['maliyet'], "Güncel": cur, "K/Z": (cur-info['maliyet'])*info['adet']})
    df = pd.DataFrame(rows)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w: df.to_excel(w, index=False)
    out.seek(0)
    return send_file(out, download_name="Ekip_Portfoy.xlsx", as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)