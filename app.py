from flask import Flask, render_template, request, jsonify
import sqlite3
import os
from datetime import datetime, timedelta
import win32print
import serial
import socket
import time
import urllib.request
import urllib.error
import urllib.parse
import shutil
import struct
import json
from collections import defaultdict

# --- CONFIGURAZIONE PERCORSI ASSOLUTI ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

app = Flask(__name__, template_folder=TEMPLATE_DIR)

# --- CONFIGURAZIONE ---
SEDE = "MARINA"
DB_NAME = "lavanderia.db"

# --- CONFIGURAZIONE MAGAZZINO REMOTO (ARUBA) ---
REMOTE_URL = "https://www.lavanderiaigea.com/api_magazzino.php" 
REMOTE_SECRET = "WASHIFY_SECURE_KEY" 

# --- CONFIGURAZIONE WHATSAPP ---
PATH_WA_OUT = r"C:\Washify_Whatsapp\Da_Inviare"
PATH_WA_ESITI = r"C:\Washify_Whatsapp\Esiti"

# --- CONFIGURAZIONE AGGIORNAMENTI ---
GITHUB_USER = "lucabecc"
GITHUB_REPO_BASE = f"https://raw.githubusercontent.com/{GITHUB_USER}/GestLav-updates/main/"

FESTIVITA = [
    "01-01", "06-01", "25-04", "01-05", "02-06", "15-08", "01-11", "08-12", "25-12", "26-12",
    "2025-08-10", "2025-08-11", "2025-08-12"
]

LISTINO_DEFAULT = [
    ("ABBIGLIAMENTO", "Camicia", 5.00, "Nastro"), ("ABBIGLIAMENTO", "Pantalone", 7.00, "Nastro"),
    ("ABBIGLIAMENTO", "Giacca", 10.00, "Nastro"), ("ABBIGLIAMENTO", "Completo Uomo", 17.00, "Nastro"),
    ("ABBIGLIAMENTO", "Gonna", 6.00, "Nastro"), ("ABBIGLIAMENTO", "Cappotto", 15.00, "Nastro"),
    ("ABBIGLIAMENTO", "Impermeabile", 16.00, "Nastro"), ("ABBIGLIAMENTO", "Maglione", 6.00, "Nastro"),
    ("CASA & LETTO", "Piumone Singolo", 25.00, "Scaffale"), ("CASA & LETTO", "Piumone Matrim.", 30.00, "Scaffale"),
    ("CASA & LETTO", "Trapunta Sing.", 22.00, "Scaffale"), ("CASA & LETTO", "Trapunta Matr.", 28.00, "Scaffale"),
    ("CASA & LETTO", "Copriletto", 15.00, "Scaffale"), ("CASA & LETTO", "Lenzuolo", 4.00, "Scaffale"),
    ("CASA & LETTO", "Federa", 2.00, "Scaffale"), ("CASA & LETTO", "Tappeto (al kg)", 7.00, "Scaffale"),
    ("LAVORO", "Tuta da Lavoro", 9.00, "Nastro"), ("LAVORO", "Giacca Lavoro", 8.00, "Nastro"),
    ("LAVORO", "Pantalone Lavoro", 7.00, "Nastro"), ("LAVORO", "Camice Medico", 6.00, "Nastro"),
    ("PRODOTTI VENDITA", "Detersivo sfuso", 3.50, "Scaffale"), ("PRODOTTI VENDITA", "Ammorbidente", 4.00, "Scaffale"),
    ("PRODOTTI VENDITA", "Profumatore", 6.00, "Scaffale"), ("PRODOTTI VENDITA", "Grucce (10pz)", 2.50, "Scaffale"),
    ("PRODOTTI VENDITA", "Sacchi Custodia", 1.00, "Scaffale")
]

