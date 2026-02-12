
import os, threading, time, io, json, pandas as pd, yfinance as yf, requests
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- MONGODB AYARI ---
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['borsa_takip']
collection = db['veriler']

fiyat_deposu = {}
veri_kilidi = threading.Lock()

def veriyi_yukle():
    data = collection.find_one({"_id": "sistem_verisi"})
    if not data:
        data = {
            "_id": "sistem_verisi", "yonetici_sifre": "1234", "grup_sifre": "1234",
            "takip_listesi": {}, "kullanicilar": {}, "mesajlar": []
        }
        collection.insert_one(data)
    return data

def veriyi_kaydet(sistem):
    collection.replace_one({"_id": "sistem_verisi"}, sistem)

# CANLI VERİ ÇEKME (Render Engelini Aşmak İçin Geliştirildi)
def fiyatlari_guncelle_loop():
    global fiyat_deposu
    # Tarayıcı gibi davranmak için header ekliyoruz
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    while True:
        try:
            sistem = veriyi_yukle()
            semboller = list(sistem["takip_listesi"].keys())
            if semboller:
                # Yahoo Finance toplu çekim
                data = yf.download(semboller, period="1d", interval="1m", progress=False)
                for s in semboller:
                    try:
                        if len(semboller) > 1:
                            val = data['Close'][s].iloc[-1]
                        else:
                            val = data['Close'].iloc[-1]
                        
                        if not pd.isna(val):
                            fiyat_deposu[s] = round(float(val), 2)
                    except: continue
        except Exception as e:
            print(f"Borsa Veri Hatası: {e}")
        time.sleep(60)

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

@app.route('/')
def ana_sayfa(): return send_file('index.html')

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
        "hisseler": veriler, "ekip": sistem["kullanicilar"], 
        "mesajlar": sistem.get("mesajlar", [])[-30:]
    })

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    sistem = veriyi_yukle()
    if data.get("rol") == "yonetici" and data.get("sifre") == sistem["yonetici_sifre"]:
        return jsonify({"durum": "basarili"})
    if data.get("sifre") == sistem["grup_sifre"]:
        return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

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
    user, hisse = data.get("kullanici").strip(), data.get("hisse").upper()
    sistem = veriyi_yukle()
    if user not in sistem["kullanicilar"]: sistem["kullanicilar"][user] = {}
    sistem["kullanicilar"][user][hisse] = {"adet": int(data.get("adet", 0)), "maliyet": float(data.get("maliyet", 0))}
    veriyi_kaydet(sistem)
    return jsonify({"durum": "guncellendi"})

@app.route('/mesaj-gonder', methods=['POST'])
def send_msg():
    data = request.json
    sistem = veriyi_yukle()
    if "mesajlar" not in sistem: sistem["mesajlar"] = []
    sistem["mesajlar"].append({"user": data['user'], "text": data['text'], "time": time.strftime("%H:%M")})
    veriyi_kaydet(sistem)
    return jsonify({"durum": "ok"})

@app.route('/excel-indir')
def export():
    sistem = veriyi_yukle()
    rows = []
    for u, assets in sistem["kullanicilar"].items():
        for s, info in assets.items():
            cur = fiyat_deposu.get(s + ".IS", 0)
            rows.append({"Kullanıcı": u, "Hisse": s, "Adet": info['adet'], "Maliyet": info['maliyet'], "Güncel": cur, "K/Z": round((cur-info['maliyet'])*info['adet'], 2)})
    df = pd.DataFrame(rows)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w: df.to_excel(w, index=False)
    out.seek(0)
    return send_file(out, download_name="Ekip_Portfoy.xlsx", as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)