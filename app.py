import os, threading, time, io, pandas as pd, requests, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime
from bson.objectid import ObjectId

app = Flask(__name__)
CORS(app)

# --- MONGODB ---
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI, connect=False)
db = client['borsa_takip']

veriler_col = db['veriler']
chat_col = db['chat_logs']
log_col = db['connection_logs']
islem_gecmisi_col = db['transactions'] # Yeni: Tüm alım/satım dökümü

fiyat_deposu = {}

def veriyi_yukle():
    data = veriler_col.find_one({"_id": "sistem_verisi"})
    if not data:
        data = {"_id": "sistem_verisi", "yonetici_sifre": "admin123", "kullanicilar": {}, "takip_listesi": {}, "fiyat_yedek": {}}
        veriler_col.insert_one(data)
    return data

# --- PORTFÖY HESAPLAYICI (Kritik: Hatalı giriş silinince burası her şeyi düzeltir) ---
def portfoy_ozeti_hesapla(user):
    transactions = list(islem_gecmisi_col.find({"user": user}))
    portfoy = {}
    toplam_realize_kar = 0.0

    for t in transactions:
        hisse = t["hisse"]
        adet = t["adet"]
        fiyat = t["fiyat"]
        tip = t["tip"]

        if hisse not in portfoy:
            portfoy[hisse] = {"adet": 0, "maliyet": 0.0}

        curr = portfoy[hisse]

        if tip == "ALIS":
            yeni_adet = curr["adet"] + adet
            yeni_maliyet = ((curr["adet"] * curr["maliyet"]) + (adet * fiyat)) / yeni_adet
            portfoy[hisse] = {"adet": yeni_adet, "maliyet": yeni_maliyet}
        
        elif tip == "SATIS":
            kar = (fiyat - curr["maliyet"]) * adet
            toplam_realize_kar += kar
            portfoy[hisse]["adet"] -= adet
            if portfoy[hisse]["adet"] <= 0:
                del portfoy[hisse]

    return portfoy, round(toplam_realize_kar, 2)

# --- FİYAT DÖNGÜSÜ ---
def fiyat_cek_zorla(sembol):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}?interval=1m&range=1d"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return round(float(r.json()['chart']['result'][0]['meta']['regularMarketPrice']), 2)
    except: pass
    return 0

def fiyatlari_guncelle_loop():
    global fiyat_deposu
    while True:
        try:
            s = veriyi_yukle()
            semboller = list(s.get("takip_listesi", {}).keys())
            for sembol in semboller:
                f = fiyat_cek_zorla(sembol)
                if f > 0:
                    fiyat_deposu[sembol] = f
                    s["fiyat_yedek"][sembol] = f
                time.sleep(1.5)
            veriler_col.replace_one({"_id": "sistem_verisi"}, s)
        except: pass
        time.sleep(60)

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

# --- ROTALAR ---
@app.route('/')
def ana_sayfa(): return send_file('index.html')

@app.route('/borsa-verileri')
def get_data():
    s = veriyi_yukle()
    user = request.args.get("user")
    
    # Kullanıcıya özel portföy ve realize karı anlık hesapla
    user_portfoy, user_kar = portfoy_ozeti_hesapla(user) if user else ({}, 0)
    
    # Kullanıcının son 5 işlemini getir (Silme işlemi için)
    gecmis = []
    if user:
        gecmis = list(islem_gecmisi_col.find({"user": user}).sort("_id", -1).limit(5))
        for g in gecmis: g["_id"] = str(g["_id"])

    veriler = []
    for sembol, hedef in s.get("takip_listesi", {}).items():
        anlik = fiyat_deposu.get(sembol) or s.get("fiyat_yedek", {}).get(sembol, 0)
        veriler.append({"sembol": sembol.replace(".IS",""), "fiyat": anlik, "hedef": hedef, "durum": "AL" if 0 < anlik <= hedef else "BEKLE"})
    
    return jsonify({
        "hisseler": veriler, 
        "user_portfoy": user_portfoy,
        "user_kar": user_kar,
        "islem_gecmisi": gecmis,
        "kullanicilar": list(s.get("kullanicilar", {}).keys())
    })

