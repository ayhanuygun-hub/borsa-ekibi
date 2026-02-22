import os, threading, time, io, pandas as pd, requests, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime

app = Flask(__name__)
CORS(app)

# --- MONGODB BAĞLANTISI ---
# Render üzerinde donma yapmaması için 'connect=False' kullanıyoruz.
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI, connect=False, serverSelectionTimeoutMS=5000)
db = client['borsa_takip']

# Tablo (Collection) Tanımlamaları
veriler_col = db['veriler']      # Hisseler, Portföyler, Kar-Zarar
chat_col = db['chat_logs']       # Sohbet mesajları
log_col = db['connection_logs']  # Giriş kayıtları

fiyat_deposu = {}

def veriyi_yukle():
    """Veritabanından ana sistem verisini yükler ve eksik alanları tamir eder."""
    try:
        data = veriler_col.find_one({"_id": "sistem_verisi"})
        if not data:
            data = {
                "_id": "sistem_verisi", "yonetici_sifre": "admin123", 
                "kullanicilar": {}, "takip_listesi": {}, 
                "portfoyler": {}, "fiyat_yedek": {}, "realize_kar": {}
            }
            veriler_col.insert_one(data)
        
        # Kritik alan kontrolü (Hata almamak için şart)
        defaults = {
            "kullanicilar": {}, "takip_listesi": {}, "portfoyler": {}, 
            "fiyat_yedek": {}, "realize_kar": {}, "yonetici_sifre": "admin123"
        }
        needs_update = False
        for key, value in defaults.items():
            if key not in data:
                data[key] = value
                needs_update = True
        
        if needs_update:
            veriler_col.replace_one({"_id": "sistem_verisi"}, data)
        return data
    except Exception as e:
        print(f"Veri Yükleme Hatası: {e}")
        return None

def veriyi_kaydet(sistem):
    """Sistem verisini MongoDB'ye yazar."""
    try:
        veriler_col.replace_one({"_id": "sistem_verisi"}, sistem)
    except Exception as e:
        print(f"Veri Kaydetme Hatası: {e}")

