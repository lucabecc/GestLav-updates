from flask import Flask, render_template, request, jsonify
import sqlite3
import os
from datetime import datetime
import win32print
import serial
import socket 
import time
import urllib.request
import shutil 

app = Flask(__name__)

# --- CONFIGURAZIONE ---
SEDE = "FALCONARA"
DB_NAME = "lavanderia.db"
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # Cartella corrente sicura

# --- CONFIGURAZIONE AGGIORNAMENTI ---
GITHUB_USER = "lucabecc" 
GITHUB_REPO = f"https://raw.githubusercontent.com/{GITHUB_USER}/GestLav-updates/main/"

FESTIVITA = [
    "01-01", "06-01", "25-04", "01-05", "02-06", "15-08", "01-11", "08-12", "25-12", "26-12",
    "2025-08-10", "2025-08-11", "2025-08-12" 
]

# LISTINO BASE
LISTINO_DEFAULT = [
    ("ABBIGLIAMENTO", "Camicia", 5.00),
    ("ABBIGLIAMENTO", "Pantalone", 7.00),
    ("ABBIGLIAMENTO", "Giacca", 10.00),
    ("ABBIGLIAMENTO", "Completo Uomo", 17.00),
    ("ABBIGLIAMENTO", "Gonna", 6.00),
    ("ABBIGLIAMENTO", "Cappotto", 15.00),
    ("ABBIGLIAMENTO", "Impermeabile", 16.00),
    ("ABBIGLIAMENTO", "Maglione", 6.00),
    ("CASA & LETTO", "Piumone Singolo", 25.00),
    ("CASA & LETTO", "Piumone Matrim.", 30.00),
    ("CASA & LETTO", "Trapunta Sing.", 22.00),
    ("CASA & LETTO", "Trapunta Matr.", 28.00),
    ("CASA & LETTO", "Copriletto", 15.00),
    ("CASA & LETTO", "Lenzuolo", 4.00),
    ("CASA & LETTO", "Federa", 2.00),
    ("CASA & LETTO", "Tappeto (al kg)", 7.00),
    ("LAVORO", "Tuta da Lavoro", 9.00),
    ("LAVORO", "Giacca Lavoro", 8.00),
    ("LAVORO", "Pantalone Lavoro", 7.00),
    ("LAVORO", "Camice Medico", 6.00),
    ("PRODOTTI VENDITA", "Detersivo sfuso", 3.50),
    ("PRODOTTI VENDITA", "Ammorbidente", 4.00),
    ("PRODOTTI VENDITA", "Profumatore", 6.00),
    ("PRODOTTI VENDITA", "Grucce (10pz)", 2.50),
    ("PRODOTTI VENDITA", "Sacchi Custodia", 1.00)
]

def get_db():
    conn = sqlite3.connect(os.path.join(BASE_DIR, DB_NAME))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    if not os.path.exists(os.path.join(BASE_DIR, DB_NAME)):
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS clienti (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            nome TEXT NOT NULL, 
            cognome TEXT, 
            telefono TEXT, 
            indirizzo TEXT, 
            citta TEXT, 
            cap TEXT, 
            data_nascita TEXT, 
            note TEXT)''')
            
        cursor.execute('''CREATE TABLE IF NOT EXISTS ordini (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            num_scontrino INTEGER, 
            cliente_id INTEGER, 
            data_ingresso TIMESTAMP, 
            data_ritiro TEXT, 
            totale REAL, 
            sconto REAL DEFAULT 0, 
            pagato INTEGER DEFAULT 0, 
            acconto REAL DEFAULT 0, 
            fiscale_emesso INTEGER DEFAULT 0, 
            fiscale_desk INTEGER DEFAULT 0, 
            metodo_pagamento TEXT, 
            stato TEXT DEFAULT 'In Lavorazione', 
            sede TEXT)''')
            
        cursor.execute('''CREATE TABLE IF NOT EXISTS dettagli_ordine (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            ordine_id INTEGER, 
            capo TEXT, 
            prezzo REAL, 
            ritirato INTEGER DEFAULT 0, 
            stato_lavorazione INTEGER DEFAULT 0, 
            numero_catena TEXT DEFAULT '')''')
            
        cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
            chiave TEXT PRIMARY KEY, 
            valore TEXT)''')
            
        cursor.execute('''CREATE TABLE IF NOT EXISTS listino (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            categoria TEXT, 
            capo TEXT, 
            prezzo REAL)''')
        
        defaults = [
            ("printer_star", "Star TSP100 Cutter (TSP143)"), 
            ("port_labels", "COM1"), 
            ("ip_fiscal", "192.168.1.8"), 
            ("fiscal_always", "0"), 
            ("last_reset_date", "2000-01-01 00:00:00")
        ]
        cursor.executemany("INSERT OR IGNORE INTO settings (chiave, valore) VALUES (?, ?)", defaults)
        
        cursor.execute("INSERT OR IGNORE INTO clienti (id, nome, cognome, telefono, citta) VALUES (1, 'CLIENTE', 'AL BANCO', '', '')")
        
        cursor.execute("SELECT COUNT(*) FROM listino")
        if cursor.fetchone()[0] == 0:
            cursor.executemany("INSERT INTO listino (categoria, capo, prezzo) VALUES (?, ?, ?)", LISTINO_DEFAULT)
        
        conn.commit()
        conn.close()
        
    v_file = os.path.join(BASE_DIR, "version.txt")
    if not os.path.exists(v_file):
        with open(v_file, "w") as f:
            f.write("1.0")

