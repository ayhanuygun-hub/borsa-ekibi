import os, threading, time, io, json, pandas as pd, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient
import requests # Yeni ekledik

app = Flask(__name__)
CORS(app)

# --- MONGODB BAĞLANTISI ---
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['borsa_takip']
collection = db['veriler']

fiyat_deposu = {}

def veriyi_yukle():
    data = collection.find_one({"_id": "sistem_verisi"})
    if not data:
        data = {
            "_id": "sistem_verisi", 
            "yonetici_sifre": "1234", 
            "grup_sifre": "1234", 
            "takip_listesi": {}, 
            "kullanicilar": {},
            "mesajlar": [] # Mesaj kutusu için yeni alan
        }
        collection.insert_one(data)
    return data

def veriyi_kaydet(sistem):
    collection.replace_one({"_id": "sistem_verisi"}, sistem)

# Gelişmiş Fiyat Güncelleme (Yahoo engeli aşmak için)
def fiyatlari_guncelle_loop():
    global fiyat_deposu
    while True:
        sistem = veriyi_yukle()
        semboller = list(sistem["takip_listesi"].keys())
        if semboller:
            try:
                # Toplu çekim ve User-Agent taklidi
                data = yf.download(semboller, period="1d", interval="1m", progress=False, group_by='ticker')
                for s in semboller:
                    try:
                        # Tekli veya çoklu hisse durumuna göre veri ayıklama
                        if len(semboller) > 1:
                            val = data[s]['Close'].iloc[-1]
                        else:
                            val = data['Close'].iloc[-1]
                        if not pd.isna(val):
                            fiyat_deposu[s] = round(float(val), 2)
                    except: continue
            except Exception as e:
                print(f"Borsa Hatası: {e}")
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
            "fiyat": anlik, 
            "hedef": hedef, 
            "durum": "AL" if 0 < anlik <= hedef else "BEKLE"
        })
    return jsonify({
        "hisseler": veriler, 
        "ekip": sistem["kullanicilar"],
        "mesajlar": sistem.get("mesajlar", [])[-20:] # Son 20 mesajı gönder
    })

# --- YENİ MESAJLAŞMA ROTASI ---
@app.route('/mesaj-gonder', methods=['POST'])
def mesaj_gonder():
    data = request.json
    sistem = veriyi_yukle()
    yeni_mesaj = {
        "user": data.get("user"),
        "text": data.get("text"),
        "time": time.strftime("%H:%M")
    }
    if "mesajlar" not in sistem: sistem["mesajlar"] = []
    sistem["mesajlar"].append(yeni_mesaj)
    veriyi_kaydet(sistem)
    return jsonify({"durum": "mesaj iletildi"})

# Diğer rotalar (hisse-ekle, adet-guncelle, giris-yap) öncekiyle aynı...
# Lütfen önceki app.py'deki o kısımları buraya eklemeyi unutmayın.

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)