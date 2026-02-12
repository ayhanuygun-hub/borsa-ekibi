
import os, threading, time, io, json, pandas as pd, requests
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- AYARLAR ---
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
import os
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY") # finnhub.io adresinden ücretsiz alın

client = MongoClient(MONGO_URI)
db = client['borsa_takip']
collection = db['veriler']

fiyat_deposu = {}

def veriyi_yukle():
    data = collection.find_one({"_id": "sistem_verisi"})
    if not data:
        data = {
            "_id": "sistem_verisi",
            "yonetici_sifre": "admin123", # İlk giriş için yönetici şifresi
            "kullanicilar": {}, # Kullanıcı adı: Şifre eşleşmesi
            "takip_listesi": {}, # Hisse: Hedef
            "portfoyler": {}, # Kullanıcı: {Hisse: {adet, maliyet}}
            "mesajlar": []
        }
        collection.insert_one(data)
    return data

def veriyi_kaydet(sistem):
    collection.replace_one({"_id": "sistem_verisi"}, sistem)

# --- FINNHUB İLE CANLI VERİ ÇEKME ---
def fiyatlari_guncelle_loop():
    global fiyat_deposu
    while True:
        try:
            sistem = veriyi_yukle()
            semboller = list(sistem["takip_listesi"].keys())
            for s in semboller:
                # Finnhub API Formatı (BIST hisseleri için THYAO.IS gibi kullanılır)
                url = f"https://finnhub.io/api/v1/quote?symbol={s}&token={FINNHUB_API_KEY}"
                r = requests.get(url)
                if r.status_code == 200:
                    data = r.json()
                    current_price = data.get('c', 0) # 'c' = Current Price
                    if current_price > 0:
                        fiyat_deposu[s] = round(float(current_price), 2)
                time.sleep(1.5) # API limitlerine takılmamak için (Saniyede 1-2 istek)
        except Exception as e:
            print(f"Finnhub Hatası: {e}")
        time.sleep(60)

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

# --- ROTALAR ---
@app.route('/')
def ana_sayfa(): return send_file('index.html')

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    sistem = veriyi_yukle()
    user = data.get("user")
    sifre = data.get("sifre")
    rol = data.get("rol")

    if rol == "yonetici":
        if sifre == sistem["yonetici_sifre"]: return jsonify({"durum": "basarili"})
    else:
        # Bireysel kullanıcı şifre kontrolü
        if user in sistem["kullanicilar"] and sistem["kullanicilar"][user] == sifre:
            return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

@app.route('/kullanici-ekle', methods=['POST'])
def add_user():
    data = request.json
    sistem = veriyi_yukle()
    sistem["kullanicilar"][data['username']] = data['password']
    veriyi_kaydet(sistem)
    return jsonify({"durum": "ok"})

@app.route('/borsa-verileri')
def get_data():
    sistem = veriyi_yukle()
    veriler = []
    for sembol, hedef in sistem["takip_listesi"].items():
        anlik = fiyat_deposu.get(sembol, 0)
        veriler.append({
            "sembol": sembol.replace(".IS",""), 
            "fiyat": anlik, "hedef": hedef, 
            "durum": "AL" if 0 < anlik <= hedef else "BEKLE"
        })
    return jsonify({
        "hisseler": veriler, "portfoyler": sistem["portfoyler"], 
        "mesajlar": sistem.get("mesajlar", [])[-30:]
    })

@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    sistem = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS"): kod += ".IS"
    sistem["takip_listesi"][kod] = float(data.get("hedef", 0))
    veriyi_kaydet(sistem)
    return jsonify({"durum": "tamam"})

@app.route('/hisse-sil', methods=['POST'])
def delete_hisse():
    data = request.json
    sistem = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS"): kod += ".IS"
    if kod in sistem["takip_listesi"]: del sistem["takip_listesi"][kod]
    veriyi_kaydet(sistem)
    return jsonify({"durum": "silindi"})

@app.route('/adet-guncelle', methods=['POST'])
def update_amount():
    data = request.json
    user, hisse = data.get("kullanici"), data.get("hisse").upper()
    sistem = veriyi_yukle()
    if user not in sistem["portfoyler"]: sistem["portfoyler"][user] = {}
    sistem["portfoyler"][user][hisse] = {"adet": int(data.get("adet", 0)), "maliyet": float(data.get("maliyet", 0))}
    veriyi_kaydet(sistem)
    return jsonify({"durum": "guncellendi"})

@app.route('/mesaj-gonder', methods=['POST'])
def send_msg():
    data = request.json
    sistem = veriyi_yukle()
    sistem["mesajlar"].append({"user": data['user'], "text": data['text'], "time": time.strftime("%H:%M")})
    veriyi_kaydet(sistem)
    return jsonify({"durum": "ok"})

@app.route('/excel-indir')
def export():
    sistem = veriyi_yukle()
    rows = []
    for u, assets in sistem["portfoyler"].items():
        for s, info in assets.items():
            cur = fiyat_deposu.get(s + ".IS", 0)
            rows.append({"Kullanıcı": u, "Hisse": s, "Adet": info['adet'], "Maliyet": info['maliyet'], "Güncel": cur, "K/Z": round((cur-info['maliyet'])*info['adet'], 2)})
    df = pd.DataFrame(rows)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w: df.to_excel(w, index=False)
    out.seek(0)
    return send_file(out, download_name="Ekip_Raporu.xlsx", as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)