def get_setting(chiave):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT valore FROM settings WHERE chiave = ?", (chiave,))
    res = cursor.fetchone()
    conn.close()
    return res['valore'] if res else ""

def set_setting(chiave, valore):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (chiave, valore) VALUES (?, ?)", (chiave, valore))
    conn.commit()
    conn.close()

def get_listino_dict():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM listino ORDER BY categoria, capo")
    rows = cursor.fetchall()
    conn.close()
    
    listino_dict = {}
    for row in rows:
        cat = row['categoria']
        nome = row['capo']
        prezzo = row['prezzo']
        if cat not in listino_dict:
            listino_dict[cat] = {}
        listino_dict[cat][nome] = prezzo
    return listino_dict

# --- CHIUSURA FISCALE ---
def esegui_chiusura_fiscale():
    ip = get_setting("ip_fiscal")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((ip, 9100))
        s.send(b"\x18")
        time.sleep(0.5)
        s.send(b"1F\r\n")
        time.sleep(1)
        s.close()
        return True, "Chiusura Inviata!"
    except Exception as e:
        return False, str(e)

# --- STAMPE (IBRIDA: COM vs WINDOWS) ---
def stampa_etichette(num_visibile, carrello, cliente_nome, data_ritiro_str):
    porta_o_stampante = get_setting("port_labels")
    
    listino_vendita = get_listino_dict().get("PRODOTTI VENDITA", {})
    capi = [x for x in carrello if x['nome'] not in listino_vendita]
    tot = len(capi)
    
    if tot == 0:
        return True

    if porta_o_stampante.upper().startswith("COM"):
        try:
            ser = serial.Serial(porta_o_stampante, 9600, timeout=1)
            ESC = b'\x1b'
            CUT = b'\x1d' + b'V' + b'\x42' + b'\x00'
            TXT_BIG = b'\x1b' + b'!' + b'\x31'
            ser.write(ESC + b'@')
            i = 1
            for item in capi:
                testo = TXT_BIG + f"ORD:{num_visibile} R:{data_ritiro_str}\n".encode() + f"{cliente_nome[:12].upper()}\n".encode() + f"{i}/{tot} {item['nome'][:12]}\n".encode() + CUT
                ser.write(testo)
                i += 1  
            ser.close()
            return True
        except:
            return False
    else:
        try:
            hPrinter = win32print.OpenPrinter(porta_o_stampante)
            try:
                hJob = win32print.StartDocPrinter(hPrinter, 1, ("Etichette", None, "RAW"))
                win32print.StartPagePrinter(hPrinter)
                
                ESC = b'\x1b'
                CUT = b'\x1d' + b'V' + b'\x42' + b'\x00'
                TXT_BIG = b'\x1b' + b'!' + b'\x31'
                
                win32print.WritePrinter(hPrinter, ESC + b'@')
                i = 1
                for item in capi:
                    testo = TXT_BIG + f"ORD:{num_visibile} R:{data_ritiro_str}\n".encode() + f"{cliente_nome[:12].upper()}\n".encode() + f"{i}/{tot} {item['nome'][:12]}\n".encode() + CUT
                    win32print.WritePrinter(hPrinter, testo)
                    i += 1  
                
                win32print.EndPagePrinter(hPrinter)
                win32print.EndDocPrinter(hPrinter)
            finally:
                win32print.ClosePrinter(hPrinter)
            return True
        except:
            return False