# --- FİYAT GÜNCELLEME MOTORU ---
def fiyat_cek_zorla(sembol):
    """Yahoo Finance üzerinden en güncel fiyatı çeker."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        # Önce hızlı API denemesi
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}?interval=1m&range=1d"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return round(float(r.json()['chart']['result'][0]['meta']['regularMarketPrice']), 2)
    except:
        try:
            # Yedek: yfinance kütüphanesi
            t = yf.Ticker(sembol)
            df = t.history(period="1d")
            if not df.empty:
                return round(float(df['Close'].iloc[-1]), 2)
        except: pass
    return 0

def fiyatlari_guncelle_loop():
    """Arka planda fiyatları sürekli güncel tutan döngü."""
    global fiyat_deposu
    while True:
        try:
            s = veriyi_yukle()
            if s:
                semboller = list(s.get("takip_listesi", {}).keys())
                for sembol in semboller:
                    f = fiyat_cek_zorla(sembol)
                    if f > 0:
                        fiyat_deposu[sembol] = f
                        s["fiyat_yedek"][sembol] = f
                    time.sleep(2) # IP Engeli koruması
                veriyi_kaydet(s)
        except Exception as e:
            print(f"Döngü Hatası: {e}")
        time.sleep(60)

# Döngüyü ayrı bir kolda başlat
threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

# --- ROTALAR (API) ---

@app.route('/')
def ana_sayfa():
    return send_file('index.html')

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    s = veriyi_yukle()
    user, sifre, rol = data.get("user"), data.get("sifre"), data.get("rol")
    success = (rol == "yonetici" and sifre == s.get("yonetici_sifre")) or \
              (user in s.get("kullanicilar", {}) and s["kullanicilar"][user] == sifre)
    if success:
        log_col.insert_one({"user": user, "time": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "role": rol})
        return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

@app.route('/borsa-verileri')
def get_data():
    s = veriyi_yukle()
    veriler = []
    for sembol, hedef in s.get("takip_listesi", {}).items():
        anlik = fiyat_deposu.get(sembol) or s.get("fiyat_yedek", {}).get(sembol, 0)
        veriler.append({
            "sembol": sembol.replace(".IS",""), 
            "fiyat": anlik, "hedef": hedef, 
            "durum": "AL" if 0 < anlik <= hedef else "BEKLE"
        })
    return jsonify({
        "hisseler": veriler, 
        "portfoyler": s.get("portfoyler", {}), 
        "kullanicilar": list(s.get("kullanicilar", {}).keys()),
        "realize_kar": s.get("realize_kar", {})
    })

@app.route('/sohbet-getir')
def get_chat():
    mesajlar = list(chat_col.find().sort("_id", -1).limit(40))
    for m in mesajlar: m["_id"] = str(m["_id"])
    return jsonify(mesajlar[::-1])

@app.route('/mesaj-gonder', methods=['POST'])
def send_msg():
    data = request.json
    chat_col.insert_one({"user": data['user'], "text": data['text'], "time": datetime.now().strftime("%H:%M")})
    return jsonify({"durum": "ok"})

# --- İŞLEM KAYIT MANTĞI (Alış/Satış) ---
@app.route('/islem-kaydet', methods=['POST'])
def save_transaction():
    data = request.json
    user = data.get("kullanici")
    hisse = data.get("hisse", "").upper()
    adet = int(data.get("adet", 0))
    fiyat = float(data.get("fiyat", 0))
    tip = data.get("tip") # "ALIS" veya "SATIS"

    s = veriyi_yukle()
    if user not in s["portfoyler"]: s["portfoyler"][user] = {}
    if user not in s["realize_kar"]: s["realize_kar"][user] = 0.0

    current = s["portfoyler"][user].get(hisse, {"adet": 0, "maliyet": 0.0})
    
    if tip == "ALIS":
        yeni_adet = current["adet"] + adet
        yeni_maliyet = ((current["adet"] * current["maliyet"]) + (adet * fiyat)) / yeni_adet
        s["portfoyler"][user][hisse] = {"adet": yeni_adet, "maliyet": round(yeni_maliyet, 4)}
    
    elif tip == "SATIS":
        if adet > current["adet"]: 
            return jsonify({"durum": "hata", "mesaj": "Elinizde yeterli adet yok!"}), 400
        
        kar = (fiyat - current["maliyet"]) * adet
        s["realize_kar"][user] += round(kar, 2)
        
        yeni_adet = current["adet"] - adet
        if yeni_adet <= 0:
            if hisse in s["portfoyler"][user]: del s["portfoyler"][user][hisse]
        else:
            s["portfoyler"][user][hisse]["adet"] = yeni_adet

    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

# --- EXCEL RAPORLAMA (Sayfa Yapısı Düzeltildi) ---
@app.route('/excel-indir')
def export():
    try:
        s = veriyi_yukle()
        
        # Sayfa 1: Mevcut Portföy
        rows_p = []
        for u, assets in s.get("portfoyler", {}).items():
            for st, info in assets.items():
                ticker = st if st.endswith(".IS") else st + ".IS"
                cur = fiyat_deposu.get(ticker, 0) or s.get("fiyat_yedek", {}).get(ticker, 0)
                rows_p.append({
                    "Kullanıcı": u, "Hisse": st.replace(".IS", ""), "Adet": info['adet'], 
                    "Ort. Maliyet": info['maliyet'], "Güncel Fiyat": cur, 
                    "Anlık K/Z": round((cur - info['maliyet']) * info['adet'], 2)
                })
        
        # Sayfa 2: Kar Özeti
        rows_k = []
        for u, kar in s.get("realize_kar", {}).items():
            rows_k.append({"Kullanıcı": u, "Toplam Realize Kâr": round(kar, 2)})

        df1 = pd.DataFrame(rows_p if rows_p else [{"Kullanıcı": "Veri Yok"}])
        df2 = pd.DataFrame(rows_k if rows_k else [{"Kullanıcı": "Veri Yok"}])

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df1.to_excel(writer, sheet_name='Aktif Portföyler', index=False)
            df2.to_excel(writer, sheet_name='Kar-Zarar Özeti', index=False)
        output.seek(0)
        return send_file(output, download_name="Borsa_Ekip_Rapor.xlsx", as_attachment=True)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500

# --- YÖNETİCİ ARAÇLARI ---
@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if "." not in kod and len(kod) <= 5: kod += ".IS"
    s["takip_listesi"][kod] = float(data.get("hedef", 0))
    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

@app.route('/hisse-sil', methods=['POST'])
def delete_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if "." not in kod and len(kod) <= 5: kod += ".IS"
    if kod in s["takip_listesi"]: del s["takip_listesi"][kod]
    veriyi_kaydet(s)
    return jsonify({"durum": "silindi"})

@app.route('/kullanici-sil', methods=['POST'])
def delete_user():
    data = request.json
    s = veriyi_yukle()
    u = data.get("username")
    if u in s["kullanicilar"]:
        del s["kullanicilar"][u]
        if u in s["portfoyler"]: del s["portfoyler"][u]
        veriyi_kaydet(s)
        return jsonify({"durum": "silindi"})
    return jsonify({"durum": "hata"}), 404

@app.route('/loglari-getir')
def get_logs():
    logs = list(log_col.find().sort("_id", -1).limit(40))
    for l in logs: l["_id"] = str(l["_id"])
    return jsonify(logs)

@app.route('/tablo-temizle', methods=['POST'])
def clear_table():
    target = request.json.get("tablo")
    if target == "chat": chat_col.delete_many({})
    elif target == "logs": log_col.delete_many({})
    return jsonify({"durum": "ok"})

@app.route('/kullanici-ekle', methods=['POST'])
def add_user():
    data = request.json
    s = veriyi_yukle()
    s["kullanicilar"][data['username']] = data['password']
    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)