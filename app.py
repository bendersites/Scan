#!/usr/bin/env python3
import os, csv, re, time, subprocess, threading
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
from flask import Flask, render_template, request, send_file, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['REPORTS_FOLDER'] = 'reports'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['REPORTS_FOLDER'], exist_ok=True)

scan_progress = {'running': False, 'total': 0, 'current': 0, 'url': '', 'results': [], 'report_file': None}
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8", "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
SKIP_DOMAINS = ['facebook', 'instagram', 'maps.google', 'fleurop', 'gelbeseiten', 'etsy', 'blume2000', 'blumen-risse', 'google']

def get_domain_age(domain):
    try:
        result = subprocess.run(['whois', domain], capture_output=True, text=True, timeout=5)
        for pat in [r'Creation Date:\s*(\d{4})', r'created:\s*(\d{4})', r'Registered on:\s*(\d{4})']:
            m = re.search(pat, result.stdout, re.I)
            if m: return int(m.group(1))
    except: pass
    return None

def analyze_website(url):
    result = {'url': url, 'reachable': False, 'domain_age': None,
        'scores': {'rechtliches': 0, 'technik': 0, 'vertrauen': 0, 'verkauf': 0, 'google': 0},
        'details': [], 'warnings': [], 'total_score': 0, 'category': 'unbekannt',
        'mail': None, 'telefon': None, 'meta_title': '', 'meta_desc': '',
        'has_ssl': False, 'load_time': 0, 'server': ''}
    if not url.startswith('http'): url = 'https://' + url
    domain = urlparse(url).netloc or urlparse(url).path
    result['has_ssl'] = url.startswith('https://')
    if not result['has_ssl']:
        result['warnings'].append("Kein HTTPS"); result['details'].append("❌ Kein SSL")
    else:
        result['details'].append("✅ SSL aktiv"); result['scores']['rechtliches'] += 5
    result['domain_age'] = get_domain_age(domain)
    if result['domain_age']: result['details'].append(f"📅 Domain seit {result['domain_age']}")
    start_time = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        result['load_time'] = round(time.time() - start_time, 2)
        result['reachable'] = True; result['server'] = r.headers.get('Server', 'unbekannt')
        html = r.text.lower(); soup = BeautifulSoup(r.text, 'html.parser'); text = r.text
    except Exception as e:
        result['warnings'].append(f"Nicht erreichbar: {str(e)[:50]}")
        result['details'].append(f"❌ Fehler: {str(e)[:50]}")
        return result

    # RECHTLICHES
    has_imp = any(p in text for p in ['/impressum','impressum','imprint']) or soup.find('a', href=re.compile(r'impressum|imprint', re.I))
    if has_imp: result['scores']['rechtliches'] += 5; result['details'].append("✅ Impressum")
    else: result['warnings'].append("Kein Impressum"); result['details'].append("❌ Kein Impressum")
    has_dsg = any(p in text for p in ['/datenschutz','datenschutz','privacy']) or soup.find('a', href=re.compile(r'datenschutz|privacy', re.I))
    if has_dsg: result['scores']['rechtliches'] += 5; result['details'].append("✅ Datenschutz")
    else: result['warnings'].append("Kein Datenschutz"); result['details'].append("❌ Kein Datenschutz")
    if any(c in text for c in ['cookie','consent','dsgvo','gdpr']):
        result['scores']['rechtliches'] += 5; result['details'].append("✅ Cookie-Banner")
    else: result['warnings'].append("Kein Cookie-Banner"); result['details'].append("⚠️ Kein Cookie-Banner")
    if result['has_ssl']: result['scores']['rechtliches'] += 5

    # TECHNIK
    vp = soup.find('meta', attrs={'name': 'viewport'})
    if vp: result['scores']['technik'] += 5; result['details'].append("✅ Mobile")
    else: result['warnings'].append("Nicht mobil"); result['details'].append("❌ Kein Viewport")
    lt = result['load_time']
    if lt < 2: result['scores']['technik'] += 5; result['details'].append(f"✅ Schnell ({lt}s)")
    elif lt < 5: result['scores']['technik'] += 3; result['details'].append(f"⚠️ Mittel ({lt}s)")
    else: result['warnings'].append(f"Langsam ({lt}s)"); result['details'].append(f"❌ Langsam ({lt}s)")
    old = []
    if 'jquery-1.' in text or 'jquery/1.' in text: old.append("jQuery 1.x")
    if 'bootstrap/3' in text or 'bootstrap-3' in text: old.append("Bootstrap 3")
    if html.count('<table') > 5: old.append("Table-Layout")
    if 'flash' in text: old.append("Flash")
    if old:
        result['warnings'].append(f"Alte Technik: {', '.join(old)}")
        result['details'].append(f"⚠️ Alt: {', '.join(old)}")
        result['scores']['technik'] += 5
    else: result['scores']['technik'] += 5; result['details'].append("✅ Moderne Technik")
    imgs = soup.find_all('img')
    if imgs and sum(1 for i in imgs if not i.get('loading'))/len(imgs) > 0.5:
        result['warnings'].append("Bilder unoptimiert")
        result['details'].append(f"⚠️ {len(imgs)} Bilder, teils unoptimiert")
    else: result['scores']['technik'] += 5; result['details'].append(f"✅ {len(imgs)} Bilder OK")

    # VERTRAUEN
    if any(w in text for w in ['bewertung','review','google maps','sterne','trustpilot']):
        result['scores']['vertrauen'] += 5; result['details'].append("✅ Bewertungen")
    else: result['warnings'].append("Keine Bewertungen"); result['details'].append("❌ Keine Bewertungen")
    phones = []
    for p in [r'\+49[\s\d\-/]{6,20}', r'0\d{2,4}[\s\-/]\d{3,10}', r'tel:\+?[\d\s\-]+']:
        phones.extend(re.findall(p, text))
    if phones:
        result['telefon'] = phones[0][:30]
        result['scores']['vertrauen'] += 5; result['details'].append(f"✅ Tel: {phones[0][:20]}")
    else: result['warnings'].append("Keine Telefonnummer"); result['details'].append("❌ Keine Telefonnummer")
    if any(w in text for w in ['öffnungszeit','öffnungszeiten','montag','mo-fr']):
        result['scores']['vertrauen'] += 5; result['details'].append("✅ Öffnungszeiten")
    else: result['warnings'].append("Keine Öffnungszeiten"); result['details'].append("❌ Keine Öffnungszeiten")
    forms = soup.find_all('form')
    if forms: result['scores']['vertrauen'] += 5; result['details'].append(f"✅ {len(forms)} Formular(e)")
    else: result['warnings'].append("Kein Kontaktformular"); result['details'].append("❌ Kein Formular")

    # VERKAUF
    h1 = soup.find('h1')
    if h1 and len(h1.get_text(strip=True)) > 5:
        result['scores']['verkauf'] += 5; result['details'].append(f"✅ H1: {h1.get_text(strip=True)[:40]}")
    else: result['warnings'].append("Keine H1"); result['details'].append("❌ Keine H1")
    if any(w in text for w in ['leistung','service','angebot','preis','paket']):
        result['scores']['verkauf'] += 5; result['details'].append("✅ Leistungen")
    else: result['warnings'].append("Keine Leistungen"); result['details'].append("❌ Keine Leistungen")
    cta_w = ['termin','anrufen','kontakt','jetzt','buchen','reservieren']
    btns = soup.find_all(['a','button'])
    has_cta = any(any(w in b.get_text(strip=True).lower() for w in cta_w) for b in btns if b.get_text(strip=True))
    if has_cta: result['scores']['verkauf'] += 5; result['details'].append("✅ CTA vorhanden")
    else: result['warnings'].append("Kein CTA"); result['details'].append("❌ Kein CTA")
    if any(b in text for b in ['terminbuchung','online termin','calendly']):
        result['scores']['verkauf'] += 5; result['details'].append("✅ Online-Buchung")
    else: result['details'].append("⚠️ Keine Online-Buchung")

    # GOOGLE
    title = soup.find('title')
    result['meta_title'] = title.get_text(strip=True)[:60] if title else ''
    if title and len(title.get_text(strip=True)) > 10:
        result['scores']['google'] += 5; result['details'].append(f"✅ Title: {result['meta_title'][:35]}")
    else: result['warnings'].append("Kein Title"); result['details'].append("❌ Kein Title")
    md = soup.find('meta', attrs={'name': 'description'})
    if md and md.get('content'):
        result['meta_desc'] = md['content'][:100]
        result['scores']['google'] += 5; result['details'].append("✅ Meta-Description")
    else: result['warnings'].append("Keine Meta-Desc"); result['details'].append("❌ Keine Meta-Description")
    loc = ['köln','berlin','münchen','hamburg','frankfurt','stuttgart','düsseldorf','dresden','leipzig','kerpen','merheim']
    if any(w in text for w in loc) or any(w in result['meta_title'].lower() for w in loc):
        result['scores']['google'] += 5; result['details'].append("✅ Lokale Keywords")
    else: result['warnings'].append("Keine lokalen Keywords"); result['details'].append("⚠️ Keine Lokalisierung")
    if 'google.com/maps' in text or 'maps/embed' in text:
        result['scores']['google'] += 5; result['details'].append("✅ Google Maps")
    else: result['warnings'].append("Keine Maps"); result['details'].append("❌ Keine Maps")

    # MAIL
    mt = soup.find('a', href=re.compile(r'mailto:', re.I))
    if mt: result['mail'] = mt['href'].replace('mailto:', '').strip().split('?')[0].lower()
    if not result['mail']:
        m = re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', r.text)
        if m:
            c = m.group().lower()
            if not any(j in c for j in ['.png','.jpg','.css','.js','example','domain','wixpress']):
                result['mail'] = c

    total = sum(result['scores'].values())
    result['total_score'] = total
    age = 0
    if result['domain_age'] and result['domain_age'] < 2015: age += 2
    if old: age += 3
    if not vp: age += 2
    if result['load_time'] > 4: age += 1
    if age >= 4: result['category'] = '🔴 ALT'
    elif age >= 2: result['category'] = '🟡 VERALTET'
    elif total < 40: result['category'] = '🟡 UNVOLLSTÄNDIG'
    else: result['category'] = '🟢 MODERN'
    return result