def stampa_scontrino(num_visibile, data, cliente_nome, carrello, totale, sconto, acconto, data_ritiro_str, pagato, metodo):
    stampante = get_setting("printer_star")
    try:
        hPrinter = win32print.OpenPrinter(stampante)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("Scontrino", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            
            ESC = b'\x1b'
            CUT = b'\x1b' + b'd' + b'\x02'
            BOLD_ON = b'\x1b' + b'E' + b'\x01'
            BOLD_OFF = b'\x1b' + b'E' + b'\x00'
            ALIGN_CENTER = b'\x1b' + b'a' + b'\x01'
            ALIGN_LEFT = b'\x1b' + b'a' + b'\x00'
            
            def l(txt): return txt.encode() + b"\n"
            def c(txt): return ALIGN_CENTER + txt.encode() + b"\n" + ALIGN_LEFT
            
            testo = c("LAVANDERIA") + c(SEDE) + c("Via Roma, 10 - Tel. 071.xxxxx") + l("-" * 42)
            testo += l(f"Scontrino N.: {num_visibile}") + l(f"Data: {data}") + l(f"Cliente: {cliente_nome[:25]}") + l("-" * 42)
            
            for item in carrello:
                nome = item['nome'][:25]
                prezzo = f"{item['prezzo']:.2f}"
                spazi = " " * (38 - len(nome) - len(prezzo))
                testo += (f"{nome}{spazi}{prezzo}E").encode() + b"\n"
            
            testo += l("-" * 42)
            
            if sconto > 0:
                testo += l(f"SCONTO APPLICATO: -{sconto:.2f} E")
            
            testo += BOLD_ON + c(f"TOTALE: {totale:.2f} E")
            
            if acconto > 0:
                residuo = max(0, totale - acconto)
                testo += l(f"ACCONTO: {acconto:.2f} E ({metodo})")
                if residuo > 0:
                    testo += c(f"DA SALDARE: {residuo:.2f} E")
                else:
                    testo += c("SALDATO")
            else:
                if pagato:
                    testo += c(f"PAGATO ({metodo})")
                else:
                    testo += c("DA PAGARE")
            
            testo += BOLD_OFF + l("") + BOLD_ON + c(f"Ritiro dal: {data_ritiro_str}") + BOLD_OFF + c("Grazie e Arrivederci!") + b"\n\n\n\n\n" + CUT
            
            win32print.WritePrinter(hPrinter, testo)
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        return True
    except:
        return False

def stampa_fiscale(carrello, sconto=0):
    ip = get_setting("ip_fiscal")
    listino_vendita = get_listino_dict().get("PRODOTTI VENDITA", {})
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((ip, 9100))
        
        s.send(b"\x18") 
        time.sleep(0.1) 
        
        totale_lordo = sum(i['prezzo'] for i in carrello)
        
        for item in carrello:
            prezzo_effettivo = item['prezzo']
            if sconto > 0 and totale_lordo > 0:
                quota_sconto = (item['prezzo'] / totale_lordo) * sconto
                prezzo_effettivo = max(0.00, item['prezzo'] - quota_sconto)
            
            reparto = "H2R" if item['nome'] in listino_vendita else "H1R"
            riga = f'"{item["nome"][:20]}" {"{:.2f}".format(prezzo_effettivo)} {reparto}\r\n'
            s.send(riga.encode())
        
        s.send(b"1T\r\n") 
        s.close()
        return True
    except:
        return False

# --- ROTTE API ---
@app.route('/')
def home():
    listino_db = get_listino_dict()
    return render_template('index.html', sede=SEDE, listino=listino_db, festivita=FESTIVITA)

@app.route('/api/get_listino_raw')
def api_get_listino_raw():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM listino ORDER BY categoria, capo")
    items = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(items)

@app.route('/api/save_item_listino', methods=['POST'])
def api_save_item_listino():
    d = request.json
    conn = get_db()
    cursor = conn.cursor()
    
    if 'id' in d and d['id']: 
        cursor.execute("UPDATE listino SET categoria=?, capo=?, prezzo=? WHERE id=?", 
                       (d['categoria'].upper(), d['capo'], d['prezzo'], d['id']))
    else: 
        cursor.execute("INSERT INTO listino (categoria, capo, prezzo) VALUES (?, ?, ?)", 
                       (d['categoria'].upper(), d['capo'], d['prezzo']))
    
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/api/delete_item_listino', methods=['POST'])
def api_delete_item_listino():
    d = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM listino WHERE id=?", (d['id'],))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/api/get_settings')
def api_get_settings():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM settings")
    data = {row['chiave']: row['valore'] for row in cursor.fetchall()}
    conn.close()
    
    # ⚠️ FIX: Controlla cartella backup nel percorso assoluto
    backup_file = os.path.join(BASE_DIR, "backup", "app.py")
    data['has_backup'] = 1 if os.path.exists(backup_file) else 0
    
    return jsonify(data)

@app.route('/api/save_settings', methods=['POST'])
def api_save_settings():
    for k, v in request.json.items():
        set_setting(k, v)
    return jsonify({'status': 'success'})

@app.route('/api/reset_counters', methods=['POST'])
def api_reset_counters():
    set_setting("last_reset_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return jsonify({'status': 'success'})

@app.route('/api/get_system_printers')
def get_system_printers():
    try:
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = [printer[2] for printer in win32print.EnumPrinters(flags)]
        return jsonify(printers)
    except Exception as e:
        return jsonify([])

@app.route('/api/elimina_ordine_definitivo', methods=['POST'])
def elimina_ordine_definitivo():
    num = request.json.get('num_scontrino')
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM ordini WHERE num_scontrino = ?", (num,))
    res = cursor.fetchone()
    if not res:
        conn.close()
        return jsonify({'status': 'error', 'msg': 'Ordine non trovato!'})
    
    ordine_id = res[0]
    cursor.execute("DELETE FROM dettagli_ordine WHERE ordine_id = ?", (ordine_id,))
    cursor.execute("DELETE FROM ordini WHERE id = ?", (ordine_id,))
    
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'msg': f'Ordine {num} eliminato per sempre.'})