@app.route('/islem-kaydet', methods=['POST'])
def save_trans():
    data = request.json
    data["date"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    islem_gecmisi_col.insert_one(data)
    return jsonify({"durum": "ok"})

@app.route('/islem-sil', methods=['POST'])
def delete_trans():
    islem_id = request.json.get("id")
    islem_gecmisi_col.delete_one({"_id": ObjectId(islem_id)})
    return jsonify({"durum": "silindi"})

@app.route('/excel-indir')
def export():
    # Sayfa 1: Tüm İşlemler
    all_trans = list(islem_gecmisi_col.find().sort("_id", 1))
    df1 = pd.DataFrame(all_trans).drop(columns=['_id'], errors='ignore')
    
    # Sayfa 2: Özet (Kullanıcı Bazlı Kar-Zarar)
    s = veriyi_yukle()
    summary = []
    for u in s.get("kullanicilar", {}).keys():
        _, kar = portfoy_ozeti_hesapla(u)
        summary.append({"Kullanıcı": u, "Toplam Gerçekleşen Kâr/Zarar": kar})
    df2 = pd.DataFrame(summary)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df1.to_excel(writer, sheet_name='Tüm İşlem Kayıtları', index=False)
        df2.to_excel(writer, sheet_name='Kar-Zarar Özeti', index=False)
    output.seek(0)
    return send_file(output, download_name="Ekip_Detayli_Rapor.xlsx", as_attachment=True)

# --- DİĞER ROTALAR (login, chat-getir, mesaj-gonder vb. değişmedi) ---
@app.route('/sohbet-getir')
def get_chat():
    m = list(chat_col.find().sort("_id", -1).limit(40))
    for i in m: i["_id"] = str(i["_id"])
    return jsonify(m[::-1])

@app.route('/mesaj-gonder', methods=['POST'])
def send_msg():
    chat_col.insert_one({"user": request.json['user'], "text": request.json['text'], "time": datetime.now().strftime("%H:%M")})
    return jsonify({"durum": "ok"})

@app.route('/giris-yap', methods=['POST'])
def login():
    data = request.json
    s = veriyi_yukle()
    user, sifre, rol = data.get("user"), data.get("sifre"), data.get("rol")
    success = (rol == "yonetici" and sifre == s.get("yonetici_sifre")) or (user in s.get("kullanicilar", {}) and s["kullanicilar"][user] == sifre)
    if success:
        log_col.insert_one({"user": user, "time": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "role": rol})
        return jsonify({"durum": "basarili"})
    return jsonify({"durum": "hata"}), 401

@app.route('/hisse-ekle', methods=['POST'])
def add_hisse():
    data = request.json
    s = veriyi_yukle()
    kod = data.get("hisse", "").upper().strip()
    if not kod.endswith(".IS") and len(kod) <= 5: kod += ".IS"
    s["takip_listesi"][kod] = float(data.get("hedef", 0))
    veriler_col.replace_one({"_id": "sistem_verisi"}, s)
    chat_col.insert_one({"user": "SİSTEM", "text": f"📢 YENİ SİNYAL: {kod.replace('.IS','')} eklendi.", "time": datetime.now().strftime("%H:%M")})
    return jsonify({"durum": "ok"})

@app.route('/kullanici-ekle', methods=['POST'])
def add_user():
    s = veriyi_yukle()
    s["kullanicilar"][request.json['username']] = request.json['password']
    veriler_col.replace_one({"_id": "sistem_verisi"}, s)
    return jsonify({"durum": "ok"})

@app.route('/kullanici-sil', methods=['POST'])
def delete_user():
    s = veriyi_yukle()
    u = request.json.get("username")
    if u in s["kullanicilar"]: del s["kullanicilar"][u]
    veriler_col.replace_one({"_id": "sistem_verisi"}, s)
    islem_gecmisi_col.delete_many({"user": u}) # Kullanıcı silinince geçmişini de temizle
    return jsonify({"durum": "silindi"})

@app.route('/hisse-sil', methods=['POST'])
def delete_hisse():
    s = veriyi_yukle()
    kod = request.json.get("hisse")
    if not kod.endswith(".IS"): kod += ".IS"
    if kod in s["takip_listesi"]: del s["takip_listesi"][kod]
    veriler_col.replace_one({"_id": "sistem_verisi"}, s)
    return jsonify({"durum": "silindi"})

@app.route('/loglari-getir')
def get_logs():
    l = list(log_col.find().sort("_id", -1).limit(40))
    for i in l: i["_id"] = str(i["_id"])
    return jsonify(l)

@app.route('/tablo-temizle', methods=['POST'])
def clear_table():
    t = request.json.get("tablo")
    if t == "chat": chat_col.delete_many({})
    elif t == "logs": log_col.delete_many({})
    return jsonify({"durum": "ok"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)