def generate_html_report(results, filepath):
    results.sort(key=lambda x: (0 if 'ALT' in x['category'] else (1 if 'VERALTET' in x['category'] else 2), -x['total_score']))
    html = f"""<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>BenderSites Report</title><style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;padding:2rem}}.header{{text-align:center;margin-bottom:2rem}}.header h1{{font-size:2.5rem;background:linear-gradient(135deg,#f59e0b,#ef4444);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin-bottom:2rem}}.stat-card{{background:#1e293b;border-radius:12px;padding:1.5rem;text-align:center;border:1px solid #334155}}.stat-card h3{{font-size:2rem;color:#f59e0b}}.stat-card p{{color:#94a3b8;font-size:.9rem}}.filters{{display:flex;gap:.5rem;margin-bottom:1.5rem;flex-wrap:wrap}}.filter-btn{{padding:.5rem 1rem;border:1px solid #475569;background:#1e293b;color:#e2e8f0;border-radius:8px;cursor:pointer;font-size:.9rem}}.filter-btn.active{{background:#f59e0b;color:#0f172a;border-color:#f59e0b;font-weight:600}}.filter-btn:hover{{background:#334155}}.site-card{{background:#1e293b;border-radius:12px;margin-bottom:1rem;overflow:hidden;border:1px solid #334155}}.site-header{{padding:1.25rem;display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap}}.site-info{{flex:1;min-width:250px}}.site-info h2{{font-size:1.1rem;color:#f1f5f9;word-break:break-all}}.site-info a{{color:#60a5fa;text-decoration:none;font-size:.85rem}}.site-meta{{display:flex;gap:1rem;margin-top:.5rem;flex-wrap:wrap;font-size:.8rem;color:#94a3b8}}.badge{{padding:.35rem .75rem;border-radius:20px;font-size:.75rem;font-weight:600}}.badge-alt{{background:#7f1d1d;color:#fca5a5}}.badge-mittel{{background:#713f12;color:#fde047}}.badge-gut{{background:#064e3b;color:#6ee7b7}}.score-ring{{width:50px;height:50px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1rem}}.score-low{{background:#7f1d1d;color:#fca5a5}}.score-mid{{background:#713f12;color:#fde047}}.score-high{{background:#064e3b;color:#6ee7b7}}.details{{padding:0 1.25rem 1.25rem}}.score-breakdown{{display:grid;grid-template-columns:repeat(5,1fr);gap:.5rem;margin-bottom:1rem}}@media(max-width:768px){{.score-breakdown{{grid-template-columns:repeat(2,1fr)}}}}.score-item{{background:#0f172a;padding:.5rem;border-radius:8px;text-align:center}}.score-item .label{{font-size:.7rem;color:#94a3b8;text-transform:uppercase}}.score-item .value{{font-size:1rem;font-weight:700}}.detail-list{{display:grid;gap:.25rem}}.detail-item{{display:flex;align-items:center;gap:.5rem;font-size:.85rem;padding:.25rem .5rem;border-radius:6px}}.detail-item.ok{{background:rgba(16,185,129,.1);color:#6ee7b7}}.detail-item.warn{{background:rgba(245,158,11,.1);color:#fde047}}.detail-item.bad{{background:rgba(239,68,68,.1);color:#fca5a5}}.warnings{{margin-top:.75rem;padding:.75rem;background:#450a0a;border-radius:8px;border-left:3px solid #ef4444}}.warnings h4{{color:#fca5a5;font-size:.8rem;margin-bottom:.25rem}}.warnings ul{{list-style:none;font-size:.8rem;color:#fecaca}}.warnings li{{padding:.1rem 0}}.warnings li::before{{content:'⚠️ '}}.hidden{{display:none!important}}.export-btn{{position:fixed;bottom:2rem;right:2rem;background:#f59e0b;color:#0f172a;padding:1rem 1.5rem;border-radius:50px;font-weight:700;text-decoration:none;box-shadow:0 4px 20px rgba(245,158,11,.3);border:none;cursor:pointer;font-size:1rem}}.export-btn:hover{{transform:scale(1.05)}}</style></head><body><div class="header"><h1>🔍 BenderSites Scanner</h1><p style="color:#94a3b8">{datetime.now().strftime('%d.%m.%Y %H:%M')}</p></div><div class="stats"><div class="stat-card"><h3>{len(results)}</h3><p>Gescannt</p></div><div class="stat-card"><h3>{sum(1 for r in results if 'ALT' in r['category'])}</h3><p>🔴 Alt</p></div><div class="stat-card"><h3>{sum(1 for r in results if 'VERALTET' in r['category'] or 'UNVOLLSTÄNDIG' in r['category'])}</h3><p>🟡 Mittel</p></div><div class="stat-card"><h3>{sum(1 for r in results if 'MODERN' in r['category'])}</h3><p>🟢 Modern</p></div></div><div class="filters"><button class="filter-btn active" onclick="filter('all')">Alle</button><button class="filter-btn" onclick="filter('alt')">🔴 Alt</button><button class="filter-btn" onclick="filter('mittel')">🟡 Mittel</button><button class="filter-btn" onclick="filter('gut')">🟢 Gut</button></div><div id="sites-container">"""
    for r in results:
        sc = 'score-low' if r['total_score']<40 else ('score-mid' if r['total_score']<70 else 'score-high')
        bc = 'badge-alt' if 'ALT' in r['category'] else ('badge-mittel' if 'VERALTET' in r['category'] or 'UNVOLLSTÄNDIG' in r['category'] else 'badge-gut')
        dc = ''.join(f'<div class="detail-item {"ok" if d.startswith("✅") else ("warn" if d.startswith("⚠️") else "bad")}">{d}</div>' for d in r['details'])
        wc = '<div class="warnings"><h4>Probleme</h4><ul>' + ''.join(f'<li>{w}</li>' for w in r['warnings']) + '</ul></div>' if r['warnings'] else ''
        html += f'''<div class="site-card" data-category="{'alt' if 'ALT' in r['category'] else ('mittel' if 'VERALTET' in r['category'] or 'UNVOLLSTÄNDIG' in r['category'] else 'gut')}"><div class="site-header"><div class="site-info"><h2>{r['url'].replace('https://','').replace('http://','')[:50]}</h2><a href="{r['url']}" target="_blank">{r['url']}</a><div class="site-meta"><span>📧 {r['mail'] or 'keine Mail'}</span><span>📞 {r['telefon'] or 'kein Tel'}</span><span>⏱️ {r['load_time']}s</span><span>📅 {r['domain_age'] or '?'}</span></div></div><div class="score-ring {sc}">{r['total_score']}</div><span class="badge {bc}">{r['category']}</span></div><div class="details"><div class="score-breakdown"><div class="score-item"><div class="label">Recht</div><div class="value" style="color:{'#fca5a5' if r['scores']['rechtliches']<10 else '#6ee7b7'}">{r['scores']['rechtliches']}/20</div></div><div class="score-item"><div class="label">Technik</div><div class="value" style="color:{'#fca5a5' if r['scores']['technik']<10 else '#6ee7b7'}">{r['scores']['technik']}/20</div></div><div class="score-item"><div class="label">Vertrauen</div><div class="value" style="color:{'#fca5a5' if r['scores']['vertrauen']<10 else '#6ee7b7'}">{r['scores']['vertrauen']}/20</div></div><div class="score-item"><div class="label">Verkauf</div><div class="value" style="color:{'#fca5a5' if r['scores']['verkauf']<10 else '#6ee7b7'}">{r['scores']['verkauf']}/20</div></div><div class="score-item"><div class="label">Google</div><div class="value" style="color:{'#fca5a5' if r['scores']['google']<10 else '#6ee7b7'}">{r['scores']['google']}/20</div></div></div><div class="detail-list">{dc}</div>{wc}</div></div>'''
    html += '''</div><button class="export-btn" onclick="exportCSV()">📥 CSV</button><script>function filter(cat){document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));event.target.classList.add('active');document.querySelectorAll('.site-card').forEach(c=>{if(cat==='all'||c.dataset.category===cat)c.classList.remove('hidden');else c.classList.add('hidden')})}function exportCSV(){const rows=[];rows.push('URL;Kategorie;Score;Recht;Technik;Vertrauen;Verkauf;Google;Mail;Telefon;Domainalter;Ladezeit;Probleme');document.querySelectorAll('.site-card:not(.hidden)').forEach(c=>{const url=c.querySelector('a').href;const cat=c.querySelector('.badge').textContent;const score=c.querySelector('.score-ring').textContent;const scores=Array.from(c.querySelectorAll('.score-item .value')).map(s=>s.textContent.split('/')[0]);const meta=c.querySelectorAll('.site-meta span');const probs=Array.from(c.querySelectorAll('.warnings li')).map(l=>l.textContent).join(' | ');rows.push(`${url};${cat};${score};${scores.join(';')};${meta[0]?.textContent.replace('📧 ','')||''};${meta[1]?.textContent.replace('📞 ','')||''};${meta[3]?.textContent.replace('📅 ','')||''};${meta[2]?.textContent.replace('⏱️ ','')||''};${probs}`)});const blob=new Blob([rows.join('\\n')],{type:'text/csv'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='bender-scan.csv';a.click()}</script></body></html>'''
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)
    return filepath

