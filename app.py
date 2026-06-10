#!/usr/bin/env python3
"""
BenderSites Scanner v3
Fokus: VISUELLES ALTER der Website
"""

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


def analyze_visual_age(soup, html, css_text):
    """
    Analysiert das VISUELLE Alter einer Website.
    Returns: (score_0_100, details_list, era_guess)
    score: 0 = steinzeit, 100 = modern
    """
    score = 50  # Start neutral
    details = []
    era = "unbekannt"
    
    # ─── CSS ANALYSE ──────────────────────────────────────────────
    # Flexbox / Grid = modern
    has_flex = 'display:flex' in css_text or 'display: flex' in css_text
    has_grid = 'display:grid' in css_text or 'display: grid' in css_text
    if has_flex: score += 8; details.append("✅ Flexbox")
    if has_grid: score += 10; details.append("✅ CSS Grid")
    if not has_flex and not has_grid:
        score -= 10; details.append("❌ Kein Flexbox/Grid = fixe Layouts")
    
    # Float-basiert = alt
    floats = css_text.count('float:') + css_text.count('float :')
    if floats > 5:
        score -= 15; details.append(f"❌ Float-Layout ({floats}x) = sehr alt")
    elif floats > 0:
        score -= 5; details.append(f"⚠️ Float verwendet ({floats}x)")
    
    # Position absolute überall = alt
    abs_pos = css_text.count('position:absolute') + css_text.count('position: absolute')
    if abs_pos > 10:
        score -= 10; details.append(f"❌ Viel position:absolute ({abs_pos}x)")
    
    # ─── FARBEN & VERLÄUFE ───────────────────────────────────────
    # Gradienten = 2010-2015 Stil (alt)
    gradients = len(re.findall(r'linear-gradient|radial-gradient', css_text, re.I))
    if gradients > 3:
        score -= 10; details.append(f"❌ Viele Gradienten ({gradients}) = Web 2.0 Look")
    elif gradients > 0:
        score -= 3; details.append(f"⚠️ Gradienten ({gradients})")
    
    # Box-shadow überall = 2010er
    shadows = len(re.findall(r'box-shadow', css_text, re.I))
    if shadows > 5:
        score -= 8; details.append(f"❌ Viele Schatten ({shadows}) = altbacken")
    
    # Border-radius = modern (aber seit 2010 verfügbar, also neutral)
    radius = len(re.findall(r'border-radius', css_text, re.I))
    if radius == 0:
        score -= 5; details.append("❌ Keine abgerundeten Ecken = kantig/alt")
    
    # ─── SCHRIFTEN ────────────────────────────────────────────────
    # Custom Fonts = modern
    has_webfonts = '@font-face' in css_text or 'fonts.googleapis' in html
    if has_webfonts:
        score += 5; details.append("✅ Custom Fonts")
    else:
        score -= 5; details.append("❌ System-Fonts nur = langweilig/alt")
    
    # Times New Roman / Arial = sehr alt
    if 'times new roman' in html.lower() or 'font-family:times' in css_text.lower():
        score -= 15; details.append("❌ Times New Roman = 90er/2000er")
    if 'font-family:arial' in css_text.lower() and not has_webfonts:
        score -= 5; details.append("⚠️ Arial als Hauptfont")
    
    # ─── LAYOUT & STRUKTUR ──────────────────────────────────────
    # Viewport = modern
    viewport = soup.find('meta', attrs={'name': 'viewport'})
    if not viewport:
        score -= 20; details.append("❌ KEIN Viewport = NICHT mobil = sehr alt")
    
    # Fixe Breite = alt
    fixed_width = len(re.findall(r'width:\s*\d+px', css_text))
    if fixed_width > 3:
        score -= 15; details.append(f"❌ Fixe Pixel-Breiten ({fixed_width}x) = nicht responsive")
    
    # Max-width / Container mit % = modern
    if 'max-width' in css_text:
        score += 5; details.append("✅ Max-width verwendet")
    
    # ─── BILDER ───────────────────────────────────────────────────
    images = soup.find_all('img')
    if images:
        # Große Hero-Bilder = modern
        big_images = sum(1 for img in images if img.get('width') in ['100%', 'auto'] or 'hero' in str(img.get('class',[])).lower())
        if big_images >= 1:
            score += 5; details.append("✅ Große Bilder/Full-width")
        
        # Kleine Thumbnails = alt
        small_imgs = sum(1 for img in images if img.get('width') and int(re.search(r'\d+', str(img.get('width','0')) or '0') or 0) < 150)
        if small_imgs > len(images) * 0.5:
            score -= 10; details.append("❌ Viele kleine Bilder = alt")
        
        # Lazy loading = modern
        lazy = sum(1 for img in images if img.get('loading') == 'lazy')
        if lazy > 0:
            score += 5; details.append(f"✅ Lazy Loading ({lazy})")
    
    # ─── HTML STRUKTUR ────────────────────────────────────────────
    # Semantische Tags = modern
    semantic = ['<header', '<nav', '<main', '<section', '<article', '<footer']
    sem_count = sum(html.lower().count(tag) for tag in semantic)
    if sem_count >= 3:
        score += 8; details.append(f"✅ Semantisches HTML ({sem_count})")
    else:
        score -= 5; details.append("⚠️ Kein semantisches HTML = div-Suppe")
    
    # Div-Suppe = alt
    divs = html.lower().count('<div')
    if divs > 50:
        score -= 5; details.append(f"⚠️ Viele divs ({divs})")
    
    # Tabellen für Layout = sehr alt
    tables = html.lower().count('<table')
    if tables > 2:
        score -= 20; details.append(f"❌ Table-Layout ({tables}) = Steinzeit")
    elif tables > 0:
        score -= 5; details.append(f"⚠️ Tabellen ({tables})")
    
    # ─── ANIMATIONEN ──────────────────────────────────────────────
    # CSS Transitions/Animations = modern
    has_anim = 'transition' in css_text or 'animation' in css_text or '@keyframes' in css_text
    if has_anim:
        score += 5; details.append("✅ CSS Animationen")
    
    # Marquee / Blink = sehr alt
    if '<marquee' in html.lower():
        score -= 30; details.append("❌ MARQUEE = 90er!")
    if '<blink' in html.lower():
        score -= 30; details.append("❌ BLINK = 90er!")
    
    # ─── FRAMEWORKS ───────────────────────────────────────────────
    # Bootstrap 3 = alt
    if 'bootstrap/3' in html or 'bootstrap-3' in html:
        score -= 10; details.append("❌ Bootstrap 3")
    # Bootstrap 4+ = ok
    elif 'bootstrap' in html:
        score += 0; details.append("ℹ️ Bootstrap (Version unklar)")
    
    # jQuery 1.x = alt
    if 'jquery-1.' in html or 'jquery/1.' in html:
        score -= 10; details.append("❌ jQuery 1.x")
    # jQuery aktuell = neutral
    elif 'jquery' in html:
        score -= 2; details.append("ℹ️ jQuery vorhanden")
    
    # ─── WHITESPACE & LUFT ────────────────────────────────────────
    # Padding/Margin viel = modern (luftig)
    padding_count = len(re.findall(r'padding:\s*\d+', css_text))
    if padding_count > 10:
        score += 5; details.append("✅ Viel Padding = luftiges Design")
    elif padding_count < 3:
        score -= 5; details.append("❌ Wenig Padding = gequetscht")
    
    # ─── SCORE BEGRENZEN ──────────────────────────────────────────
    score = max(0, min(100, score))
    
    # Ära bestimmen
    if score < 25:
        era = "🔴 2005-2010 (Steinzeit)"
    elif score < 45:
        era = "🟠 2010-2015 (Web 2.0 / Alt)"
    elif score < 70:
        era = "🟡 2015-2019 (Mittel / Ok)"
    else:
        era = "🟢 2020+ (Modern)"
    
    return score, details, era


