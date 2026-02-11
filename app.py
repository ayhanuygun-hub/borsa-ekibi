import os
import threading
import time
import json
import yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Render'da dosyaların silinmesini engellemek için (geçici çözüm)
# Normalde MongoDB gibi bir veritabanı kullanılmalıdır.
DB_FILE = "/opt/render/project/src/veritabani.json" if os.path.exists("/opt/render/project/src") else "veritabani.json"

fiyat_deposu = {}
veri_kilidi = threading.Lock()

def veriyi_yukle():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Eksik anahtarları tamamla
                if "grup_sifre" not in data: data["grup_sifre"] = "1234"
                return data
        except: pass
    return {"yonetici_sifre": "1234", "grup_sifre": "1234", "takip_listesi": {}, "kullanicilar": {}}

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
                # Tek tek çekmek Render'da daha güvenlidir (Bloklanma riski azdır)
                for s in semboller:
                    ticker = yf.Ticker(s)
                    # fast_info bazen Render'da boş döner, history en garantisidir
                    hist = ticker.history(period="1d")
                    if not hist.empty:
                        fiyat_deposu[s] = round(float(hist['Close'].iloc[-1]), 2)
                    time.sleep(1) # IP engeli yememek için bekleme
            except Exception as e:
                print(f"Fiyat hatası: {e}")
        
        time.sleep(60) # 60 saniyede bir güncelle

# Thread başlatma
threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

@app.route('/')
def ana_sayfa():
    return send_file('index.html')

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

@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS"): kod += ".IS"
    with veri_kilidi:
        sistem["takip_listesi"][kod] = float(data.get("hedef", 0))
        veriyi_kaydet()
    return jsonify({"durum": "tamam"})

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    sifre = data.get("sifre")
    if data.get("rol") == "yonetici":
        if sifre == sistem["yonetici_sifre"]: return jsonify({"durum": "basarili"})
    elif sifre == sistem.get("grup_sifre", "1234"):
        return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)