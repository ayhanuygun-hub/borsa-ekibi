import os, threading, time, io, json, pandas as pd, requests, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- AYARLAR ---
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY") 

client = MongoClient(MONGO_URI)
db = client['borsa_takip']
collection = db['veriler']

fiyat_deposu = {}

def veriyi_yukle():
    data = collection.find_one({"_id": "sistem_verisi"})
    if not data:
        data = {
            "_id": "sistem_verisi",
            "yonetici_sifre": "admin123",
            "kullanicilar": {}, 
            "takip_listesi": {},
            "portfoyler": {}, 
            "mesajlar": [],
            "grup_sifre": "1234"
        }
        collection.insert_one(data)
    
    # Eksik anahtar kontrolü
    keys = ["yonetici_sifre", "kullanicilar", "takip_listesi", "portfoyler", "mesajlar", "grup_sifre"]
    updated = False
    for k in keys:
        if k not in data:
            data[k] = "1234" if "sifre" in k else ({} if k != "mesajlar" else [])
            updated = True
    if updated: collection.replace_one({"_id": "sistem_verisi"}, data)
    return data

def veriyi_kaydet(sistem):
    collection.replace_one({"_id": "sistem_verisi"}, sistem)

# --- VERI CEKME (Gelişmiş Yahoo & Finnhub Hibrit) ---
def fiyatlari_guncelle_loop():
    global fiyat_deposu
    # Yahoo'yu kandırmak için tarayıcı başlıkları
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })

    while True:
        try:
            sistem = veriyi_yukle()
            semboller = list(sistem.get("takip_listesi", {}).keys())
            
            if not semboller:
                print("Takip listesi boş, veri çekilmiyor...")
            
            for s in semboller:
                basarili = False
                
                # 1. Tercih: Finnhub (API Key varsa)
                if FINNHUB_API_KEY:
                    try:
                        url = f"https://finnhub.io/api/v1/quote?symbol={s}&token={FINNHUB_API_KEY}"
                        r = session.get(url, timeout=5)
                        res = r.json()
                        if r.status_code == 200 and res.get('c', 0) > 0:
                            fiyat_deposu[s] = round(float(res['c']), 2)
                            basarili = True
                            print(f"Finnhub: {s} -> {fiyat_deposu[s]}")
                    except: pass

                # 2. Tercih: Yahoo Finance (Finnhub başarısızsa veya BIST verisi vermezse)
                if not basarili:
                    try:
                        # Session kullanarak Yahoo engelini aşmaya çalışıyoruz
                        t = yf.Ticker(s, session=session)
                        h = t.history(period="1d", interval="1m")
                        if not h.empty:
                            fiyat_deposu[s] = round(float(h['Close'].iloc[-1]), 2)
                            basarili = True
                            print(f"Yahoo: {s} -> {fiyat_deposu[s]}")
                        else:
                            print(f"Yahoo Veri Bulamadı: {s}")
                    except Exception as e:
                        print(f"Yahoo Hatası ({s}): {e}")
                
                time.sleep(2) # IP ban yememek için bekleme

        except Exception as global_e:
            print(f"Döngü genel hatası: {global_e}")
            
        time.sleep(60) # 1 dakikada bir güncelle

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

# --- ROTALAR ---

@app.route('/')
def ana_sayfa(): return send_file('index.html')

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    s = veriyi_yukle()
    user, sifre, rol = data.get("user"), data.get("sifre"), data.get("rol")
    if rol == "yonetici":
        if sifre == s.get("yonetici_sifre"): return jsonify({"durum": "basarili"})
    else:
        if user in s.get("kullanicilar", {}) and s["kullanicilar"][user] == sifre:
            return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

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
    veriyi_kaydet(s)
    return jsonify({"durum": "tamam"})

@app.route('/hisse-sil', methods=['POST'])
def delete_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS"): kod += ".IS"
    if kod in s["takip_listesi"]:
        del s["takip_listesi"][kod]
        veriyi_kaydet(s)
        return jsonify({"durum": "silindi"})
    return jsonify({"durum": "hata"}), 404

@app.route('/adet-guncelle', methods=['POST'])
def update_amount():
    try:
        data = request.json
        user = data.get("kullanici")
        hisse = data.get("hisse", "").upper()
        adet = data.get("adet", 0)
        maliyet = data.get("maliyet", 0)
        sistem = veriyi_yukle()
        if "portfoyler" not in sistem: sistem["portfoyler"] = {}
        if user not in sistem["portfoyler"]: sistem["portfoyler"][user] = {}
        sistem["portfoyler"][user][hisse] = {"adet": int(adet) if adet else 0, "maliyet": float(maliyet) if maliyet else 0.0}
        veriyi_kaydet(sistem)
        return jsonify({"durum": "guncellendi", "hisse": hisse})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500

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