def get_db():
    db_path = os.path.join(BASE_DIR, DB_NAME)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db_path = os.path.join(BASE_DIR, DB_NAME)
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS clienti (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, cognome TEXT, telefono TEXT, indirizzo TEXT, citta TEXT, cap TEXT, data_nascita TEXT, note TEXT)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS ordini (id INTEGER PRIMARY KEY AUTOINCREMENT, num_scontrino INTEGER, cliente_id INTEGER, data_ingresso TIMESTAMP, data_ritiro TEXT, totale REAL, sconto REAL DEFAULT 0, pagato INTEGER DEFAULT 0, acconto REAL DEFAULT 0, pagamenti_parziali REAL DEFAULT 0, fiscale_emesso INTEGER DEFAULT 0, fiscale_desk INTEGER DEFAULT 0, metodo_pagamento TEXT, stato TEXT DEFAULT 'In Lavorazione', sede TEXT, is_approx_date INTEGER DEFAULT 0)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS dettagli_ordine (id INTEGER PRIMARY KEY AUTOINCREMENT, ordine_id INTEGER, capo TEXT, prezzo REAL, ritirato INTEGER DEFAULT 0, stato_lavorazione INTEGER DEFAULT 0, numero_catena TEXT DEFAULT '')''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (chiave TEXT PRIMARY KEY, valore TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS listino (id INTEGER PRIMARY KEY AUTOINCREMENT, categoria TEXT, capo TEXT, prezzo REAL)''')
    
    # --- AGGIORNAMENTI SCHEMA ---
    try: cursor.execute("SELECT ordine FROM listino LIMIT 1")
    except: cursor.execute("ALTER TABLE listino ADD COLUMN ordine INTEGER DEFAULT 0"); cursor.execute("UPDATE listino SET ordine = id")
        
    try: cursor.execute("SELECT tipo_stoccaggio FROM listino LIMIT 1")
    except: cursor.execute("ALTER TABLE listino ADD COLUMN tipo_stoccaggio TEXT DEFAULT 'Nastro'"); cursor.execute("UPDATE listino SET tipo_stoccaggio = 'Scaffale' WHERE categoria LIKE '%CASA%' OR categoria LIKE '%PRODOTTI%'")

    try: cursor.execute("SELECT tipo_stoccaggio FROM dettagli_ordine LIMIT 1")
    except: cursor.execute("ALTER TABLE dettagli_ordine ADD COLUMN tipo_stoccaggio TEXT DEFAULT 'Nastro'")
    
    try: cursor.execute("SELECT is_approx_date FROM ordini LIMIT 1")
    except: cursor.execute("ALTER TABLE ordini ADD COLUMN is_approx_date INTEGER DEFAULT 0")

    try: cursor.execute("SELECT preferenza_scontrino FROM clienti LIMIT 1")
    except: cursor.execute("ALTER TABLE clienti ADD COLUMN preferenza_scontrino TEXT DEFAULT 'stampa'")

    try: cursor.execute("SELECT codice_lotteria FROM clienti LIMIT 1")
    except: cursor.execute("ALTER TABLE clienti ADD COLUMN codice_lotteria TEXT DEFAULT ''")

    try: cursor.execute("SELECT pagamenti_parziali FROM ordini LIMIT 1")
    except: cursor.execute("ALTER TABLE ordini ADD COLUMN pagamenti_parziali REAL DEFAULT 0")

    try: cursor.execute("SELECT data_ritiro_effettivo FROM dettagli_ordine LIMIT 1")
    except: cursor.execute("ALTER TABLE dettagli_ordine ADD COLUMN data_ritiro_effettivo TEXT DEFAULT ''")

    conn.commit()

    defaults = [
        ("printer_star", "Star TSP100 Cutter (TSP143)"), ("port_labels", "COM1"), ("ip_fiscal", "192.168.1.8"), 
        ("fiscal_always", "0"), ("last_reset_date", "2000-01-01 00:00:00"),
        ("ticket_header", f"LAVANDERIA WASHIFY\n{SEDE}\nVia Roma, 10 - Tel. 071.xxxxx"),
        ("ticket_footer", "Grazie e Arrivederci!"),
        ("print_logo", "0"), ("label_custom_text", SEDE),
        ("font_header", "wide"), ("font_num", "big"), ("font_customer", "big"),
        ("font_items", "norm"), ("font_total", "huge"), ("font_footer", "norm"),
        ("font_label_row1", "huge"), ("font_label_row2", "huge"), 
        ("font_label_row3", "norm"), ("font_label_row4", "norm"), ("label_feed", "12")                      
    ]
    
    for k, v in defaults: cursor.execute("INSERT OR IGNORE INTO settings (chiave, valore) VALUES (?, ?)", (k, v))
    cursor.execute("INSERT OR IGNORE INTO clienti (id, nome, cognome, telefono, citta) VALUES (1, 'CLIENTE', 'AL BANCO', '', '')")
    
    cursor.execute("SELECT COUNT(*) FROM listino")
    if cursor.fetchone()[0] == 0: 
        for idx, item in enumerate(LISTINO_DEFAULT):
            cursor.execute("INSERT INTO listino (categoria, capo, prezzo, tipo_stoccaggio, ordine) VALUES (?, ?, ?, ?, ?)", (item[0], item[1], item[2], item[3], idx))
    
    conn.commit()
    conn.close()
    
    if not os.path.exists(os.path.join(BASE_DIR, "version.txt")):
        with open(os.path.join(BASE_DIR, "version.txt"), "w") as f: f.write("1.0")

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
    cursor.execute("SELECT * FROM listino ORDER BY ordine ASC")
    rows = cursor.fetchall()
    conn.close()
    listino_dict = {}
    for row in rows:
        if row['categoria'] not in listino_dict: listino_dict[row['categoria']] = {}
        listino_dict[row['categoria']][row['capo']] = row['prezzo']
    return listino_dict

# --- GESTIONE STAMPANTE FISCALE (CUSTOM KUBE) ---
def stampa_fiscale_vendita(items, totale, codice_lotteria=""):
    ip = get_setting("ip_fiscal")
    if not ip:
        print("IP Fiscale non configurato.")
        return False
        
    print(f"--- AVVIO STAMPA FISCALE SU {ip} ---")
    
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10) # Timeout leggermente aumentato per sicurezza
        
        try:
            print("1. Connessione...")
            s.connect((ip, 9100))
        except OSError as e:
            print(f"ERRORE CONNESSIONE: {e}")
            return False

        # --- FASE 1: SBLOCCO ---
        print("2. Esecuzione Reset...")
        s.sendall(b'\x1b@') # Reset Hardware
        time.sleep(0.1)
        s.sendall(b'C\r\n') # Clear Tasto C
        time.sleep(0.1) 
        s.sendall(b'1A\r\n') # Annulla Scontrino
        time.sleep(0.5) 

        # --- FASE 2: STAMPA DATI ---
        print("3. Invio Nuovi Dati...")
        
        for item in items:
            nome_raw = str(item['nome']).replace('"', '').replace("'", "").strip()
            desc = nome_raw[:22] 
            if not desc: desc = "Reparto 1"

            try: valore = float(item['prezzo'])
            except: valore = 0.0
            
            if valore < 0.00: continue

            prezzo_str = f"{valore:.2f}"
            
            # Comando Corretto: "Descrizione"PrezzoH1R
            # H = Vendita Reparto, 1 = Num Reparto, R = Registra
            comando = f'"{desc}"{prezzo_str}H1R\r\n'
            
            s.sendall(comando.encode('latin1', errors='ignore'))
            
            # --- MODIFICA CRITICA: PAUSA PER IL BUFFER ---
            # Senza questa pausa, se mandi 10 capi, la stampante si "strozza" e taglia lo scontrino a metà.
            time.sleep(0.15) 

        if codice_lotteria:
            cmd_lotteria = f"C{codice_lotteria.upper()}\r\n"
            s.sendall(cmd_lotteria.encode('latin1'))
            time.sleep(0.15)
        
        # --- FASE 3: CHIUSURA ---
        print("4. Chiusura Scontrino (1T)...")
        s.sendall(b"1T\r\n")
        
        time.sleep(1.0) # Attesa chiusura
        s.close()
        print("--- STAMPA COMPLETATA ---")
        return True
        
    except Exception as e:
        print(f"ERRORE CRITICO STAMPA: {e}")
        if s: 
            try: s.close()
            except: pass
        return False

def esegui_chiusura_fiscale():
    ip = get_setting("ip_fiscal")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((ip, 9100))
        
        s.sendall(b'\x1b@') 
        time.sleep(0.1)
        s.sendall(b'C\r\n') 
        time.sleep(0.1)
        s.sendall(b"1A\r\n") 
        time.sleep(0.5)
        
        s.sendall(b"1F\r\n") # Chiusura
        time.sleep(2.0)
        
        s.close()
        return True, "Chiusura Inviata!"
    except Exception as e: return False, f"Errore: {str(e)}"

# --- FONTS ---
def get_star_font(size_name):
    if size_name == "huge": return b'\x1bW\x02\x1bh\x02' 
    if size_name == "big":  return b'\x1bW\x01\x1bh\x01' 
    if size_name == "wide": return b'\x1bW\x01\x1bh\x00' 
    if size_name == "high": return b'\x1bW\x00\x1bh\x01' 
    return b'\x1bW\x00\x1bh\x00' 

def get_label_font_command(size_name):
    if size_name == "huge" or size_name == "big": return b'\x1b!\x30' 
    if size_name == "wide": return b'\x1b!\x20' 
    if size_name == "high": return b'\x1b!\x10' 
    return b'\x1b!\x00' 

# --- STAMPE ---
def stampa_etichette(num_visibile, items_con_id, cliente_nome, data_ritiro_str):
    porta = get_setting("port_labels")
    listino_vendita = get_listino_dict().get("PRODOTTI VENDITA", {})
    is_singola = len(items_con_id) == 1
    capi = [x for x in items_con_id if is_singola or x['nome'] not in listino_vendita]
    
    tot = len(capi)
    if tot == 0: return True
    
    f_row1 = get_label_font_command(get_setting("font_label_row1") or "huge")
    f_row2 = get_label_font_command(get_setting("font_label_row2") or "huge")
    f_row3 = get_label_font_command(get_setting("font_label_row3") or "norm")
    
    try: feed_lines = int(get_setting("label_feed") or 12)
    except: feed_lines = 12
    
    CMD_CUT = b'\x1bi'; SPACE_COMPACT = b'\x1b3\x12'; BOLD_ON = b'\x1bE\x01'; BOLD_OFF = b'\x1bE\x00'; ALIGN_LEFT = b'\x1ba\x00'; CP_PC858 = b'\x1bt\x13'

    try:
        def enc(t): return t.encode('cp858', errors='replace')
        if porta.upper().startswith("COM"):
            h = serial.Serial(porta, 9600, timeout=1)
            def write(b): h.write(b)
            def close(): h.close()
        else:
            h = win32print.OpenPrinter(porta)
            job = win32print.StartDocPrinter(h, 1, ("Etichette", None, "RAW")); win32print.StartPagePrinter(h)
            def write(b): win32print.WritePrinter(h, b)
            def close(): win32print.EndPagePrinter(h); win32print.EndDocPrinter(h); win32print.ClosePrinter(h)

        write(CP_PC858); write(ALIGN_LEFT); write(SPACE_COMPACT)
        for i, item in enumerate(capi, 1):
            write(b'\n') 
            riga1 = f"ORD:{num_visibile} ID:{item['id']}"
            if get_setting("font_label_row1") == "huge": write(f_row1 + enc(f"{riga1}\n"))
            else: write(f_row1 + BOLD_ON + enc(f"{riga1}\n") + BOLD_OFF)
            write(f_row2 + BOLD_ON + enc(f"{cliente_nome[:15].upper()}\n") + BOLD_OFF)
            
            riga_capo = f"{item['nome'][:18]}"
            if not is_singola: riga_capo += f" ({i}/{tot})"
            
            riga_completa = f"{riga_capo} R:{data_ritiro_str}"
            write(f_row3 + BOLD_ON + enc(f"{riga_completa}\n") + BOLD_OFF)
            write(b'\x1bd' + bytes([feed_lines])); write(CMD_CUT)
        close()
        return True
    except Exception as e: return False

def stampa_scontrino(num_visibile, data, cliente_nome, carrello, totale, sconto, acconto, data_ritiro_str, pagato, metodo, is_approx_date=False, codice_lotteria=""):
    stampante = get_setting("printer_star")
    header_text = get_setting("ticket_header")
    footer_text = get_setting("ticket_footer")
    print_logo = get_setting("print_logo") == "1"
    
    stringa_data_ritiro_stampa = "Data Approssimativa 30 Giorni" if is_approx_date else f"Ritiro dal: {data_ritiro_str}"
    
    S_HEAD = get_star_font(get_setting("font_header") or "wide")
    S_NUM  = get_star_font(get_setting("font_num") or "big")
    S_CUST = get_star_font(get_setting("font_customer") or "big")
    S_ITEM = get_star_font(get_setting("font_items") or "norm")
    S_TOT  = get_star_font(get_setting("font_total") or "huge") 
    S_FOOT = get_star_font(get_setting("font_footer") or "norm")
    S_NORM = get_star_font("norm"); S_WIDE = get_star_font("wide")

    try:
        hPrinter = win32print.OpenPrinter(stampante)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("Scontrino", None, "RAW")); win32print.StartPagePrinter(hPrinter)
            ALIGN_CENTER = b'\x1b\x1d\x61\x01'; ALIGN_LEFT = b'\x1b\x1d\x61\x00'
            BOLD_ON = b'\x1bE'; BOLD_OFF = b'\x1bF'; CUT = b'\x1bd\x02'; CMD_LOGO = b'\x1b\x1c\x70\x01\x00\r\n'; CMD_CP858 = b'\x1b\x1d\x74\x04'; EURO = b'\xd5' 
            def enc(txt): return txt.encode('cp858', errors='replace')
            buffer = b'\x1b@' + CMD_CP858 
            if print_logo: buffer += ALIGN_CENTER + CMD_LOGO + ALIGN_LEFT
            buffer += ALIGN_CENTER + S_HEAD + BOLD_ON
            if header_text:
                for r in header_text.split('\n'): buffer += enc(r) + b"\n"
            buffer += BOLD_OFF + S_NORM + ALIGN_LEFT + b"-" * 42 + b"\n"
            buffer += ALIGN_CENTER + S_NUM + BOLD_ON + enc(f"{num_visibile}\n") + BOLD_OFF + S_NORM + enc(f"Data: {data}\n") + ALIGN_LEFT
            buffer += ALIGN_CENTER + S_CUST + BOLD_ON + enc(f"{cliente_nome[:20]}\n") + BOLD_OFF + S_NORM
            
            buffer += ALIGN_LEFT + b"-" * 42 + b"\n"
            buffer += S_ITEM
            num_capi_tot = 0
            for item in carrello:
                num_capi_tot += 1
                nome = item['nome'][:25]; prezzo = f"{item['prezzo']:.2f}"
                spazi = " " * (38 - len(nome) - len(prezzo) - 1)
                buffer += enc(f"{nome}{spazi}{prezzo}") + EURO + b"\n"
            buffer += S_NORM + b"-" * 42 + b"\n"
            if sconto > 0: buffer += enc(f"SCONTO APPLICATO: -{sconto:.2f}") + EURO + b"\n"
            buffer += ALIGN_CENTER + S_WIDE + enc(f"N. Capi: {num_capi_tot}\n")
            
            buffer += S_TOT + BOLD_ON + enc(f"TOT: {totale:.2f}") + EURO + b"\n" + BOLD_OFF
            
            residuo = max(0, totale - acconto)
            
            if acconto > 0 and residuo > 0.01:
                buffer += S_NORM + ALIGN_LEFT + enc(f"ACCONTO: {acconto:.2f}") + EURO + b"\n"
                buffer += S_WIDE + ALIGN_CENTER + b"DA SALDARE:\n"
                buffer += S_TOT + BOLD_ON + enc(f"{residuo:.2f}") + EURO + b"\n" + BOLD_OFF + S_NORM + ALIGN_LEFT
            
            elif residuo > 0.01 and acconto <= 0:
                buffer += S_WIDE + ALIGN_LEFT + b"DA PAGARE\n" + S_NORM
            
            elif residuo <= 0.01:
                buffer += ALIGN_CENTER + S_WIDE + b"PAGATO\n" + S_NORM + ALIGN_LEFT
            
            buffer += b"\n" + ALIGN_CENTER + S_NORM + BOLD_ON + enc(f"{stringa_data_ritiro_stampa}\n") + BOLD_OFF
            buffer += ALIGN_CENTER + S_FOOT
            if footer_text:
                for r in footer_text.split('\n'): buffer += enc(r) + b"\n"
            buffer += ALIGN_LEFT + b"\n\n\n\n\n" + CUT
            win32print.WritePrinter(hPrinter, buffer)
            win32print.EndPagePrinter(hPrinter); win32print.EndDocPrinter(hPrinter)
        finally: win32print.ClosePrinter(hPrinter)
        return True
    except Exception as e: return False

def stampa_riepilogo_ordine_printer(num_visibile, cliente_nome, data_ritiro):
    stampante = get_setting("printer_star")
    S_HUGE = get_star_font("huge") 
    
    try:
        hPrinter = win32print.OpenPrinter(stampante)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("Riepilogo", None, "RAW")); win32print.StartPagePrinter(hPrinter)
            ALIGN_CENTER = b'\x1b\x1d\x61\x01'
            BOLD_ON = b'\x1bE'; BOLD_OFF = b'\x1bF'; CUT = b'\x1bd\x02'; CMD_CP858 = b'\x1b\x1d\x74\x04'
            def enc(txt): return txt.encode('cp858', errors='replace')
            
            buffer = b'\x1b@' + CMD_CP858 
            buffer += ALIGN_CENTER + b"\n"
            
            buffer += S_HUGE + BOLD_ON 
            buffer += enc(f"{num_visibile}\n")
            buffer += b"\n"
            buffer += enc(f"{cliente_nome[:20].upper()}\n")
            buffer += BOLD_OFF 
            
            buffer += b"\n\n\n\n\n" + CUT
            win32print.WritePrinter(hPrinter, buffer)
            win32print.EndPagePrinter(hPrinter); win32print.EndDocPrinter(hPrinter)
        finally: win32print.ClosePrinter(hPrinter)
        return True
    except Exception as e: return False

def formatta_scontrino_whatsapp(nome_cliente, num_scontrino, carrello, totale, acconto, data_ritiro):
    text = f"*LAVANDERIA WASHIFY*\nOrdine N. *{num_scontrino}*\nCliente: {nome_cliente.strip()}\nRitiro: *{data_ritiro}*\n--------------------\n"
    for item in carrello: text += f"{item['nome'][:20]} : € {item['prezzo']:.2f}\n"
    text += "--------------------\n" + f"*TOTALE: € {totale:.2f}*\n"
    if acconto > 0:
        residuo = max(0, totale - acconto)
        text += f"Acconto: € {acconto:.2f}\n" + (f"*DA SALDARE: € {residuo:.2f}*\n" if residuo > 0 else "*PAGATO* ✅\n")
    else: text += "*DA PAGARE* ❌\n"
    text += "\nGrazie e arrivederci!"
    return urllib.parse.quote(text)

def parse_catena_range(catena_str):
    numeri = set()
    catena_str = str(catena_str).strip()
    if not catena_str: return numeri
    try:
        if '-' in catena_str:
            parts = catena_str.split('-')
            if len(parts) == 2:
                start, end = int(parts[0]), int(parts[1])
                if start > end: start, end = end, start
                for i in range(start, end + 1): numeri.add(i)
        else: numeri.add(int(catena_str))
    except ValueError: pass 
    return numeri

# --- ROTTE API ---
@app.route('/')
def home():
    listino_db = get_listino_dict()
    return render_template('index.html', sede=SEDE, listino=listino_db, festivita=FESTIVITA)

@app.route('/api/magazzino_proxy', methods=['POST'])
def api_magazzino_proxy():
    try:
        payload = request.json
        payload['secret'] = REMOTE_SECRET
        if 'sede' not in payload: payload['sede'] = SEDE
        data_encoded = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(REMOTE_URL, data=data_encoded, headers={'Content-Type': 'application/json', 'User-Agent': 'WashifyClient'})
        with urllib.request.urlopen(req, timeout=10) as response:
            resp_data = response.read().decode('utf-8')
            return jsonify(json.loads(resp_data))
    except urllib.error.URLError as e: return jsonify({'status': 'error', 'msg': f"Connessione: {e.reason}"})
    except Exception as e: return jsonify({'status': 'error', 'msg': f"Errore Sistema: {str(e)}"})

@app.route('/api/get_listino_raw')
def api_get_listino_raw():
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM listino ORDER BY ordine ASC")
    items = [dict(row) for row in cursor.fetchall()]; conn.close()
    return jsonify(items)

@app.route('/api/aggiorna_ordine_listino', methods=['POST'])
def api_aggiorna_ordine_listino():
    nuovo_ordine = request.json.get('ordine_ids', [])
    conn = get_db(); cursor = conn.cursor()
    try:
        for index, item_id in enumerate(nuovo_ordine): cursor.execute("UPDATE listino SET ordine = ? WHERE id = ?", (index, item_id))
        conn.commit(); return jsonify({'status': 'success'})
    except Exception as e: return jsonify({'status': 'error', 'msg': str(e)})
    finally: conn.close()

@app.route('/api/save_item_listino', methods=['POST'])
def api_save_item_listino():
    d = request.json; conn = get_db(); cursor = conn.cursor()
    tipo = d.get('tipo_stoccaggio', 'Nastro')
    if 'id' in d and d['id']: cursor.execute("UPDATE listino SET categoria=?, capo=?, prezzo=?, tipo_stoccaggio=? WHERE id=?", (d['categoria'].upper(), d['capo'], d['prezzo'], tipo, d['id']))
    else: 
        cursor.execute("SELECT MAX(ordine) FROM listino")
        max_order = cursor.fetchone()[0]; next_order = (max_order + 1) if max_order is not None else 0
        cursor.execute("INSERT INTO listino (categoria, capo, prezzo, tipo_stoccaggio, ordine) VALUES (?, ?, ?, ?, ?)", (d['categoria'].upper(), d['capo'], d['prezzo'], tipo, next_order))
    conn.commit(); conn.close(); return jsonify({'status': 'success'})

@app.route('/api/delete_item_listino', methods=['POST'])
def api_delete_item_listino():
    d = request.json; conn = get_db(); cursor = conn.cursor(); cursor.execute("DELETE FROM listino WHERE id=?", (d['id'],)); conn.commit(); conn.close(); return jsonify({'status': 'success'})

@app.route('/api/get_settings')
def api_get_settings():
    conn = get_db(); cursor = conn.cursor(); cursor.execute("SELECT * FROM settings")
    data = {row['chiave']: row['valore'] for row in cursor.fetchall()}; conn.close()
    backup_folder = os.path.join(BASE_DIR, "backup")
    backup_file = os.path.join(backup_folder, "app.py")
    data['has_backup'] = 1 if os.path.exists(backup_file) else 0
    return jsonify(data)

@app.route('/api/save_settings', methods=['POST'])
def api_save_settings():
    for k, v in request.json.items(): set_setting(k, v)
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
    except: return jsonify([])

@app.route('/api/elimina_ordine_definitivo', methods=['POST'])
def elimina_ordine_definitivo():
    num = request.json.get('num_scontrino'); conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT id FROM ordini WHERE num_scontrino = ?", (num,)); res = cursor.fetchone()
    if not res: conn.close(); return jsonify({'status': 'error', 'msg': 'Ordine non trovato!'})
    ordine_id = res[0]
    cursor.execute("DELETE FROM dettagli_ordine WHERE ordine_id = ?", (ordine_id,)); cursor.execute("DELETE FROM ordini WHERE id = ?", (ordine_id,))
    conn.commit(); conn.close(); return jsonify({'status': 'success', 'msg': f'Ordine {num} eliminato per sempre.'})

@app.route('/api/elimina_capo_definitivo', methods=['POST'])
def elimina_capo_definitivo():
    id_capo = request.json.get('id_capo'); conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT ordine_id FROM dettagli_ordine WHERE id = ?", (id_capo,)); res = cursor.fetchone()
    if not res: conn.close(); return jsonify({'status': 'error', 'msg': 'Codice Capo non trovato!'})
    ordine_id = res[0]
    cursor.execute("DELETE FROM dettagli_ordine WHERE id = ?", (id_capo,)); cursor.execute("SELECT SUM(prezzo) FROM dettagli_ordine WHERE ordine_id = ?", (ordine_id,)); nuovo_totale = cursor.fetchone()[0] or 0.0
    cursor.execute("UPDATE ordini SET totale = ? WHERE id = ?", (nuovo_totale, ordine_id))
    conn.commit(); conn.close(); return jsonify({'status': 'success', 'msg': f'Capo {id_capo} eliminato. Totale ordine aggiornato.'})

@app.route('/api/carico_lavoro')
def api_carico_lavoro():
    conn = get_db(); cursor = conn.cursor()
    sql = """SELECT o.data_ritiro, COUNT(d.id) as num_capi FROM ordini o JOIN dettagli_ordine d ON o.id = d.ordine_id WHERE d.ritirato = 0 AND o.stato != 'Consegnato' GROUP BY o.data_ritiro"""
    cursor.execute(sql); dati = {row['data_ritiro']: row['num_capi'] for row in cursor.fetchall()}; conn.close(); return jsonify(dati)

@app.route('/esegui_chiusura', methods=['POST'])
def esegui_chiusura():
    successo, msg = esegui_chiusura_fiscale(); return jsonify({'status': 'success' if successo else 'error', 'msg': msg})

@app.route('/cerca_cliente')
def cerca_cliente():
    q = request.args.get('q', ''); conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM clienti WHERE (nome LIKE ? OR cognome LIKE ? OR telefono LIKE ?) AND id != 1", ('%'+q+'%', '%'+q+'%', '%'+q+'%'))
    items = [dict(row, nome_completo=f"{row['nome']} {row['cognome'] or ''}".strip()) for row in cursor.fetchall()]; conn.close(); return jsonify(items)

@app.route('/get_cliente_rapido')
def get_cliente_rapido():
    conn = get_db(); cursor = conn.cursor(); cursor.execute("SELECT * FROM clienti WHERE id = 1"); c = dict(cursor.fetchone()); c['nome_completo'] = "CLIENTE AL BANCO"; conn.close(); return jsonify(c)

@app.route('/crea_cliente', methods=['POST'])
def crea_cliente():
    d = request.json; conn = get_db(); cursor = conn.cursor()
    cursor.execute("INSERT INTO clienti (nome, cognome, telefono, indirizzo, citta, cap, data_nascita, codice_lotteria) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                   (d.get('nome','').upper(), d.get('cognome','').upper(), d.get('telefono',''), d.get('indirizzo',''), d.get('citta',''), d.get('cap',''), d.get('data_nascita',''), d.get('codice_lotteria','').upper()))
    conn.commit(); new_id = cursor.lastrowid; conn.close(); 
    return jsonify({'status': 'success', 'id': new_id, 'nome': f"{d.get('nome','')} {d.get('cognome','')}".strip(), 
                    'telefono': d.get('telefono',''), 'citta': d.get('citta',''), 'indirizzo': d.get('indirizzo',''), 
                    'cognome': d.get('cognome',''), 'cap': d.get('cap',''), 'data_nascita': d.get('data_nascita',''), 
                    'codice_lotteria': d.get('codice_lotteria','').upper(), 'preferenza_scontrino': 'stampa'})

@app.route('/modifica_cliente', methods=['POST'])
def modifica_cliente():
    d = request.json; conn = get_db(); cursor = conn.cursor()
    cursor.execute("UPDATE clienti SET nome=?, cognome=?, telefono=?, indirizzo=?, citta=?, cap=?, data_nascita=?, codice_lotteria=? WHERE id=?", 
                   (d.get('nome','').upper(), d.get('cognome','').upper(), d.get('telefono',''), d.get('indirizzo',''), d.get('citta',''), d.get('cap',''), d.get('data_nascita',''), d.get('codice_lotteria','').upper(), d.get('id')))
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'id': d.get('id'), 'nome': f"{d.get('nome','')} {d.get('cognome','')}".strip(), 
                    'telefono': d.get('telefono',''), 'citta': d.get('citta',''), 'indirizzo': d.get('indirizzo',''), 
                    'cognome': d.get('cognome',''), 'cap': d.get('cap',''), 'data_nascita': d.get('data_nascita',''), 'codice_lotteria': d.get('codice_lotteria','').upper()})

@app.route('/cerca_ordini_aperti')
def cerca_ordini_aperti():
    q = request.args.get('q', ''); cliente_id = request.args.get('cliente_id', ''); conn = get_db(); cursor = conn.cursor()
    # NB: data_ritiro è già presente nella select
    sql = """SELECT DISTINCT o.id, o.num_scontrino, o.data_ingresso, o.data_ritiro, o.totale, o.acconto, o.pagamenti_parziali, o.pagato, o.fiscale_emesso, o.fiscale_desk, c.nome, c.cognome, c.telefono, (SELECT COUNT(*) FROM dettagli_ordine WHERE ordine_id = o.id AND stato_lavorazione = 0) as non_pronti, (SELECT GROUP_CONCAT(DISTINCT numero_catena || ' (' || tipo_stoccaggio || ')') FROM dettagli_ordine WHERE ordine_id = o.id AND numero_catena != '') as posizioni FROM ordini o JOIN clienti c ON o.cliente_id = c.id LEFT JOIN dettagli_ordine d ON o.id = d.ordine_id WHERE o.stato != 'Consegnato' AND o.stato != 'Sospeso'"""
    conditions = []
    if cliente_id: conditions.append(f"o.cliente_id = {cliente_id}")
    elif q.isdigit(): conditions.append(f"o.num_scontrino = {q}")
    else: 
        if not q and not cliente_id: conditions.append("1=0") 
    if conditions: sql += " AND " + " AND ".join(conditions)
    sql += " ORDER BY o.id DESC"; cursor.execute(sql); items = []
    for row in cursor.fetchall(): 
        d = dict(row); d['cliente_nome'] = f"{d['nome']} {d['cognome'] or ''}".strip()
        d['residuo'] = max(0, d['totale'] - (d['acconto'] or 0) - (d['pagamenti_parziali'] or 0))
        d['tutto_pronto'] = (d['non_pronti'] == 0)
        
        try:
            d['data_ingresso_str'] = datetime.strptime(str(d['data_ingresso']).split('.')[0], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y")
        except:
            d['data_ingresso_str'] = d['data_ingresso']
            
        items.append(d)
    conn.close(); return jsonify(items)

@app.route('/get_dettagli_ordine/<int:ordine_id>')
def get_dettagli_ordine(ordine_id):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT totale, acconto, fiscale_emesso, fiscale_desk, pagato, pagamenti_parziali FROM ordini WHERE id = ?", (ordine_id,)); res = cursor.fetchone()
    totale = res[0]; pagato = res[4]; acconto = res[1] or 0; pagamenti_parziali = res[5] or 0 
    totale_versato = totale if pagato == 1 else (acconto + pagamenti_parziali)
    info = {'totale_ordine': totale, 'totale_versato': totale_versato, 'acconto': acconto, 'pagamenti_parziali': pagamenti_parziali, 'fiscale_emesso': res[2], 'fiscale_desk': res[3], 'pagato': pagato}
    cursor.execute("SELECT id, capo, prezzo, ritirato, stato_lavorazione, numero_catena, tipo_stoccaggio FROM dettagli_ordine WHERE ordine_id = ?", (ordine_id,))
    capi = [dict(row) for row in cursor.fetchall()]; conn.close(); return jsonify({'capi': capi, 'info': info})

@app.route('/consegna_items', methods=['POST'])
def consegna_items():
    ids = request.json.get('ids', []); incasso = float(request.json.get('incasso', 0)); sconto_extra = float(request.json.get('sconto_extra', 0)); richiesta_fiscale = request.json.get('stampa_fiscale', False); metodo = request.json.get('metodo_pagamento', '')
    conn = get_db(); cursor = conn.cursor(); capi_ritirati = []
    ordine_id = None
    if ids:
        cursor.execute("SELECT ordine_id FROM dettagli_ordine WHERE id = ?", (ids[0],)); res = cursor.fetchone()
        if res: ordine_id = res[0]
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for item_id in ids:
        cursor.execute("UPDATE dettagli_ordine SET ritirato = 1, data_ritiro_effettivo = ? WHERE id = ?", (now_str, item_id))
        cursor.execute("SELECT capo as nome, prezzo FROM dettagli_ordine WHERE id = ?", (item_id,)); capi_ritirati.append(dict(cursor.fetchone()))
    
    msg = "Nessuna stampa."
    if ordine_id:
        if sconto_extra > 0: cursor.execute("UPDATE ordini SET sconto = sconto + ?, totale = totale - ? WHERE id = ?", (sconto_extra, sconto_extra, ordine_id))
        if incasso > 0: 
            if metodo: cursor.execute("UPDATE ordini SET pagamenti_parziali = pagamenti_parziali + ?, metodo_pagamento = ? WHERE id = ?", (incasso, metodo, ordine_id))
            else: cursor.execute("UPDATE ordini SET pagamenti_parziali = pagamenti_parziali + ? WHERE id = ?", (incasso, ordine_id))
        cursor.execute("SELECT totale, acconto, pagamenti_parziali FROM ordini WHERE id = ?", (ordine_id,)); r = cursor.fetchone()
        totale_pagato = (r['acconto'] or 0) + (r['pagamenti_parziali'] or 0)
        has_acconto = (r['acconto'] and r['acconto'] > 0); valore_totale_capi_selezionati = sum(item['prezzo'] for item in capi_ritirati)
        if totale_pagato >= r['totale'] - 0.01: cursor.execute("UPDATE ordini SET pagato = 1 WHERE id = ?", (ordine_id,))
        cursor.execute("SELECT COUNT(*) FROM dettagli_ordine WHERE ordine_id = ? AND ritirato = 0", (ordine_id,))
        if cursor.fetchone()[0] == 0: cursor.execute("UPDATE ordini SET stato = 'Consegnato' WHERE id = ?", (ordine_id,))
        if richiesta_fiscale:
            cursor.execute("UPDATE ordini SET fiscale_emesso = 1 WHERE id = ?", (ordine_id,))
            cursor.execute("SELECT c.codice_lotteria FROM clienti c JOIN ordini o ON o.cliente_id = c.id WHERE o.id = ?", (ordine_id,)); res_cli = cursor.fetchone(); cod_lotteria = res_cli[0] if res_cli else ""
            
            # --- MODIFICA LOGICA DESCRIZIONE ---
            importo_da_fiscale = incasso
            
            # Default
            items_da_fiscale = [{'nome': 'Ritiro Capi', 'prezzo': incasso}]

            # Se l'importo che pago ora è UGUALE alla somma dei capi, stampo i capi dettagliati
            if abs(incasso - valore_totale_capi_selezionati) < 0.05:
                items_da_fiscale = capi_ritirati
            # Oppure se c'era un acconto e sto saldando (o ritirando tutto), uso i capi (logica precedente)
            elif has_acconto:
                items_da_fiscale = capi_ritirati
                importo_da_fiscale = valore_totale_capi_selezionati
            
            if importo_da_fiscale > 0:
                if stampa_fiscale_vendita(items_da_fiscale, importo_da_fiscale, cod_lotteria): msg = "✅ Scontrino Fiscale Stampato!"
                else: msg = "❌ Errore Stampa Fiscale!"
            else: msg = "Importo 0, niente scontrino."
    conn.commit(); conn.close(); return jsonify({'status': 'success', 'msg': msg})

@app.route('/salva_ordine', methods=['POST'])
def salva_ordine():
    d = request.json; listino_vendita = get_listino_dict().get("PRODOTTI VENDITA", {})
    carrello = d['carrello']; data_ritiro_raw = d['data_ritiro']; sconto, acconto = float(d.get('sconto', 0)), float(d.get('acconto', 0)); pagato_bool = d['pagato']; metodo = d['metodo']; is_approx = d.get('is_approx', False); azione = d.get('azione', 'stampa') 
    dt_obj = datetime.strptime(data_ritiro_raw, "%Y-%m-%d") if "-" in data_ritiro_raw and len(data_ritiro_raw.split("-")[0])==4 else datetime.now()
    data_ritiro_str = dt_obj.strftime("%d/%m") if "-" in data_ritiro_raw else data_ritiro_raw
    totale = max(0, sum(i['prezzo'] for i in carrello) - sconto); solo_prodotti = all(i['nome'] in listino_vendita for i in carrello)
    if pagato_bool: acconto = totale; pagato = True
    else: pagato = True if acconto >= totale else False
    if solo_prodotti: pagato = True; metodo = metodo or "Contanti"; acconto = totale
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("UPDATE clienti SET preferenza_scontrino = ? WHERE id = ?", (azione, d['cliente_id']))
    cursor.execute("SELECT codice_lotteria FROM clienti WHERE id = ?", (d['cliente_id'],)); res_cli = cursor.fetchone(); codice_lotteria = res_cli['codice_lotteria'] if res_cli else ""
    last_reset = get_setting("last_reset_date"); cursor.execute("SELECT COUNT(*) FROM ordini WHERE data_ingresso > ?", (last_reset,)); nuovo_num = cursor.fetchone()[0] + 1
    stampa_ora = False; contiene_prodotti = any(i['nome'] in listino_vendita for i in carrello); fiscal_always = get_setting("fiscal_always") == "1"
    if (pagato and metodo == 'Carta') or contiene_prodotti or fiscal_always: stampa_ora = True
    fiscale_desk_val = 1 if stampa_ora else 0
    cursor.execute("INSERT INTO ordini (num_scontrino, cliente_id, data_ingresso, data_ritiro, totale, sconto, acconto, pagato, fiscale_emesso, fiscale_desk, metodo_pagamento, sede, stato, is_approx_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (nuovo_num, d['cliente_id'], datetime.now(), data_ritiro_str, totale, sconto, acconto, 1 if pagato else 0, 1 if stampa_ora else 0, fiscale_desk_val, metodo, SEDE, 'Consegnato' if solo_prodotti else 'In Lavorazione', 1 if is_approx else 0))
    oid = cursor.lastrowid; stato_lavorazione = 1 if solo_prodotti else 0; items_con_id = []
    for i in carrello: 
        cursor.execute("INSERT INTO dettagli_ordine (ordine_id, capo, prezzo, ritirato, stato_lavorazione) VALUES (?, ?, ?, ?, ?)", (oid, i['nome'], i['prezzo'], 0 if not solo_prodotti else 1, stato_lavorazione))
        item_id = cursor.lastrowid; capo_con_id = i.copy(); capo_con_id['id'] = item_id; items_con_id.append(capo_con_id)
    conn.commit(); conn.close()
    if stampa_ora:
        if pagato: stampa_fiscale_vendita(carrello, totale, codice_lotteria)
        else: stampa_fiscale_vendita([{'nome': 'Acconto Lavanderia', 'prezzo': acconto}], acconto, codice_lotteria)
    msg_wa = "Nessun invio"
    if not solo_prodotti:
        if azione == 'stampa':
            stampa_scontrino(nuovo_num, datetime.now().strftime("%d/%m %H:%M"), d['cliente_nome'], carrello, totale, sconto, acconto, data_ritiro_str, pagato, metodo, is_approx, codice_lotteria)
            stampa_etichette(nuovo_num, items_con_id, d['cliente_nome'], data_ritiro_str)
        elif azione == 'whatsapp':
            conn = get_db(); cursor = conn.cursor(); cursor.execute("SELECT telefono FROM clienti WHERE id = ?", (d['cliente_id'],)); res_tel = cursor.fetchone(); conn.close()
            telefono = res_tel['telefono'] if res_tel else ""
            if len(telefono) > 5:
                testo_msg = formatta_scontrino_whatsapp(d['cliente_nome'], nuovo_num, carrello, totale, acconto, data_ritiro_str)
                path_json = os.path.join(PATH_WA_OUT, f"ord_{oid}.json")
                dati_bot = { "telefono": telefono, "messaggio": urllib.parse.unquote(testo_msg) }
                if not os.path.exists(PATH_WA_OUT): os.makedirs(PATH_WA_OUT)
                if not os.path.exists(PATH_WA_ESITI): os.makedirs(PATH_WA_ESITI)
                with open(path_json, 'w', encoding='utf-8') as f: json.dump(dati_bot, f)
                esito_path = os.path.join(PATH_WA_ESITI, f"ord_{oid}.txt"); attesa = 0; msg_wa = "⏳ Timeout WhatsApp"
                while attesa < 100:
                    if os.path.exists(esito_path):
                        with open(esito_path, 'r') as f: esito = f.read()
                        msg_wa = "✅ WhatsApp Inviato!" if esito == "OK" else "❌ Errore Invio WhatsApp"; os.remove(esito_path); break
                    time.sleep(0.1); attesa += 1
            else: msg_wa = "❌ Cliente senza telefono!"
            stampa_etichette(nuovo_num, items_con_id, d['cliente_nome'], data_ritiro_str)
        elif azione == 'email': stampa_etichette(nuovo_num, items_con_id, d['cliente_nome'], data_ritiro_str)
    return jsonify({"status": "success", "id_ordine": oid, "msg_wa": msg_wa})

@app.route('/sospendi_ordine', methods=['POST'])
def sospendi_ordine():
    d=request.json; conn=get_db(); cursor=conn.cursor()
    cursor.execute("INSERT INTO ordini (cliente_id, data_ingresso, data_ritiro, totale, stato, sede) VALUES (?, ?, ?, ?, 'Sospeso', ?)", (d['cliente_id'], datetime.now(), d['data_ritiro'], 0, SEDE))
    oid=cursor.lastrowid
    for i in d['carrello']: cursor.execute("INSERT INTO dettagli_ordine (ordine_id, capo, prezzo) VALUES (?, ?, ?)", (oid, i['nome'], i['prezzo']))
    conn.commit(); conn.close(); return jsonify({'status': 'success'})

@app.route('/recupera_sospesi')
def recupera_sospesi():
    conn=get_db(); cursor=conn.cursor()
    cursor.execute("SELECT o.id, c.nome, c.cognome, o.data_ingresso FROM ordini o JOIN clienti c ON o.cliente_id = c.id WHERE o.stato = 'Sospeso' ORDER BY o.id DESC")
    r=[dict(x) for x in cursor.fetchall()]; conn.close(); return jsonify(r)

@app.route('/carica_sospeso', methods=['POST'])
def carica_sospeso():
    oid=request.json['id']; conn=get_db(); cursor=conn.cursor()
    cursor.execute("SELECT * FROM ordini WHERE id=?",(oid,)); o=dict(cursor.fetchone())
    cursor.execute("SELECT * FROM clienti WHERE id=?",(o['cliente_id'],)); c=dict(cursor.fetchone()); c['nome_completo']=f"{c['nome']} {c['cognome'] or ''}".strip()
    cursor.execute("SELECT capo as nome, prezzo FROM dettagli_ordine WHERE ordine_id=?",(oid,)); l=[dict(x) for x in cursor.fetchall()]
    cursor.execute("DELETE FROM dettagli_ordine WHERE ordine_id=?",(oid,)); cursor.execute("DELETE FROM ordini WHERE id=?",(oid,)); conn.commit(); conn.close()
    return jsonify({'cliente':c, 'carrello':l, 'data_ritiro':o['data_ritiro']})

@app.route('/get_items_scontrino', methods=['POST'])
def get_items_scontrino():
    tipo = request.json.get('tipo'); valore = request.json.get('valore'); conn = get_db(); cursor = conn.cursor()
    target_item_id = None; order_id = None
    if tipo == 'ordine':
        cursor.execute("SELECT id FROM ordini WHERE num_scontrino = ? AND stato != 'Consegnato'", (valore,)); res = cursor.fetchone()
        if res: order_id = res[0]
    elif tipo == 'capo':
        cursor.execute("SELECT ordine_id FROM dettagli_ordine WHERE id = ? AND ritirato = 0", (valore,)); res = cursor.fetchone()
        if res: order_id = res[0]; target_item_id = int(valore)
    if not order_id: conn.close(); return jsonify({'status': 'error', 'msg': 'Nessun risultato trovato.'})
    if tipo == 'capo' and target_item_id: cursor.execute("SELECT id, capo, stato_lavorazione, numero_catena FROM dettagli_ordine WHERE id = ?", (target_item_id,))
    else: cursor.execute("SELECT id, capo, stato_lavorazione, numero_catena FROM dettagli_ordine WHERE ordine_id = ?", (order_id,))
    capi = [dict(row) for row in cursor.fetchall()]; conn.close()
    return jsonify({'status': 'success', 'items': capi, 'ordine_id': order_id, 'target_item_id': target_item_id})

@app.route('/conferma_pronti', methods=['POST'])
def conferma_pronti():
    ids = request.json.get('ids', []); oid = request.json.get('ordine_id'); catena_input = request.json.get('catena', '').strip(); conn = get_db(); cursor = conn.cursor()
    if catena_input and ids:
        nuovi_numeri = parse_catena_range(catena_input)
        if nuovi_numeri:
            placeholders = ','.join(['?']*len(ids))
            cursor.execute(f"SELECT DISTINCT tipo_stoccaggio FROM dettagli_ordine WHERE id IN ({placeholders})", ids)
            types = [row[0] for row in cursor.fetchall()]
            for t in types:
                cursor.execute("""SELECT DISTINCT d.numero_catena, o.num_scontrino FROM dettagli_ordine d JOIN ordini o ON d.ordine_id = o.id WHERE d.tipo_stoccaggio = ? AND d.ritirato = 0 AND d.ordine_id != ? AND d.numero_catena != ''""", (t, oid))
                rows = cursor.fetchall()
                for row in rows:
                    numeri_esistenti = parse_catena_range(row['numero_catena'])
                    if not nuovi_numeri.isdisjoint(numeri_esistenti): conn.close(); return jsonify({'status': 'error', 'msg': f"⛔ POSIZIONE {catena_input} OCCUPATA (Conflitto con {row['numero_catena']} - Ordine #{row['num_scontrino']})!"})
    if ids: pl = ','.join(['?']*len(ids)); cursor.execute(f"UPDATE dettagli_ordine SET stato_lavorazione = 1, numero_catena = ? WHERE id IN ({pl})", [catena_input] + ids)
    else: cursor.execute("UPDATE dettagli_ordine SET stato_lavorazione = 0, numero_catena = '' WHERE ordine_id = ?", (oid,))
    conn.commit(); conn.close(); return jsonify({'status': 'success'})

@app.route('/api/stampa_riepilogo', methods=['POST'])
def api_stampa_riepilogo():
    try:
        oid = request.json.get('ordine_id'); conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT o.num_scontrino, o.data_ritiro, c.nome, c.cognome FROM ordini o JOIN clienti c ON o.cliente_id = c.id WHERE o.id = ?", (oid,)); res = cursor.fetchone(); conn.close()
        if not res: return jsonify({'status': 'error', 'msg': 'Ordine non trovato'})
        nome_completo = f"{res['nome']} {res['cognome'] or ''}".strip()
        if stampa_riepilogo_ordine_printer(res['num_scontrino'], nome_completo, res['data_ritiro']): return jsonify({'status': 'success', 'msg': 'Riepilogo stampato!'})
        else: return jsonify({'status': 'error', 'msg': 'Errore stampa riepilogo'})
    except Exception as e: return jsonify({'status': 'error', 'msg': str(e)})

@app.route('/modifica_capo_ordine', methods=['POST'])
def modifica_capo_ordine():
    d = request.json; conn = get_db(); cursor = conn.cursor()
    cursor.execute("UPDATE dettagli_ordine SET capo = ?, prezzo = ? WHERE id = ?", (d['nome'], d['prezzo'], d['id']))
    cursor.execute("SELECT SUM(prezzo) FROM dettagli_ordine WHERE ordine_id = (SELECT ordine_id FROM dettagli_ordine WHERE id = ?)", (d['id'],))
    nuovo_tot = cursor.fetchone()[0]; cursor.execute("UPDATE ordini SET totale = ? WHERE id = (SELECT ordine_id FROM dettagli_ordine WHERE id = ?)", (nuovo_tot, d['id']))
    conn.commit(); conn.close(); return jsonify({'status': 'success'})

@app.route('/api/check_update')
def check_update():
    try:
        with open(os.path.join(BASE_DIR, "version.txt"), "r") as f: local_ver = f.read().strip()
        with urllib.request.urlopen(GITHUB_REPO_BASE + "version.txt") as response: remote_ver = response.read().decode('utf-8').strip()
        has_backup = os.path.exists(os.path.join(BASE_DIR, "backup", "app.py"))
        return jsonify({'update_available': remote_ver != local_ver, 'local': local_ver, 'remote': remote_ver, 'has_backup': has_backup})
    except Exception as e: return jsonify({'error': str(e)})

@app.route('/api/perform_update', methods=['POST'])
def perform_update():
    try:
        backup_dir = os.path.join(BASE_DIR, "backup"); templates_dir = os.path.join(BASE_DIR, "templates")
        if not os.path.exists(backup_dir): os.makedirs(backup_dir)
        shutil.copy(os.path.join(BASE_DIR, "app.py"), os.path.join(backup_dir, "app.py"))
        if os.path.exists(os.path.join(templates_dir, "index.html")): shutil.copy(os.path.join(templates_dir, "index.html"), os.path.join(backup_dir, "index.html"))
        if os.path.exists(os.path.join(BASE_DIR, "version.txt")): shutil.copy(os.path.join(BASE_DIR, "version.txt"), os.path.join(backup_dir, "version.txt"))
        urllib.request.urlretrieve(GITHUB_REPO_BASE + "app.py", os.path.join(BASE_DIR, "app.py"))
        urllib.request.urlretrieve(GITHUB_REPO_BASE + "templates/index.html", os.path.join(templates_dir, "index.html"))
        urllib.request.urlretrieve(GITHUB_REPO_BASE + "version.txt", os.path.join(BASE_DIR, "version.txt"))
        return jsonify({'status': 'success', 'msg': 'Aggiornamento completato!'})
    except Exception as e: return jsonify({'status': 'error', 'msg': str(e)})

@app.route('/api/restore_backup', methods=['POST'])
def restore_backup():
    try:
        backup_dir = os.path.join(BASE_DIR, "backup"); templates_dir = os.path.join(BASE_DIR, "templates")
        shutil.copy(os.path.join(backup_dir, "app.py"), os.path.join(BASE_DIR, "app.py"))
        shutil.copy(os.path.join(backup_dir, "index.html"), os.path.join(templates_dir, "index.html"))
        return jsonify({'status':'success', 'msg':'Ripristino completato!'})
    except Exception as e: return jsonify({'status':'error', 'msg':str(e)})

@app.route('/api/test_print', methods=['POST'])
def api_test_print():
    tipo = request.json.get('tipo')
    fake_cart = [{'nome': 'Camicia Test', 'prezzo': 5.00}, {'nome': 'Giacca Test', 'prezzo': 10.00}]
    if tipo == 'scontrino': stampa_scontrino(9999, datetime.now().strftime("%d/%m/%Y"), "TEST", fake_cart, 15.00, 0, 0, "25/12", False, "Contanti")
    elif tipo == 'etichetta': stampa_etichette(9999, [{'nome': 'Camicia Test', 'id': 9876}, {'nome': 'Giacca Test', 'id': 9877}], "TEST", "25/12")
    return jsonify({'status': 'success'})

@app.route('/api/toggle_stato_pagamento', methods=['POST'])
def toggle_stato_pagamento():
    oid = request.json.get('ordine_id'); conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT pagato, totale FROM ordini WHERE id = ?", (oid,)); res = cursor.fetchone()
    if res:
        nuovo_stato = 0 if res['pagato'] == 1 else 1
        cursor.execute("SELECT SUM(prezzo) FROM dettagli_ordine WHERE ordine_id = ? AND ritirato = 1", (oid,)); valore_ritirati = cursor.fetchone()[0] or 0.0
        nuovo_acconto = valore_ritirati if nuovo_stato == 0 else res['totale']
        cursor.execute("UPDATE ordini SET pagato = ?, acconto = ? WHERE id = ?", (nuovo_stato, nuovo_acconto, oid))
        conn.commit()
    conn.close(); return jsonify({'status': 'success'})

@app.route('/api/stampa_etichetta_singola', methods=['POST'])
def stampa_etichetta_singola():
    try:
        d = request.json; capo_id = d.get('capo_id'); conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT * FROM dettagli_ordine WHERE id = ?", (capo_id,)); capo = cursor.fetchone()
        if not capo: return jsonify({'status': 'error', 'msg': 'Capo non trovato'})
        cursor.execute("SELECT * FROM ordini WHERE id = ?", (capo['ordine_id'],)); ordine = cursor.fetchone()
        cursor.execute("SELECT * FROM clienti WHERE id = ?", (ordine['cliente_id'],)); cliente = cursor.fetchone()
        cliente_nome = f"{cliente['nome']} {cliente['cognome'] or ''}".strip()
        conn.close(); item_obj = {'id': capo['id'], 'nome': capo['capo']}
        if stampa_etichette(ordine['num_scontrino'], [item_obj], cliente_nome, ordine['data_ritiro']): return jsonify({'status': 'success', 'msg': 'Etichetta singola stampata!'})
        else: return jsonify({'status': 'error', 'msg': 'Errore durante la stampa'})
    except Exception as e: return jsonify({'status': 'error', 'msg': str(e)})

@app.route('/api/ristampa_ordine', methods=['POST'])
def ristampa_ordine():
    try:
        d = request.json; oid = d.get('id'); tipo = d.get('tipo') 
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT * FROM ordini WHERE id = ?", (oid,)); ordine = cursor.fetchone()
        if not ordine: return jsonify({'status': 'error', 'msg': 'Ordine non trovato'})
        cursor.execute("SELECT * FROM clienti WHERE id = ?", (ordine['cliente_id'],)); cliente = cursor.fetchone()
        cliente_nome = f"{cliente['nome']} {cliente['cognome'] or ''}".strip(); codice_lotteria = cliente['codice_lotteria'] if cliente and cliente['codice_lotteria'] else ""
        cursor.execute("SELECT id, capo as nome, prezzo FROM dettagli_ordine WHERE ordine_id = ?", (oid,)); carrello = [dict(row) for row in cursor.fetchall()]
        is_approx = False; 
        if 'is_approx_date' in ordine.keys(): is_approx = (ordine['is_approx_date'] == 1)
        conn.close()
        if tipo == 'scontrino':
            totale = ordine['totale']; sconto = ordine['sconto']; acconto = ordine['acconto']; pagato = (ordine['pagato'] == 1); metodo = ordine['metodo_pagamento'] or "Contanti"
            data_ingresso_str = datetime.strptime(ordine['data_ingresso'].split('.')[0], "%Y-%m-%d %H:%M:%S").strftime("%d/%m %H:%M")
            if stampa_scontrino(ordine['num_scontrino'], data_ingresso_str, cliente_nome, carrello, totale, sconto, acconto, ordine['data_ritiro'], pagato, metodo, is_approx, codice_lotteria): return jsonify({'status': 'success', 'msg': 'Scontrino inviato alla stampante!'})
            else: return jsonify({'status': 'error', 'msg': 'Errore stampa scontrino'})
        elif tipo == 'etichette':
            if stampa_etichette(ordine['num_scontrino'], carrello, cliente_nome, ordine['data_ritiro']): return jsonify({'status': 'success', 'msg': 'Etichette inviate alla stampante!'})
            else: return jsonify({'status': 'error', 'msg': 'Errore stampa etichette'})
        return jsonify({'status': 'error', 'msg': 'Tipo stampa non valido'})
    except Exception as e: return jsonify({'status': 'error', 'msg': str(e)})

@app.route('/api/storico_cliente/<int:cliente_id>')
def storico_cliente(cliente_id):
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("""SELECT o.id, o.num_scontrino, o.data_ingresso, o.data_ritiro, o.totale, d.capo, d.prezzo, 
                      (SELECT MAX(data_ritiro_effettivo) FROM dettagli_ordine WHERE ordine_id = o.id) as data_effettiva 
                      FROM ordini o JOIN dettagli_ordine d ON o.id = d.ordine_id 
                      WHERE o.cliente_id = ? AND o.stato = 'Consegnato' 
                      ORDER BY o.data_ingresso ASC""", (cliente_id,))
    rows = cursor.fetchall(); conn.close()
    storico = {}
    for r in rows:
        oid = r['id']
        if oid not in storico: 
            d_eff = r['data_effettiva']
            if d_eff:
                try:
                    d_eff = datetime.strptime(str(d_eff).split('.')[0], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y")
                except:
                    d_eff = str(d_eff)[:10] 
            else:
                d_eff = "-"
            
            try:
                data_ing_fmt = datetime.strptime(r['data_ingresso'].split('.')[0], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y")
            except:
                data_ing_fmt = r['data_ingresso']

            storico[oid] = {
                'num_scontrino': r['num_scontrino'], 
                'data_ingresso': r['data_ingresso'], 
                'data_ingresso_formatted': data_ing_fmt, 
                'data_ritiro': r['data_ritiro'], 
                'data_ritiro_effettivo': d_eff, 
                'totale': r['totale'], 
                'capi': []
            }
        storico[oid]['capi'].append({'capo': r['capo'], 'prezzo': r['prezzo']})
    return jsonify(list(storico.values()))

@app.route('/api/get_stats')
def api_get_stats():
    start_date = request.args.get('start', '2000-01-01'); end_date_raw = request.args.get('end', '2100-01-01')
    q_capo = request.args.get('search_capo', '').strip() 
    
    conn = get_db(); cursor = conn.cursor()

    # --- CALCOLO DATE ANNO SCORSO ---
    s_date = datetime.strptime(start_date, "%Y-%m-%d")
    e_date = datetime.strptime(end_date_raw, "%Y-%m-%d")
    prev_start = (s_date - timedelta(days=366 if (s_date.year - 1) % 4 == 0 else 365)).strftime("%Y-%m-%d")
    prev_end = (e_date - timedelta(days=366 if (e_date.year - 1) % 4 == 0 else 365)).strftime("%Y-%m-%d")

    daily_stats = defaultdict(lambda: {'items': 0, 'revenue': 0.0, 'fiscal': 0})
    cursor.execute("""SELECT date(o.data_ingresso) as data, count(d.id) as tot_capi FROM ordini o JOIN dettagli_ordine d ON o.id = d.ordine_id WHERE date(o.data_ingresso) BETWEEN ? AND ? GROUP BY date(o.data_ingresso)""", (start_date, end_date_raw))
    for r in cursor.fetchall(): daily_stats[r['data']]['items'] = r['tot_capi']
    cursor.execute("""SELECT date(data_ingresso) as data, SUM(totale) as tot_incasso, SUM(CASE WHEN fiscale_emesso = 1 THEN 1 ELSE 0 END) as tot_fiscali FROM ordini WHERE date(data_ingresso) BETWEEN ? AND ? GROUP BY date(data_ingresso)""", (start_date, end_date_raw))
    for r in cursor.fetchall():
        daily_stats[r['data']]['revenue'] = r['tot_incasso'] if r['tot_incasso'] else 0.0
        daily_stats[r['data']]['fiscal'] = r['tot_fiscali'] if r['tot_fiscali'] else 0
    trend_daily = []
    for d in sorted(daily_stats.keys()): trend_daily.append({'data': d, 'items': daily_stats[d]['items'], 'revenue': daily_stats[d]['revenue'], 'fiscal': daily_stats[d]['fiscal']})
    
    cursor.execute("""SELECT d.capo, count(d.id) as qty FROM dettagli_ordine d JOIN ordini o ON d.ordine_id = o.id WHERE date(o.data_ingresso) BETWEEN ? AND ? GROUP BY d.capo ORDER BY qty DESC LIMIT 10""", (start_date, end_date_raw))
    top_items = [dict(row) for row in cursor.fetchall()]

    # --- STATS CORRENTI ---
    cursor.execute("SELECT COUNT(*) FROM ordini WHERE fiscale_emesso = 1 AND date(data_ingresso) BETWEEN ? AND ?", (start_date, end_date_raw)); fiscal_count = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(totale) FROM ordini WHERE fiscale_emesso = 1 AND date(data_ingresso) BETWEEN ? AND ?", (start_date, end_date_raw)); fiscal_value = cursor.fetchone()[0] or 0.0
    cursor.execute("SELECT SUM(totale) FROM ordini WHERE date(data_ingresso) BETWEEN ? AND ?", (start_date, end_date_raw)); total_revenue = cursor.fetchone()[0] or 0.0
    cursor.execute("""SELECT COUNT(d.id) FROM dettagli_ordine d JOIN ordini o ON d.ordine_id = o.id WHERE date(o.data_ingresso) BETWEEN ? AND ?""", (start_date, end_date_raw)); capi_entrati_period = cursor.fetchone()[0] or 0
    
    # --- STATS ANNO PRECEDENTE ---
    cursor.execute("SELECT COUNT(*) FROM ordini WHERE fiscale_emesso = 1 AND date(data_ingresso) BETWEEN ? AND ?", (prev_start, prev_end))
    prev_fiscal_count = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(totale) FROM ordini WHERE fiscale_emesso = 1 AND date(data_ingresso) BETWEEN ? AND ?", (prev_start, prev_end))
    prev_fiscal_value = cursor.fetchone()[0] or 0.0
    cursor.execute("SELECT SUM(totale) FROM ordini WHERE date(data_ingresso) BETWEEN ? AND ?", (prev_start, prev_end))
    prev_total_revenue = cursor.fetchone()[0] or 0.0
    cursor.execute("""SELECT COUNT(d.id) FROM dettagli_ordine d JOIN ordini o ON d.ordine_id = o.id WHERE date(o.data_ingresso) BETWEEN ? AND ?""", (prev_start, prev_end))
    prev_capi_entrati = cursor.fetchone()[0] or 0

    params_clients = (start_date, end_date_raw, start_date, end_date_raw)
    cursor.execute("""SELECT c.nome, c.cognome, SUM(o.totale) as total_spent, (SELECT COUNT(d.id) FROM dettagli_ordine d JOIN ordini o2 ON d.ordine_id = o2.id WHERE o2.cliente_id = c.id AND date(o2.data_ingresso) BETWEEN ? AND ?) as total_items FROM ordini o JOIN clienti c ON o.cliente_id = c.id WHERE date(o.data_ingresso) BETWEEN ? AND ? GROUP BY c.id ORDER BY total_spent DESC LIMIT 10""", params_clients)
    top_clients = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("""SELECT SUM(CASE WHEN d.ritirato = 1 THEN 1 ELSE 0 END) as ritirati FROM dettagli_ordine d JOIN ordini o ON d.ordine_id = o.id WHERE date(o.data_ingresso) BETWEEN ? AND ?""", (start_date, end_date_raw)); row_ritiri = cursor.fetchone(); capi_ritirati = row_ritiri[0] or 0
    cursor.execute("""SELECT SUM(CASE WHEN pagato = 1 THEN 1 ELSE 0 END) as pagati, SUM(CASE WHEN pagato = 0 THEN 1 ELSE 0 END) as da_pagare FROM ordini WHERE date(data_ingresso) BETWEEN ? AND ?""", (start_date, end_date_raw)); row_pagamenti = cursor.fetchone(); ordini_pagati = row_pagamenti[0] or 0; ordini_da_pagare = row_pagamenti[1] or 0
    cursor.execute("""SELECT COUNT(*) FROM ordini WHERE fiscale_emesso = 1 AND stato = 'Consegnato' AND date(data_ingresso) BETWEEN ? AND ?""", (start_date, end_date_raw)); scontrini_prodotti = cursor.fetchone()[0]
    
    sql_ritirati = """SELECT c.nome, c.cognome, d.capo, d.prezzo, o.data_ingresso, d.data_ritiro_effettivo 
                      FROM dettagli_ordine d JOIN ordini o ON d.ordine_id = o.id JOIN clienti c ON o.cliente_id = c.id 
                      WHERE d.ritirato = 1 AND date(o.data_ingresso) BETWEEN ? AND ?"""
    params_ritirati = [start_date, end_date_raw]
    
    if q_capo:
        sql_ritirati += " AND d.capo LIKE ?"
        params_ritirati.append(f"%{q_capo}%")
        
    sql_ritirati += " ORDER BY c.nome, c.cognome"
    
    cursor.execute(sql_ritirati, params_ritirati)
    raw_ritirati = cursor.fetchall()
    
    ritirati_by_client = defaultdict(list)
    for r in raw_ritirati: 
        data_show = r['data_ritiro_effettivo'] if r['data_ritiro_effettivo'] else r['data_ingresso']
        ritirati_by_client[f"{r['nome']} {r['cognome'] or ''}".strip()].append({'capo': r['capo'], 'prezzo': r['prezzo'], 'data': data_show})
    
    ritirati_dettaglio = []; 
    for cliente, items in ritirati_by_client.items(): ritirati_dettaglio.append({'cliente': cliente, 'items': items})
    ritirati_dettaglio.sort(key=lambda x: x['cliente']); conn.close()
    
    return jsonify({
        'trend_daily': trend_daily, 
        'top_items': top_items, 
        'ritirati_dettaglio': ritirati_dettaglio, 
        'stats': {
            'fiscal_count': fiscal_count, 
            'fiscal_value': fiscal_value, 
            'total_revenue': total_revenue, 
            'top_clients': top_clients, 
            'capi_ritirati': capi_ritirati, 
            'capi_totali': capi_entrati_period, 
            'ordini_pagati': ordini_pagati, 
            'ordini_da_pagare': ordini_da_pagare, 
            'scontrini_prodotti': scontrini_prodotti,
            'prev_fiscal_count': prev_fiscal_count,
            'prev_fiscal_value': prev_fiscal_value,
            'prev_total_revenue': prev_total_revenue,
            'prev_capi_totali': prev_capi_entrati
        }
    })

if __name__ == '__main__': 
    init_db()
    print("--- AVVIO WASHIFY V.3.6 (Fixed Fiscal Delay & Names) ---")
    app.run(debug=True, host='0.0.0.0', port=5000)