def run_scan(filepath):
    global scan_progress
    scan_progress = {'running': True, 'total': 0, 'current': 0, 'url': '', 'results': [], 'report_file': None}
    urls = []
    with open(filepath, 'r', encoding='utf-8') as f:
        sample = f.read(2048); f.seek(0)
        delimiter = ';' if sample.count(';') > sample.count(',') else ','
        f.seek(0)
        for row in csv.DictReader(f, delimiter=delimiter):
            site = row.get('Website', row.get('website', row.get('URL', row.get('url', '')))).strip()
            if site and site.startswith('http') and not any(d in site for d in SKIP_DOMAINS):
                urls.append(site)
    scan_progress['total'] = len(urls)
    for i, url in enumerate(urls):
        scan_progress['current'] = i + 1; scan_progress['url'] = url[:60]
        try:
            scan_progress['results'].append(analyze_website(url))
        except Exception as e:
            scan_progress['results'].append({'url': url, 'category': '❌ Fehler', 'total_score': 0, 'scores': {}, 'details': [], 'warnings': [str(e)], 'mail': None, 'telefon': None, 'domain_age': None, 'load_time': 0})
        time.sleep(0.3)
    rp = os.path.join(app.config['REPORTS_FOLDER'], f'report_{int(time.time())}.html')
    generate_html_report(scan_progress['results'], rp)
    scan_progress['report_file'] = rp
    scan_progress['running'] = False