@app.route('/api/elimina_capo_definitivo', methods=['POST'])
def elimina_capo_definitivo():
    id_capo = request.json.get('id_capo')
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT ordine_id FROM dettagli_ordine WHERE id = ?", (id_capo,))
    res = cursor.fetchone()
    
    if not res:
        conn.close()
        return jsonify({'status': 'error', 'msg': 'Codice Capo non trovato!'})
    
    ordine_id = res[0]
    cursor.execute("DELETE FROM dettagli_ordine WHERE id = ?", (id_capo,))
    
    cursor.execute("SELECT SUM(prezzo) FROM dettagli_ordine WHERE ordine_id = ?", (ordine_id,))
    nuovo_totale = cursor.fetchone()[0]
    if nuovo_totale is None:
        nuovo_totale = 0.0
        
    cursor.execute("UPDATE ordini SET totale = ? WHERE id = ?", (nuovo_totale, ordine_id))
    
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'msg': f'Capo {id_capo} eliminato. Totale ordine aggiornato.'})

@app.route('/api/carico_lavoro')
def api_carico_lavoro():
    conn = get_db()
    cursor = conn.cursor()
    sql = """SELECT o.data_ritiro, COUNT(d.id) as num_capi 
             FROM ordini o JOIN dettagli_ordine d ON o.id = d.ordine_id 
             WHERE d.ritirato = 0 AND o.stato != 'Consegnato' 
             GROUP BY o.data_ritiro"""
    cursor.execute(sql)
    dati = {row['data_ritiro']: row['num_capi'] for row in cursor.fetchall()}
    conn.close()
    return jsonify(dati)

@app.route('/esegui_chiusura', methods=['POST'])
def esegui_chiusura():
    successo, msg = esegui_chiusura_fiscale()
    return jsonify({'status': 'success' if successo else 'error', 'msg': msg})

@app.route('/cerca_cliente')
def cerca_cliente():
    q = request.args.get('q', '')
    if len(q) < 2: return jsonify([])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clienti WHERE (nome LIKE ? OR cognome LIKE ? OR telefono LIKE ?) AND id != 1", ('%'+q+'%', '%'+q+'%', '%'+q+'%'))
    items = [dict(row, nome_completo=f"{row['nome']} {row['cognome'] or ''}".strip()) for row in cursor.fetchall()]
    conn.close()
    return jsonify(items)