def analyze_website(url):
    result = {
        'url': url, 'reachable': False, 'domain_age': None,
        'visual_score': 0, 'visual_era': '', 'visual_details': [],
        'problems': [], 'opportunities': [],
        'mail': None, 'telefon': None,
        'has_ssl': False, 'load_time': 0,
        'category': 'unbekannt', 'total_score': 0
    }
    
    if not url.startswith('http'):
        url = 'https://' + url
    domain = urlparse(url).netloc or urlparse(url).path
    
    result['has_ssl'] = url.startswith('https://')
    if not result['has_ssl']:
        result['problems'].append("Kein HTTPS")
    
    result['domain_age'] = get_domain_age(domain)
    
    start_time = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        result['load_time'] = round(time.time() - start_time, 2)
        result['reachable'] = True
        html = r.text
        soup = BeautifulSoup(html, 'html.parser')
    except Exception as e:
        result['problems'].append(f"Nicht erreichbar: {str(e)[:50]}")
        return result
    
    # ─── CSS SAMMELN ──────────────────────────────────────────────
    css_text = ""
    # Inline styles
    styles = soup.find_all('style')
    for s in styles:
        css_text += s.get_text() + "\n"
    # External CSS (nur erste 3, sonst zu langsam)
    links = soup.find_all('link', rel='stylesheet')[:3]
    for link in links:
        href = link.get('href')
        if href:
            try:
                if href.startswith('http'):
                    css_url = href
                elif href.startswith('//'):
                    css_url = 'https:' + href
                else:
                    css_url = url.rstrip('/') + ('' if href.startswith('/') else '/') + href
                cr = requests.get(css_url, headers=HEADERS, timeout=8)
                css_text += cr.text[:5000] + "\n"  # Max 5KB pro CSS
            except: pass
    
    # ─── VISUELLE ANALYSE ─────────────────────────────────────────
    visual_score, visual_details, era = analyze_visual_age(soup, html, css_text)
    result['visual_score'] = visual_score
    result['visual_era'] = era
    result['visual_details'] = visual_details
    
    # ─── PROBLEME FÜR VERKAUFSARGUMENTE ───────────────────────────
    # (Nur sammeln, nicht für Kategorie)
    
    # Kein Impressum
    has_imp = any(p in html for p in ['/impressum','impressum','imprint']) or soup.find('a', href=re.compile(r'impressum|imprint', re.I))
    if not has_imp: result['opportunities'].append("Kein Impressum sichtbar")
    
    # Kein CTA
    cta_w = ['termin','anrufen','jetzt buchen','reservieren','kontakt']
    btns = soup.find_all(['a','button'])
    has_cta = any(any(w in b.get_text(strip=True).lower() for w in cta_w) for b in btns if b.get_text(strip=True))
    if not has_cta: result['opportunities'].append("Kein Call-to-Action")
    
    # Keine Bewertungen
    if not any(w in html.lower() for w in ['bewertung','review','sterne','google maps']):
        result['opportunities'].append("Keine Bewertungen sichtbar")
    
    # Keine Öffnungszeiten
    if not any(w in html.lower() for w in ['öffnungszeit','mo-fr','montag']):
        result['opportunities'].append("Keine Öffnungszeiten")
    
    # Langsam
    if result['load_time'] > 4:
        result['opportunities'].append(f"Langsame Ladezeit ({result['load_time']}s)")
    
    # Nicht mobil
    if not soup.find('meta', attrs={'name': 'viewport'}):
        result['opportunities'].append("NICHT mobil optimiert")
    
    # ─── MAIL & TELEFON ───────────────────────────────────────────
    mt = soup.find('a', href=re.compile(r'mailto:', re.I))
    if mt: result['mail'] = mt['href'].replace('mailto:', '').strip().split('?')[0].lower()
    if not result['mail']:
        m = re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html)
        if m:
            c = m.group().lower()
            if not any(j in c for j in ['.png','.jpg','.css','.js','example','domain','wixpress']):
                result['mail'] = c
    
    phones = []
    for p in [r'\+49[\s\d\-/]{6,20}', r'0\d{2,4}[\s\-/]\d{3,10}', r'tel:\+?[\d\s\-]+']:
        phones.extend(re.findall(p, html))
    if phones: result['telefon'] = phones[0][:30]
    
    # ─── KATEGORIE (NUR nach visuellem Alter) ─────────────────────
    result['total_score'] = visual_score
    
    if visual_score < 25:
        result['category'] = '🔴 SEHR ALT (2005-2010)'
    elif visual_score < 45:
        result['category'] = '🟠 ALT (2010-2015)'
    elif visual_score < 70:
        result['category'] = '🟡 MITTEL (2015-2019)'
    else:
        result['category'] = '🟢 MODERN (2020+)'
    
    return result