@app.route('/')
def index():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'Keine Datei'}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        return jsonify({'error': 'Nur CSV-Dateien'}), 400
    fp = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(fp)
    threading.Thread(target=run_scan, args=(fp,), daemon=True).start()
    return jsonify({'status': 'started'})

@app.route('/status')
def status():
    return jsonify({
        'running': scan_progress['running'],
        'total': scan_progress['total'],
        'current': scan_progress['current'],
        'url': scan_progress['url'],
        'percent': round(scan_progress['current'] / scan_progress['total'] * 100, 1) if scan_progress['total'] > 0 else 0
    })

@app.route('/report')
def report():
    if scan_progress['report_file'] and os.path.exists(scan_progress['report_file']):
        return send_file(scan_progress['report_file'])
    return jsonify({'error': 'Noch kein Report'}), 404

@app.route('/download-csv')
def download_csv():
    if not scan_progress['results']:
        return jsonify({'error': 'Keine Daten'}), 404
    cp = os.path.join(app.config['REPORTS_FOLDER'], 'export.csv')
    with open(cp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['URL','Kategorie','Score','Recht','Technik','Vertrauen','Verkauf','Google','Mail','Telefon','Domainalter','Ladezeit'])
        for r in scan_progress['results']:
            w.writerow([r['url'], r['category'], r['total_score'], r['scores'].get('rechtliches',0), r['scores'].get('technik',0), r['scores'].get('vertrauen',0), r['scores'].get('verkauf',0), r['scores'].get('google',0), r['mail'] or '', r['telefon'] or '', r['domain_age'] or '', r['load_time']])
    return send_file(cp, as_attachment=True, download_name='bender-sites-scan.csv')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
