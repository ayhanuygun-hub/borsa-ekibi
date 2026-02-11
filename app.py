from flask import Flask, jsonify, request, send_file # send_file ekledik
# ... diğer importlar ...

# ANA SAYFA ROTASI (Bunu ekle)
@app.route('/')
def ana_sayfa():
    return send_file('index.html')
import threading
import time
from flask import Flask, jsonify, request, send_file
import yfinance as yf
from flask_cors import CORS
import json, os, io
import pandas as pd

app = Flask(__name__)
CORS(app)

DB_FILE = "veritabani.json"
fiyat_deposu = {}
veri_kilidi = threading.Lock()

def veriyi_yukle():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"yonetici_sifre": "1234", "takip_listesi": {}, "kullanicilar": {}}

sistem = veriyi_yukle()

def veriyi_kaydet():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(sistem, f, ensure_ascii=False, indent=4)

def fiyatlari_guncelle_loop():
    global fiyat_deposu
    while True:
        with veri_kilidi:
            semboller = list(sistem["takip_listesi"].keys())
        
        if semboller:
            try:
                # Toplu çekim yaparak hızı artırıyoruz
                data = yf.download(semboller, period="1d", interval="1m", progress=False)['Close']
                for s in semboller:
                    try:
                        val = data[s].iloc[-1] if len(semboller) > 1 else data.iloc[-1]
                        fiyat_deposu[s] = round(float(val), 2)
                    except: continue
            except: pass
        time.sleep(30)

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    if data.get("rol") == "yonetici":
        sistem["yonetici_sifre"] = data.get("sifre")
        veriyi_kaydet()
        return jsonify({"durum": "basarili"})
    if data.get("sifre") == sistem["yonetici_sifre"]:
        return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS"): kod += ".IS"
    with veri_kilidi:
        sistem["takip_listesi"][kod] = float(data.get("hedef", 0))
        veriyi_kaydet()
    return jsonify({"durum": "tamam"})

@app.route('/adet-guncelle', methods=['POST'])
def update_amount():
    data = request.json
    user = data.get("kullanici").strip()
    hisse = data.get("hisse").upper()
    with veri_kilidi:
        if user not in sistem["kullanicilar"]: sistem["kullanicilar"][user] = {}
        sistem["kullanicilar"][user][hisse] = {
            "adet": int(data.get("adet", 0)),
            "maliyet": float(data.get("maliyet", 0))
        }
        veriyi_kaydet()
    return jsonify({"durum": "guncellendi"})

@app.route('/borsa-verileri')
def get_data():
    veriler = []
    with veri_kilidi:
        takip = list(sistem["takip_listesi"].items())
        ekip = dict(sistem["kullanicilar"])
    for sembol, hedef in takip:
        anlik = fiyat_deposu.get(sembol, 0)
        veriler.append({
            "sembol": sembol.replace(".IS",""),
            "fiyat": anlik,
            "hedef": hedef,
            "durum": "AL" if 0 < anlik <= hedef else "BEKLE"
        })
    return jsonify({"hisseler": veriler, "ekip": ekip})

if __name__ == '__main__':
    # Render'ın portunu otomatik alması için PORT değişkeni ekledik
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)