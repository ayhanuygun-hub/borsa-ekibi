import os, threading, time, io, json, pandas as pd, requests, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient

app = Flask(__name__)
CORS(app)

# --- AYARLAR ---
# Senin MongoDB ve Finnhub bilgilerin
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY") 

# MongoDB Bağlantısı
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
    
    # Otomatik Tamir Mekanizması (Eksik anahtar varsa ekler)
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

# --- GELİŞMİŞ VERİ ÇEKME SİSTEMİ (BIST ODAKLI) ---
def fiyatlari_guncelle_loop():
    global fiyat_deposu
    # Yahoo'yu kandırmak için session ve gerçekçi header
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    })

    while True:
        try:
            sistem = veriyi_yukle()
            semboller = list(sistem.get("takip_listesi", {}).keys())
            
            if semboller:
                # Verileri çekmek için yfinance kullanıyoruz
                # Render engeli ihtimaline karşı session ekledik
                for s in semboller:
                    try:
                        ticker = yf.Ticker(s, session=session)
                        # history verisi Render'da fast_info'dan daha kararlı çalışır
                        df = ticker.history(period="1d")
                        if not df.empty:
                            fiyat_deposu[s] = round(float(df['Close'].iloc[-1]), 2)
                        else:
                            # Yahoo başarısızsa Finnhub yedeği
                            url = f"https://finnhub.io/api/v1/quote?symbol={s}&token={FINNHUB_API_KEY}"
                            r = requests.get(url, timeout=5)
                            if r.status_code == 200 and r.json().get('c', 0) > 0:
                                fiyat_deposu[s] = round(float(r.json()['c']), 2)
                    except:
                        continue
                    time.sleep(1.5) # IP ban koruması
        except Exception as e:
            print(f"Borsa döngü hatası: {e}")
        
        time.sleep(60) # Her dakika yenile

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

# --- ROTALAR ---

@app.route('/')
def ana_sayfa():
    return send_file('index.html')

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
            "sembol": sembol.replace(".IS",""), 
            "fiyat": anlik, "hedef": hedef,
            "durum": "AL" if 0 < anlik <= hedef else "BEKLE"
        })
    return jsonify({
        "hisseler": veriler, 
        "portfoyler": s.get("portfoyler", {}), 
        "mesajlar": s.get("mesajlar", [])[-25:]
    })

@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS"): kod += ".IS"
    hedef = float(data.get("hedef", 0))
    s["takip_listesi"][kod] = hedef
    
    # Otomatik sinyal duyurusu
    s["mesajlar"].append({
        "user": "SİSTEM",
        "text": f"📢 YENİ SİNYAL: {kod.replace('.IS','')} paylaşıldı. Hedef: {hedef} TL",
        "time": time.strftime("%H:%M")
    })
    
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
        if full_kod in fiyat_deposu: del fiyat_deposu[full_kod]
        veriyi_kaydet(s)
        return jsonify({"durum": "silindi"})
    return jsonify({"durum": "hata"}), 404

@app.route('/adet-guncelle', methods=['POST'])
def update_amount():
    try:
        data = request.json
        user, hisse = data.get("kullanici"), data.get("hisse", "").upper()
        adet, maliyet = data.get("adet", 0), data.get("maliyet", 0)
        
        sistem = veriyi_yukle()
        if "portfoyler" not in sistem: sistem["portfoyler"] = {}
        if user not in sistem["portfoyler"]: sistem["portfoyler"][user] = {}
        
        sistem["portfoyler"][user][hisse] = {
            "adet": int(adet) if adet else 0,
            "maliyet": float(maliyet) if maliyet else 0.0
        }
        veriyi_kaydet(sistem)
        return jsonify({"durum": "guncellendi"})
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
def send_msg():
    data = request.json
    s = veriyi_yukle()
    s["mesajlar"].append({
        "user": data['user'], 
        "text": data['text'], 
        "time": time.strftime("%H:%M")
    })
    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

@app.route('/excel-indir')
def export_excel():
    s = veriyi_yukle()
    rows = []
    for u, assets in s.get("portfoyler", {}).items():
        for st, info in assets.items():
            cur = fiyat_deposu.get(st + ".IS", 0)
            rows.append({
                "Kullanıcı": u, "Hisse": st, "Adet": info['adet'], 
                "Maliyet": info['maliyet'], "Güncel Fiyat": cur, 
                "Net K/Z": round((cur - info['maliyet']) * info['adet'], 2)
            })
    
    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    
    return send_file(
        output, 
        download_name="Ekip_Portfoy_Raporu.xlsx", 
        as_attachment=True
    )

if __name__ == '__main__':
    # Render'ın beklediği port ayarı
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)