import os, threading, time, io, json, pandas as pd, requests, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- MONGODB ---
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
    for k in ["yonetici_sifre", "kullanicilar", "takip_listesi", "portfoyler", "mesajlar"]:
        if k not in data: data[k] = ({} if k != "mesajlar" else [])
    return data

def veriyi_kaydet(sistem):
    collection.replace_one({"_id": "sistem_verisi"}, sistem)

# --- SÜPER GARANTİCİ FİYAT ÇEKİCİ ---
def fiyat_cek_zorla(sembol):
    try:
        # Yöntem 1: Doğrudan Yahoo Query API (Tarayıcı taklidi ile)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://finance.yahoo.com/'
        }
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}?interval=1m&range=1d"
        r = requests.get(url, headers=headers, timeout=10)
        
        if r.status_code == 200:
            data = r.json()
            price = data['chart']['result'][0]['meta']['regularMarketPrice']
            return round(float(price), 2)
    except Exception as e:
        print(f"Yöntem 1 hatası ({sembol}): {e}")
        
    try:
        # Yöntem 2: yfinance (Sadece kapanış verisi odaklı)
        t = yf.Ticker(sembol)
        # fast_info Render'da daha az hata verir
        price = t.fast_info['last_price']
        if price and not pd.isna(price):
            return round(float(price), 2)
    except:
        pass
        
    return 0

def fiyatlari_guncelle_loop():
    global fiyat_deposu
    while True:
        try:
            sistem = veriyi_yukle()
            semboller = list(sistem.get("takip_listesi", {}).keys())
            
            for s in semboller:
                yeni_fiyat = fiyat_cek_zorla(s)
                if yeni_fiyat > 0:
                    fiyat_deposu[s] = yeni_fiyat
                    print(f"BAŞARILI: {s} -> {yeni_fiyat}")
                else:
                    print(f"BAŞARISIZ: {s} için fiyat alınamadı.")
                time.sleep(2) # IP koruması için bekleme
        except Exception as e:
            print(f"Genel Döngü Hatası: {e}")
        
        time.sleep(60) # Her dakika yenile

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
    s["mesajlar"].append({"user": "SİSTEM", "text": f"📢 SİNYAL: {kod.replace('.IS','')} paylaşıldı!", "time": time.strftime("%H:%M")})
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