@app.route('/get_cliente_rapido')
def get_cliente_rapido():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clienti WHERE id = 1")
    c = dict(cursor.fetchone())
    c['nome_completo'] = "CLIENTE AL BANCO"
    conn.close()
    return jsonify(c)

@app.route('/crea_cliente', methods=['POST'])
def crea_cliente():
    d = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO clienti (nome, cognome, telefono, indirizzo, citta, cap, data_nascita) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                   (d.get('nome','').upper(), d.get('cognome','').upper(), d.get('telefono',''), d.get('indirizzo',''), d.get('citta',''), d.get('cap',''), d.get('data_nascita','')))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({'status': 'success', 'id': new_id, 'nome': f"{d.get('nome','')} {d.get('cognome','')}".strip(), 'telefono': d.get('telefono','')})

@app.route('/cerca_ordini_aperti')
def cerca_ordini_aperti():
    q = request.args.get('q', '')
    cliente_id = request.args.get('cliente_id', '')
    conn = get_db()
    cursor = conn.cursor()
    sql = """SELECT DISTINCT o.id, o.num_scontrino, o.data_ingresso, o.data_ritiro, o.totale, o.acconto, o.pagato, o.fiscale_emesso, o.fiscale_desk, c.nome, c.cognome, c.telefono,
        (SELECT COUNT(*) FROM dettagli_ordine WHERE ordine_id = o.id AND stato_lavorazione = 0) as non_pronti,
        (SELECT GROUP_CONCAT(DISTINCT numero_catena) FROM dettagli_ordine WHERE ordine_id = o.id AND numero_catena != '') as posizioni
        FROM ordini o 
        JOIN clienti c ON o.cliente_id = c.id 
        LEFT JOIN dettagli_ordine d ON o.id = d.ordine_id
        WHERE o.stato != 'Consegnato' AND o.stato != 'Sospeso'"""
    
    conditions = []
    if cliente_id:
        conditions.append(f"o.cliente_id = {cliente_id}")
    elif q.isdigit():
        conditions.append(f"o.num_scontrino = {q}")
    else:
        if not q and not cliente_id:
            conditions.append("1=0") 

    if conditions:
        sql += " AND " + " AND ".join(conditions)
    
    sql += " ORDER BY o.id DESC"
    cursor.execute(sql)
    items = []
    for row in cursor.fetchall():
        d = dict(row)
        d['cliente_nome'] = f"{d['nome']} {d['cognome'] or ''}".strip()
        d['residuo'] = max(0, d['totale'] - (d['acconto'] or 0))
        d['tutto_pronto'] = (d['non_pronti'] == 0)
        items.append(d)
    conn.close()
    return jsonify(items)

@app.route('/get_dettagli_ordine/<int:ordine_id>')
def get_dettagli_ordine(ordine_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT totale, acconto, fiscale_emesso, fiscale_desk FROM ordini WHERE id = ?", (ordine_id,))
    res = cursor.fetchone()
    info = {'totale_ordine': res[0], 'totale_versato': res[1], 'fiscale_emesso': res[2], 'fiscale_desk': res[3]}
    cursor.execute("SELECT id, capo, prezzo, ritirato, stato_lavorazione, numero_catena FROM dettagli_ordine WHERE ordine_id = ?", (ordine_id,))
    capi = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'capi': capi, 'info': info})

