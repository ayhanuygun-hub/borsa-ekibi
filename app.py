import os, threading, time, io, pandas as pd, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- MONGODB BAĞLANTISI ---
# Buraya Atlas'tan aldığınız bağlantı linkini yapıştırın
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client['borsa_takip']
collection = db['veriler']

fiyat_deposu = {}

def veriyi_yukle():
    # Veriyi dosyadan değil MongoDB'den çekiyoruz
    data = collection.find_one({"_id": "sistem_verisi"})
    if not data:
        data = {"_id": "sistem_verisi", "yonetici_sifre": "1234", "grup_sifre": "1234", "takip_listesi": {}, "kullanicilar": {}}
        collection.insert_one(data)
    return data

def veriyi_kaydet(sistem):
    collection.replace_one({"_id": "sistem_verisi"}, sistem)

# Fiyat güncelleme (Yahoo Finance engeline karşı daha dirençli)
def fiyatlari_guncelle_loop():
    global fiyat_deposu
    while True:
        sistem = veriyi_yukle()
        semboller = list(sistem["takip_listesi"].keys())
        if semboller:
            try:
                for s in semboller:
                    ticker = yf.Ticker(s)
                    hist = ticker.history(period="1d", interval="1m")
                    if not hist.empty:
                        fiyat_deposu[s] = round(float(hist['Close'].iloc[-1]), 2)
                    time.sleep(2) # IP engeli yememek için yavaş çekim
            except: pass
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
        veriler.append({"sembol": sembol.replace(".IS",""), "fiyat": anlik, "hedef": hedef, "durum": "AL" if 0 < anlik <= hedef else "BEKLE"})
    return jsonify({"hisseler": veriler, "ekip": sistem["kullanicilar"]})

@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    sistem = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS"): kod += ".IS"
    sistem["takip_listesi"][kod] = float(data.get("hedef", 0))
    veriyi_kaydet(sistem)
    return jsonify({"durum": "tamam"})

@app.route('/adet-guncelle', methods=['POST'])
def update_amount():
    data = request.json
    user, hisse = data.get("kullanici").strip(), data.get("hisse").upper()
    sistem = veriyi_yukle()
    if user not in sistem["kullanicilar"]: sistem["kullanicilar"][user] = {}
    sistem["kullanicilar"][user][hisse] = {"adet": int(data.get("adet", 0)), "maliyet": float(data.get("maliyet", 0))}
    veriyi_kaydet(sistem)
    return jsonify({"durum": "guncellendi"})

@app.route('/excel-indir')
def excel_indir():
    sistem = veriyi_yukle()
    df_list = []
    for user, assets in sistem["kullanicilar"].items():
        for stock, info in assets.items():
            g_fiyat = fiyat_deposu.get(stock + ".IS", 0)
            df_list.append({"Kullanıcı": user, "Hisse": stock, "Adet": info['adet'], "Maliyet": info['maliyet'], "Güncel": g_fiyat, "K/Z": round((g_fiyat - info['maliyet']) * info['adet'], 2)})
    df = pd.DataFrame(df_list)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, download_name="Ekip_Portfoy.xlsx", as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))

    app.run(host='0.0.0.0', port=port)
