import os, threading, time, io, pandas as pd, requests, yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime

app = Flask(__name__)
CORS(app)

# --- MONGODB ---
MONGO_URI = "mongodb+srv://BorsaTakip_db_user:BrsTkp2026@cluster0.naoqjo9.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI, connect=False)
db = client['borsa_takip']
veriler_col = db['veriler']
chat_col = db['chat_logs']
log_col = db['connection_logs']

fiyat_deposu = {}

def veriyi_yukle():
    data = veriler_col.find_one({"_id": "sistem_verisi"})
    if not data:
        data = {
            "_id": "sistem_verisi", "yonetici_sifre": "admin123", 
            "kullanicilar": {}, "takip_listesi": {}, 
            "portfoyler": {}, "fiyat_yedek": {}, "realize_kar": {}
        }
        veriler_col.insert_one(data)
    # Eksik anahtar tamiri
    for k in ["portfoyler", "realize_kar", "kullanicilar", "takip_listesi", "fiyat_yedek"]:
        if k not in data: data[k] = {}
    return data

def veriyi_kaydet(sistem):
    veriler_col.replace_one({"_id": "sistem_verisi"}, sistem)

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
                time.sleep(2)
            veriyi_kaydet(s)
        except: pass
        time.sleep(60)

threading.Thread(target=fiyatlari_guncelle_loop, daemon=True).start()

# --- ROTALAR ---
@app.route('/')
def ana_sayfa(): return send_file('index.html')

@app.route('/borsa-verileri')
def get_data():
    s = veriyi_yukle()
    mesajlar = list(chat_col.find().sort("_id", -1).limit(40))
    for m in mesajlar: m["_id"] = str(m["_id"])
    veriler = []
    for sembol, hedef in s.get("takip_listesi", {}).items():
        anlik = fiyat_deposu.get(sembol) or s.get("fiyat_yedek", {}).get(sembol, 0)
        veriler.append({"sembol": sembol.replace(".IS",""), "fiyat": anlik, "hedef": hedef, "durum": "AL" if 0 < anlik <= hedef else "BEKLE"})
    return jsonify({
        "hisseler": veriler, 
        "portfoyler": s.get("portfoyler", {}), 
        "kullanicilar": list(s.get("kullanicilar", {}).keys()),
        "realize_kar": s.get("realize_kar", {}),
        "mesajlar": mesajlar[::-1]
    })

@app.route('/islem-kaydet', methods=['POST'])
def save_transaction():
    data = request.json
    user, hisse = data.get("kullanici"), data.get("hisse").upper()
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
        if adet > current["adet"]: return jsonify({"durum": "hata", "mesaj": "Yetersiz adet!"}), 400
        # Kar/Zarar Hesabı: (Satış Fiyatı - Ortalama Maliyet) * Satılan Adet
        kar = (fiyat - current["maliyet"]) * adet
        s["realize_kar"][user] += round(kar, 2)
        
        yeni_adet = current["adet"] - adet
        if yeni_adet == 0:
            del s["portfoyler"][user][hisse]
        else:
            s["portfoyler"][user][hisse]["adet"] = yeni_adet
            # Satış maliyeti değiştirmez

    veriyi_kaydet(s)
    return jsonify({"durum": "ok"})

@app.route('/excel-indir')
def export():
    s = veriyi_yukle()
    
    # Sayfa 1: Mevcut Portföy
    rows_p = []
    for u, assets in s.get("portfoyler", {}).items():
        for st, info in assets.items():
            cur = fiyat_deposu.get(st + ".IS", 0) or s.get("fiyat_yedek", {}).get(st + ".IS", 0)
            rows_p.append({
                "Kullanıcı": u, "Hisse": st, "Adet": info['adet'], 
                "Ort. Maliyet": info['maliyet'], "Güncel Fiyat": cur, 
                "Anlık K/Z": round((cur - info['maliyet']) * info['adet'], 2)
            })
    
    # Sayfa 2: Gerçekleşen Kar Özeti
    rows_k = []
    for u, kar in s.get("realize_kar", {}).items():
        rows_k.append({"Kullanıcı": u, "Toplam Gerçekleşen Kâr/Zarar": kar})

    df1 = pd.DataFrame(rows_p)
    df2 = pd.DataFrame(rows_k)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df1.to_excel(writer, sheet_name='Aktif Portföyler', index=False)
        df2.to_excel(writer, sheet_name='Kar-Zarar Özeti', index=False)
    
    output.seek(0)
    return send_file(output, download_name="Ekip_Finans_Raporu.xlsx", as_attachment=True)

# (DİĞER STANDART ROTALAR: login, mesaj-gonder, hisse-ekle, hisse-sil, kullanici-sil vb. buraya eklenecek)
# Not: Önceki sürümdeki diğer rotaları buraya aynen ekleyiniz.

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)