@app.route('/consegna_items', methods=['POST'])
def consegna_items():
    ids = request.json.get('ids', [])
    incasso = float(request.json.get('incasso', 0))
    sconto_extra = float(request.json.get('sconto_extra', 0))
    richiesta_fiscale = request.json.get('stampa_fiscale', False)
    conn = get_db()
    cursor = conn.cursor()
    capi_ritirati = []
    
    for item_id in ids:
        cursor.execute("UPDATE dettagli_ordine SET ritirato = 1 WHERE id = ?", (item_id,))
        cursor.execute("SELECT capo as nome, prezzo FROM dettagli_ordine WHERE id = ?", (item_id,))
        capi_ritirati.append(dict(cursor.fetchone()))
    
    msg = "Nessuna stampa."
    if ids:
        cursor.execute("SELECT ordine_id FROM dettagli_ordine WHERE id = ?", (ids[0],))
        ordine_id = cursor.fetchone()[0]
        
        if sconto_extra > 0:
            cursor.execute("UPDATE ordini SET sconto = sconto + ?, totale = totale - ? WHERE id = ?", (sconto_extra, sconto_extra, ordine_id))
        
        if incasso > 0:
            cursor.execute("UPDATE ordini SET acconto = acconto + ? WHERE id = ?", (incasso, ordine_id))
        
        cursor.execute("SELECT totale, acconto FROM ordini WHERE id = ?", (ordine_id,))
        r = cursor.fetchone()
        if r['acconto'] >= r['totale'] - 0.01:
            cursor.execute("UPDATE ordini SET pagato = 1 WHERE id = ?", (ordine_id,))
        
        cursor.execute("SELECT COUNT(*) FROM dettagli_ordine WHERE ordine_id = ? AND ritirato = 0", (ordine_id,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("UPDATE ordini SET stato = 'Consegnato' WHERE id = ?", (ordine_id,))
        
        if richiesta_fiscale:
            stampa_fiscale(capi_ritirati, sconto=sconto_extra)
            cursor.execute("UPDATE ordini SET fiscale_emesso = 1 WHERE id = ?", (ordine_id,))
            msg = "✅ Scontrino Fiscale Stampato!"
            
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'msg': msg})