def generate_html_report(results, filepath):
    # Sortieren: Sehr Alt zuerst, dann Alt, dann Mittel, dann Modern
    order = {'🔴 SEHR ALT (2005-2010)': 0, '🟠 ALT (2010-2015)': 1, '🟡 MITTEL (2015-2019)': 2, '🟢 MODERN (2020+)': 3, '❌ Fehler': 99}
    results.sort(key=lambda x: (order.get(x['category'], 5), -x['total_score']))
    
    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BenderSites Scanner - Visuelles Alter</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;padding:2rem}}
.header{{text-align:center;margin-bottom:2rem}}
.header h1{{font-size:2.5rem;background:linear-gradient(135deg,#f59e0b,#ef4444);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.header p{{color:#94a3b8;margin-top:.5rem}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin-bottom:2rem}}
.stat-card{{background:#1e293b;border-radius:12px;padding:1.5rem;text-align:center;border:1px solid #334155}}
.stat-card h3{{font-size:2rem;color:#f59e0b}}
.stat-card p{{color:#94a3b8;font-size:.85rem}}
.filters{{display:flex;gap:.5rem;margin-bottom:1.5rem;flex-wrap:wrap}}
.filter-btn{{padding:.5rem 1rem;border:1px solid #475569;background:#1e293b;color:#e2e8f0;border-radius:8px;cursor:pointer;font-size:.9rem}}
.filter-btn.active{{background:#f59e0b;color:#0f172a;border-color:#f59e0b;font-weight:600}}
.filter-btn:hover{{background:#334155}}
.site-card{{background:#1e293b;border-radius:12px;margin-bottom:1rem;overflow:hidden;border:1px solid #334155}}
.site-header{{padding:1.25rem;display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap}}
.site-info{{flex:1;min-width:250px}}
.site-info h2{{font-size:1.1rem;color:#f1f5f9;word-break:break-all}}
.site-info a{{color:#60a5fa;text-decoration:none;font-size:.85rem}}
.site-meta{{display:flex;gap:1rem;margin-top:.5rem;flex-wrap:wrap;font-size:.8rem;color:#94a3b8}}
.badge{{padding:.35rem .75rem;border-radius:20px;font-size:.75rem;font-weight:600}}
.badge-sehr-alt{{background:#450a0a;color:#fca5a5;border:1px solid #7f1d1d}}
.badge-alt{{background:#7f2e0d;color:#fdba74;border:1px solid #9a3412}}
.badge-mittel{{background:#713f12;color:#fde047;border:1px solid #a16207}}
.badge-modern{{background:#064e3b;color:#6ee7b7;border:1px solid #059669}}
.score-ring{{width:55px;height:55px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.9rem}}
.score-sehr-alt{{background:#450a0a;color:#fca5a5}}
.score-alt{{background:#7f2e0d;color:#fdba74}}
.score-mittel{{background:#713f12;color:#fde047}}
.score-modern{{background:#064e3b;color:#6ee7b7}}
.details{{padding:0 1.25rem 1.25rem}}
.era-label{{font-size:.9rem;color:#f59e0b;font-weight:600;margin-bottom:.75rem;padding:.5rem 0;border-bottom:1px solid #334155}}
.visual-details{{display:grid;gap:.25rem;margin-bottom:1rem}}
.detail-item{{display:flex;align-items:center;gap:.5rem;font-size:.85rem;padding:.25rem .5rem;border-radius:6px}}
.detail-item.ok{{background:rgba(16,185,129,.1);color:#6ee7b7}}
.detail-item.warn{{background:rgba(245,158,11,.1);color:#fde047}}
.detail-item.bad{{background:rgba(239,68,68,.1);color:#fca5a5}}
.opportunities{{margin-top:.75rem;padding:.75rem;background:#1e3a5f;border-radius:8px;border-left:3px solid #3b82f6}}
.opportunities h4{{color:#93c5fd;font-size:.85rem;margin-bottom:.5rem}}
.opportunities ul{{list-style:none;font-size:.85rem;color:#bfdbfe}}
.opportunities li{{padding:.15rem 0}}
.opportunities li::before{{content:'💡 '}}
.hidden{{display:none!important}}
.export-btn{{position:fixed;bottom:2rem;right:2rem;background:#f59e0b;color:#0f172a;padding:1rem 1.5rem;border-radius:50px;font-weight:700;text-decoration:none;box-shadow:0 4px 20px rgba(245,158,11,.3);border:none;cursor:pointer;font-size:1rem}}
.export-btn:hover{{transform:scale(1.05)}}
.score-bar{{width:100%;height:6px;background:#0f172a;border-radius:3px;margin-top:.5rem;overflow:hidden}}
.score-fill{{height:100%;border-radius:3px;transition:width .3s}}
</style>
</head>
<body>
<div class="header"><h1>🔍 BenderSites Scanner</h1><p>Sortiert nach VISUELLEM Alter | Deine Ziele: 🔴 SEHR ALT und 🟠 ALT</p></div>
<div class="stats">
<div class="stat-card"><h3>{len(results)}</h3><p>Gescannt</p></div>
<div class="stat-card"><h3>{sum(1 for r in results if 'SEHR ALT' in r['category'])}</h3><p>🔴 Sehr Alt</p></div>
<div class="stat-card"><h3>{sum(1 for r in results if 'ALT (2010' in r['category'])}</h3><p>🟠 Alt</p></div>
<div class="stat-card"><h3>{sum(1 for r in results if 'MITTEL' in r['category'])}</h3><p>🟡 Mittel</p></div>
<div class="stat-card"><h3>{sum(1 for r in results if 'MODERN' in r['category'])}</h3><p>🟢 Modern</p></div>
</div>
<div class="filters">
<button class="filter-btn active" onclick="filter('all')">Alle</button>
<button class="filter-btn" onclick="filter('sehr-alt')">🔴 Sehr Alt</button>
<button class="filter-btn" onclick="filter('alt')">🟠 Alt</button>
<button class="filter-btn" onclick="filter('mittel')">🟡 Mittel</button>
<button class="filter-btn" onclick="filter('modern')">🟢 Modern</button>
</div>
<div id="sites-container">"""

    for r in results:
        bc = 'badge-sehr-alt' if 'SEHR ALT' in r['category'] else ('badge-alt' if 'ALT (2010' in r['category'] else ('badge-mittel' if 'MITTEL' in r['category'] else 'badge-modern'))
        sc = 'score-sehr-alt' if 'SEHR ALT' in r['category'] else ('score-alt' if 'ALT (2010' in r['category'] else ('score-mittel' if 'MITTEL' in r['category'] else 'score-modern'))
        fill_color = '#ef4444' if 'SEHR ALT' in r['category'] else ('#f97316' if 'ALT (2010' in r['category'] else ('#eab308' if 'MITTEL' in r['category'] else '#10b981'))
        dc = ''.join(f'<div class="detail-item {"ok" if d.startswith("✅") else ("warn" if d.startswith("⚠️") or d.startswith("ℹ️") else "bad")}">{d}</div>' for d in r['visual_details'])
        oc = '<div class="opportunities"><h4>💡 Verkaufsargumente</h4><ul>' + ''.join(f'<li>{o}</li>' for o in r['opportunities']) + '</ul></div>' if r['opportunities'] else ''
        
        html += f'''<div class="site-card" data-category="{'sehr-alt' if 'SEHR ALT' in r['category'] else ('alt' if 'ALT (2010' in r['category'] else ('mittel' if 'MITTEL' in r['category'] else 'modern'))}"><div class="site-header"><div class="site-info"><h2>{r['url'].replace('https://','').replace('http://','')[:50]}</h2><a href="{r['url']}" target="_blank">{r['url']}</a><div class="site-meta"><span>📧 {r['mail'] or 'keine Mail'}</span><span>📞 {r['telefon'] or 'kein Tel'}</span><span>⏱️ {r['load_time']}s</span><span>📅 Domain: {r['domain_age'] or '?'}</span></div><div class="score-bar"><div class="score-fill" style="width:{r['visual_score']}%;background:{fill_color}"></div></div></div><div class="score-ring {sc}">{r['visual_score']}</div><span class="badge {bc}">{r['category']}</span></div><div class="details"><div class="era-label">{r['visual_era']} | Visueller Score: {r['visual_score']}/100</div><div class="visual-details">{dc}</div>{oc}</div></div>'''

    html += '''</div><button class="export-btn" onclick="exportCSV()">📥 CSV Export</button>
<script>
function filter(cat){document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));event.target.classList.add('active');document.querySelectorAll('.site-card').forEach(c=>{if(cat==='all'||c.dataset.category===cat)c.classList.remove('hidden');else c.classList.add('hidden')})}
function exportCSV(){const rows=[];rows.push('URL;Kategorie;VisuellerScore;VisuelleAera;Mail;Telefon;Domainalter;Ladezeit;Verkaufsargumente');document.querySelectorAll('.site-card:not(.hidden)').forEach(c=>{const url=c.querySelector('a').href;const cat=c.querySelector('.badge').textContent;const score=c.querySelector('.score-ring').textContent;const era=c.querySelector('.era-label').textContent.split('|')[0].trim();const meta=c.querySelectorAll('.site-meta span');const ops=Array.from(c.querySelectorAll('.opportunities li')).map(l=>l.textContent).join(' | ');rows.push(`${url};${cat};${score};${era};${meta[0]?.textContent.replace('📧 ','')||''};${meta[1]?.textContent.replace('📞 ','')||''};${meta[3]?.textContent.replace('📅 Domain: ','')||''};${meta[2]?.textContent.replace('⏱️ ','')||''};${ops}`)});const blob=new Blob([rows.join('\\n')],{type:'text/csv'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='bender-sites-scan.csv';a.click()}
</script></body></html>'''
    
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
            scan_progress['results'].append({'url': url, 'category': '❌ Fehler', 'total_score': 0, 'visual_score': 0, 'visual_era': 'Fehler', 'visual_details': [], 'opportunities': [str(e)], 'mail': None, 'telefon': None, 'domain_age': None, 'load_time': 0})
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
        w.writerow(['URL','Kategorie','VisuellerScore','VisuelleAera','Mail','Telefon','Domainalter','Ladezeit','Verkaufsargumente'])
        for r in scan_progress['results']:
            w.writerow([r['url'], r['category'], r['visual_score'], r['visual_era'], r['mail'] or '', r['telefon'] or '', r['domain_age'] or '', r['load_time'], ' | '.join(r['opportunities'])])
    return send_file(cp, as_attachment=True, download_name='bender-sites-scan.csv')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