@app.route('/salva_ordine', methods=['POST'])
def salva_ordine():
    d = request.json
    listino_vendita = get_listino_dict().get("PRODOTTI VENDITA", {})
    
    carrello, data_ritiro_raw = d['carrello'], d['data_ritiro']
    sconto, acconto = float(d.get('sconto', 0)), float(d.get('acconto', 0))
    pagato, metodo = d['pagato'], d['metodo']
    
    dt_obj = datetime.strptime(data_ritiro_raw, "%Y-%m-%d") if "-" in data_ritiro_raw and len(data_ritiro_raw.split("-")[0])==4 else datetime.now()
    data_ritiro_str = dt_obj.strftime("%d/%m") if "-" in data_ritiro_raw else data_ritiro_raw
    
    totale = max(0, sum(i['prezzo'] for i in carrello) - sconto)
    
    solo_prodotti = all(i['nome'] in listino_vendita for i in carrello)
    
    if acconto >= totale: pagato = True
    else: pagato = False
    if solo_prodotti: pagato = True; metodo = metodo or "Contanti"

    conn = get_db()
    cursor = conn.cursor()
    last_reset = get_setting("last_reset_date")
    cursor.execute("SELECT COUNT(*) FROM ordini WHERE data_ingresso > ?", (last_reset,))
    nuovo_num = cursor.fetchone()[0] + 1
    
    stampa_ora = False
    contiene_prodotti = any(i['nome'] in listino_vendita for i in carrello)
    fiscal_always = get_setting("fiscal_always") == "1"
    if (pagato and metodo == 'Carta') or contiene_prodotti or fiscal_always: stampa_ora = True
    
    fiscale_desk_val = 1 if stampa_ora else 0
    
    cursor.execute("INSERT INTO ordini (num_scontrino, cliente_id, data_ingresso, data_ritiro, totale, sconto, acconto, pagato, fiscale_emesso, fiscale_desk, metodo_pagamento, sede, stato) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                   (nuovo_num, d['cliente_id'], datetime.now(), data_ritiro_str, totale, sconto, acconto, 1 if pagato else 0, 1 if stampa_ora else 0, fiscale_desk_val, metodo, SEDE, 'Consegnato' if solo_prodotti else 'In Lavorazione'))
    oid = cursor.lastrowid
    
    stato_lavorazione = 1 if solo_prodotti else 0
    for i in carrello:
        cursor.execute("INSERT INTO dettagli_ordine (ordine_id, capo, prezzo, ritirato, stato_lavorazione) VALUES (?, ?, ?, ?, ?)", (oid, i['nome'], i['prezzo'], 0 if not solo_prodotti else 1, stato_lavorazione))
    conn.commit()
    conn.close()

    if not solo_prodotti:
        stampa_scontrino(nuovo_num, datetime.now().strftime("%d/%m %H:%M"), d['cliente_nome'], carrello, totale, sconto, acconto, data_ritiro_str, pagato, metodo)
        stampa_etichette(nuovo_num, carrello, d['cliente_nome'], data_ritiro_str)
    
    if stampa_ora:
        stampa_fiscale(carrello, sconto)
    
    return jsonify({"status": "success", "id_ordine": oid})

@app.route('/sospendi_ordine', methods=['POST'])
def sospendi_ordine():
    d=request.json
    conn=get_db()
    cursor=conn.cursor()
    cursor.execute("INSERT INTO ordini (cliente_id, data_ingresso, data_ritiro, totale, stato, sede) VALUES (?, ?, ?, ?, 'Sospeso', ?)", (d['cliente_id'], datetime.now(), d['data_ritiro'], 0, SEDE))
    oid=cursor.lastrowid
    for i in d['carrello']: 
        cursor.execute("INSERT INTO dettagli_ordine (ordine_id, capo, prezzo) VALUES (?, ?, ?)", (oid, i['nome'], i['prezzo']))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/recupera_sospesi')
def recupera_sospesi():
    conn=get_db()
    cursor=conn.cursor()
    cursor.execute("SELECT o.id, c.nome, c.cognome, o.data_ingresso FROM ordini o JOIN clienti c ON o.cliente_id = c.id WHERE o.stato = 'Sospeso' ORDER BY o.id DESC")
    r=[dict(x) for x in cursor.fetchall()]
    conn.close()
    return jsonify(r)

@app.route('/carica_sospeso', methods=['POST'])
def carica_sospeso():
    oid=request.json['id']
    conn=get_db()
    cursor=conn.cursor()
    cursor.execute("SELECT * FROM ordini WHERE id=?",(oid,))
    o=dict(cursor.fetchone())
    cursor.execute("SELECT * FROM clienti WHERE id=?",(o['cliente_id'],))
    c=dict(cursor.fetchone())
    c['nome_completo']=f"{c['nome']} {c['cognome'] or ''}".strip()
    cursor.execute("SELECT capo as nome, prezzo FROM dettagli_ordine WHERE ordine_id=?",(oid,))
    l=[dict(x) for x in cursor.fetchall()]
    cursor.execute("DELETE FROM dettagli_ordine WHERE ordine_id=?",(oid,))
    cursor.execute("DELETE FROM ordini WHERE id=?",(oid,))
    conn.commit()
    conn.close()
    return jsonify({'cliente':c, 'carrello':l, 'data_ritiro':o['data_ritiro']})

@app.route('/get_items_scontrino', methods=['POST'])
def get_items_scontrino():
    tipo = request.json.get('tipo') 
    valore = request.json.get('valore')
    
    conn = get_db()
    cursor = conn.cursor()
    
    target_item_id = None
    order_id = None
    
    if tipo == 'ordine':
        cursor.execute("SELECT id FROM ordini WHERE num_scontrino = ? AND stato != 'Consegnato'", (valore,))
        res = cursor.fetchone()
        if res:
            order_id = res[0]
    
    elif tipo == 'capo':
        cursor.execute("SELECT ordine_id FROM dettagli_ordine WHERE id = ? AND ritirato = 0", (valore,))
        res = cursor.fetchone()
        if res:
            order_id = res[0]
            target_item_id = int(valore)
            
    if not order_id:
        conn.close()
        return jsonify({'status': 'error', 'msg': 'Nessun risultato trovato.'})
        
    cursor.execute("SELECT id, capo, stato_lavorazione, numero_catena FROM dettagli_ordine WHERE ordine_id = ?", (order_id,))
    capi = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({
        'status': 'success', 
        'items': capi, 
        'ordine_id': order_id, 
        'target_item_id': target_item_id
    })

@app.route('/conferma_pronti', methods=['POST'])
def conferma_pronti():
    ids = request.json.get('ids', [])
    oid = request.json.get('ordine_id')
    catena = request.json.get('catena', '')
    conn = get_db()
    cursor = conn.cursor()
    if ids:
        pl = ','.join(['?']*len(ids))
        cursor.execute(f"UPDATE dettagli_ordine SET stato_lavorazione = 0, numero_catena = '' WHERE ordine_id = ? AND id NOT IN ({pl})", [oid] + ids)
        cursor.execute(f"UPDATE dettagli_ordine SET stato_lavorazione = 1, numero_catena = ? WHERE id IN ({pl})", [catena] + ids)
    else:
        cursor.execute("UPDATE dettagli_ordine SET stato_lavorazione = 0, numero_catena = '' WHERE ordine_id = ?", (oid,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@app.route('/modifica_capo_ordine', methods=['POST'])
def modifica_capo_ordine():
    d = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE dettagli_ordine SET capo = ?, prezzo = ? WHERE id = ?", (d['nome'], d['prezzo'], d['id']))
    cursor.execute("SELECT SUM(prezzo) FROM dettagli_ordine WHERE ordine_id = (SELECT ordine_id FROM dettagli_ordine WHERE id = ?)", (d['id'],))
    nuovo_tot = cursor.fetchone()[0]
    cursor.execute("UPDATE ordini SET totale = ? WHERE id = (SELECT ordine_id FROM dettagli_ordine WHERE id = ?)", (nuovo_tot, d['id']))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

# --- SISTEMA AGGIORNAMENTO CON BACKUP ---
@app.route('/api/check_update')
def check_update():
    try:
        # Legge versione locale con percorso assoluto
        v_file = os.path.join(BASE_DIR, "version.txt")
        with open(v_file, "r") as f:
            local_ver = f.read().strip()
            
        remote_url = GITHUB_REPO + "version.txt"
        with urllib.request.urlopen(remote_url) as response:
            remote_ver = response.read().decode('utf-8').strip()
        
        # Verifica se esiste un backup
        backup_path = os.path.join(BASE_DIR, "backup", "app.py")
        has_backup = os.path.exists(backup_path)
        
        if remote_ver != local_ver:
            return jsonify({'update_available': True, 'local': local_ver, 'remote': remote_ver, 'has_backup': has_backup})
        return jsonify({'update_available': False, 'has_backup': has_backup})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/perform_update', methods=['POST'])
def perform_update():
    try:
        backup_dir = os.path.join(BASE_DIR, "backup")
        templates_dir = os.path.join(BASE_DIR, "templates")
        
        # 1. CREA BACKUP DEI FILE ATTUALI
        if not os.path.exists(backup_dir): os.makedirs(backup_dir)
        
        shutil.copy(os.path.join(BASE_DIR, "app.py"), os.path.join(backup_dir, "app.py"))
        
        if os.path.exists(os.path.join(templates_dir, "index.html")):
            shutil.copy(os.path.join(templates_dir, "index.html"), os.path.join(backup_dir, "index.html"))
            
        if os.path.exists(os.path.join(BASE_DIR, "version.txt")):
            shutil.copy(os.path.join(BASE_DIR, "version.txt"), os.path.join(backup_dir, "version.txt"))
        
        # 2. SCARICA I NUOVI FILE
        urllib.request.urlretrieve(GITHUB_REPO + "app.py", os.path.join(BASE_DIR, "app.py"))
        
        if not os.path.exists(templates_dir): os.makedirs(templates_dir)
        urllib.request.urlretrieve(GITHUB_REPO + "templates/index.html", os.path.join(templates_dir, "index.html"))
        
        urllib.request.urlretrieve(GITHUB_REPO + "version.txt", os.path.join(BASE_DIR, "version.txt"))
        
        return jsonify({'status': 'success', 'msg': 'Aggiornamento completato! (Backup salvato)'})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)})

@app.route('/api/restore_backup', methods=['POST'])
def restore_backup():
    try:
        backup_dir = os.path.join(BASE_DIR, "backup")
        templates_dir = os.path.join(BASE_DIR, "templates")
        
        if not os.path.exists(os.path.join(backup_dir, "app.py")): 
            return jsonify({'status':'error', 'msg':'Nessun backup trovato'})
        
        # Ripristina i file dalla cartella backup
        shutil.copy(os.path.join(backup_dir, "app.py"), os.path.join(BASE_DIR, "app.py"))
        
        if os.path.exists(os.path.join(backup_dir, "index.html")):
            shutil.copy(os.path.join(backup_dir, "index.html"), os.path.join(templates_dir, "index.html"))
            
        if os.path.exists(os.path.join(backup_dir, "version.txt")):
            shutil.copy(os.path.join(backup_dir, "version.txt"), os.path.join(BASE_DIR, "version.txt"))
        
        return jsonify({'status':'success', 'msg':'Ripristino completato! Riavvia il programma.'})
    except Exception as e:
        return jsonify({'status':'error', 'msg':str(e)})

if __name__ == '__main__': 
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)