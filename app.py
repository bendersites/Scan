#!/usr/bin/env python3
"""
BenderSites Scanner v3.2
Fokus: VISUELLES ALTER der Website
"""

import os, csv, re, time, subprocess, threading
from pathlib import Path
from urllib.parse import urlparse, urljoin
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

scan_progress = {
    'running': False,
    'total': 0,
    'current': 0,
    'url': '',
    'results': [],
    'report_file': None
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"
}

SKIP_DOMAINS = ['facebook', 'instagram', 'maps.google', 'fleurop',
                'gelbeseiten', 'etsy', 'blume2000', 'blumen-risse', 'google']


def get_domain_age(domain):
    try:
        result = subprocess.run(['whois', domain], capture_output=True, text=True, timeout=5)
        for pat in [r'Creation Date:\s*(\d{4})', r'created:\s*(\d{4})', r'Registered on:\s*(\d{4})']:
            m = re.search(pat, result.stdout, re.I)
            if m:
                return int(m.group(1))
    except:
        pass
    return None


def analyze_visual_age(soup, html, url):
    score = 50
    details = []
    
    # CSS laden (inline + extern)
    full_css = ""
    for s in soup.find_all('style'):
        full_css += s.get_text() + "\n"
    
    base_url = url if url.startswith('http') else 'https://' + url
    for link in soup.find_all('link', rel='stylesheet'):
        href = link.get('href', '')
        if not href:
            continue
        css_url = urljoin(base_url, href)
        try:
            cr = requests.get(css_url, headers=HEADERS, timeout=8)
            if cr.status_code == 200:
                full_css += cr.text[:8000] + "\n"
        except:
            pass
    
    full_css_lower = full_css.lower()
    
    # Viewport
    viewport = soup.find('meta', attrs={'name': 'viewport'})
    if not viewport:
        score -= 20
        details.append("❌ KEIN Viewport = fixe Breite = alt")
    else:
        content = viewport.get('content', '')
        if 'width=device-width' in content:
            score += 10
            details.append("✅ Responsive")
        else:
            score += 3
            details.append("⚠️ Viewport komisch")
    
    # Bilder
    images = soup.find_all('img')
    total_imgs = len(images)
    
    if total_imgs == 0:
        score -= 15
        details.append("❌ Keine Bilder = Text-Only")
    else:
        big_images = 0
        data_uris = 0
        
        for img in images:
            src = img.get('src', '')
            if src.startswith('data:image'):
                data_uris += 1
                continue
            
            w = img.get('width', '')
            style = img.get('style', '').lower()
            cls = ' '.join(img.get('class', [])).lower()
            
            is_big = (
                w in ['100%', 'auto', '100vw'] or
                'width:100%' in style or 'width: 100%' in style or
                any(x in cls for x in ['hero', 'banner', 'fullscreen', 'cover', 'bg'])
            )
            
            if is_big:
                big_images += 1
        
        # CSS Background-Images als große Bilder zählen
        bg_image_count = len(re.findall(r'background(?:-image)?\s*:[^;]*url\s*\(', full_css_lower))
        bg_inline = len([t for t in soup.find_all(True) if 'background-image' in t.get('style', '').lower()])
        big_images += min(bg_image_count + bg_inline, 3)
        
        if data_uris > 2:
            score -= 8
            details.append(f"❌ {data_uris} Base64-Bilder")
        
        if big_images >= 2:
            score += 15
            details.append(f"✅ {big_images} große Bilder = modern")
        elif big_images == 1:
            score += 5
            details.append(f"⚠️ 1 großes Bild")
        else:
            score -= 10
            details.append(f"❌ Nur kleine Bilder = alt")
        
        svgs = len(soup.find_all('svg'))
        if svgs > 0:
            score += 8
            details.append(f"✅ SVG ({svgs})")
        
        lazy = sum(1 for img in images if img.get('loading') == 'lazy')
        if lazy > 0:
            score += 5
            details.append("✅ Lazy Loading")
        
        pictures = len(soup.find_all('picture'))
        if pictures > 0:
            score += 5
            details.append(f"✅ Picture-Tag ({pictures})")
    
    # HTML Struktur
    semantic = ['header', 'nav', 'main', 'section', 'article', 'footer', 'aside']
    sem_count = sum(1 for tag in semantic if soup.find(tag))
    if sem_count >= 3:
        score += 10
        details.append(f"✅ Semantisch ({sem_count})")
    elif sem_count >= 2:
        score += 3
        details.append(f"⚠️ Semantisch ({sem_count})")
    else:
        score -= 10
        details.append("❌ Kein semantisches HTML")
    
    divs = len(soup.find_all('div'))
    uses_framework = any(x in html for x in ['tailwind', 'cdn.tailwindcss', 'bootstrap', 'bulma', 'foundation'])
    if divs > 100 and not uses_framework:
        score -= 5
        details.append(f"⚠️ Div-Suppe ({divs})")
    elif divs > 100 and uses_framework:
        details.append(f"ℹ️ Div-Suppe ({divs}) – Framework-generiert")
    
    tables = len(soup.find_all('table'))
    if tables > 2:
        score -= 20
        details.append(f"❌ Tables ({tables})")
    elif tables > 0:
        score -= 5
        details.append(f"⚠️ Tables ({tables})")
    
    # CSS Layout
    has_flex = 'display:flex' in full_css_lower or 'display: flex' in full_css_lower
    has_grid = 'display:grid' in full_css_lower or 'display: grid' in full_css_lower
    
    if has_flex:
        score += 8
        details.append("✅ Flexbox")
    if has_grid:
        score += 12
        details.append("✅ CSS Grid")
    
    if not has_flex and not has_grid:
        floats = full_css_lower.count('float:')
        abs_pos = full_css_lower.count('position:absolute') + full_css_lower.count('position: absolute')
        if floats > 3:
            score -= 15
            details.append(f"❌ Float-Layout ({floats}x)")
        elif abs_pos > 5:
            score -= 10
            details.append(f"❌ Viel absolute Positionierung")
        else:
            score -= 5
            details.append("⚠️ Kein modernes Layout")
    
    fixed_px = len(re.findall(r'width:\s*\d{3,4}px', full_css_lower))
    uses_framework = any(x in html for x in ['tailwind', 'cdn.tailwindcss', 'bootstrap', 'bulma', 'foundation'])
    if fixed_px > 5 and not uses_framework:
        score -= 15
        details.append(f"❌ Fixe Breiten ({fixed_px})")
    elif fixed_px > 5 and uses_framework:
        details.append(f"ℹ️ Fixe Breiten ({fixed_px}) – Framework-generiert")
    
    if 'max-width' in full_css_lower:
        score += 3
        details.append("✅ Max-width")
    
    # Schriften
    has_webfonts = 'fonts.googleapis' in html or '@font-face' in full_css_lower
    if has_webfonts:
        score += 5
        details.append("✅ Webfonts")
    elif 'font-family:arial' in full_css_lower or 'font-family: times' in full_css_lower:
        score -= 5
        details.append("⚠️ System-Fonts")
    
    # Design
    gradients = len(re.findall(r'linear-gradient|radial-gradient', full_css_lower))
    if gradients > 15:
        score -= 5
        details.append(f"⚠️ Viele Gradienten ({gradients})")
    elif gradients > 0:
        details.append(f"ℹ️ Gradienten ({gradients})")
    
    shadows = full_css_lower.count('box-shadow')
    if shadows > 10:
        score -= 5
        details.append(f"⚠️ Schatten ({shadows})")
    
    radius = full_css_lower.count('border-radius')
    if radius > 15:
        score += 3
        details.append("✅ Abgerundet")
    elif radius == 0:
        score -= 5
        details.append("❌ Kantig")
    
    # Frameworks
    if 'bootstrap/3' in html or 'bootstrap-3' in html:
        score -= 10
        details.append("❌ Bootstrap 3")
    elif 'bootstrap/4' in html or 'bootstrap-5' in html or 'bootstrap@4' in html or 'bootstrap@5' in html:
        score += 2
        details.append("✅ Bootstrap 4/5")
    
    if 'jquery-1.' in html or 'jquery/1.' in html:
        score -= 8
        details.append("❌ jQuery 1.x")
    
    # Animationen
    has_anim = 'transition' in full_css_lower or 'animation' in full_css_lower or '@keyframes' in full_css_lower
    if has_anim:
        score += 3
        details.append("✅ Animationen")
    
    # Marquee/Blink
    if '<marquee' in html.lower():
        score -= 40
        details.append("❌❌ MARQUEE!")
    if '<blink' in html.lower():
        score -= 40
        details.append("❌❌ BLINK!")
    
    # Score begrenzen
    score = max(0, min(100, score))
    
    if score < 20:
        era = "🔴 SEHR ALT (2000-2010)"
    elif score < 40:
        era = "🟠 ALT (2010-2015)"
    elif score < 65:
        era = "🟡 MITTEL (2015-2019)"
    else:
        era = "🟢 MODERN (2020+)"
    
    return score, details, era


def analyze_website(url):
    result = {
        'url': url,
        'reachable': False,
        'domain_age': None,
        'visual_score': 0,
        'visual_era': '',
        'visual_details': [],
        'problems': [],
        'opportunities': [],
        'mail': None,
        'telefon': None,
        'has_ssl': False,
        'load_time': 0,
        'category': 'unbekannt',
        'total_score': 0
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
    
    # Visuelle Analyse
    visual_score, visual_details, era = analyze_visual_age(soup, html, url)
    result['visual_score'] = visual_score
    result['visual_era'] = era
    result['visual_details'] = visual_details
    
    # Verkaufsargumente
    has_imp = any(p in html for p in ['/impressum', 'impressum', 'imprint']) or soup.find('a', href=re.compile(r'impressum|imprint', re.I))
    if not has_imp:
        result['opportunities'].append("Kein Impressum sichtbar")
    
    cta_w = ['termin', 'anrufen', 'jetzt buchen', 'reservieren', 'kontakt']
    btns = soup.find_all(['a', 'button'])
    has_cta = any(any(w in b.get_text(strip=True).lower() for w in cta_w) for b in btns if b.get_text(strip=True))
    if not has_cta:
        result['opportunities'].append("Kein Call-to-Action")
    
    if not any(w in html.lower() for w in ['bewertung', 'review', 'sterne', 'google maps']):
        result['opportunities'].append("Keine Bewertungen sichtbar")
    
    if not any(w in html.lower() for w in ['öffnungszeit', 'mo-fr', 'montag']):
        result['opportunities'].append("Keine Öffnungszeiten")
    
    if result['load_time'] > 4:
        result['opportunities'].append(f"Langsame Ladezeit ({result['load_time']}s)")
    
    if not soup.find('meta', attrs={'name': 'viewport'}):
        result['opportunities'].append("NICHT mobil optimiert")
    
    # Mail & Telefon
    mt = soup.find('a', href=re.compile(r'mailto:', re.I))
    if mt:
        result['mail'] = mt['href'].replace('mailto:', '').strip().split('?')[0].lower()
    if not result['mail']:
        m = re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html)
        if m:
            c = m.group().lower()
            if not any(j in c for j in ['.png', '.jpg', '.css', '.js', 'example', 'domain', 'wixpress']):
                result['mail'] = c
    
    phones = []
    for p in [r'\+49[\s\d\-/]{6,20}', r'0\d{2,4}[\s\-/]\d{3,10}', r'tel:\+?[\d\s\-]+']:
        phones.extend(re.findall(p, html))
    if phones:
        result['telefon'] = phones[0][:30]
    
    # Kategorie
    result['total_score'] = visual_score
    
    if visual_score < 20:
        result['category'] = '🔴 SEHR ALT (2000-2010)'
    elif visual_score < 40:
        result['category'] = '🟠 ALT (2010-2015)'
    elif visual_score < 65:
        result['category'] = '🟡 MITTEL (2015-2019)'
    else:
        result['category'] = '🟢 MODERN (2020+)'
    
    return result


def generate_html_report(results, filepath):
    order = {
        '🔴 SEHR ALT (2000-2010)': 0,
        '🟠 ALT (2010-2015)': 1,
        '🟡 MITTEL (2015-2019)': 2,
        '🟢 MODERN (2020+)': 3,
        '❌ Fehler': 99
    }
    results.sort(key=lambda x: (order.get(x['category'], 5), -x['total_score']))
    
    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BenderSites Scanner</title>
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
    scan_progress = {
        'running': True,
        'total': 0,
        'current': 0,
        'url': '',
        'results': [],
        'report_file': None
    }
    
    urls = []
    with open(filepath, 'r', encoding='utf-8') as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = ';' if sample.count(';') > sample.count(',') else ','
        f.seek(0)
        for row in csv.DictReader(f, delimiter=delimiter):
            site = row.get('Website', row.get('website', row.get('URL', row.get('url', '')))).strip()
            if site and site.startswith('http') and not any(d in site for d in SKIP_DOMAINS):
                urls.append(site)
    
    scan_progress['total'] = len(urls)
    
    for i, url in enumerate(urls):
        scan_progress['current'] = i + 1
        scan_progress['url'] = url[:60]
        try:
            scan_progress['results'].append(analyze_website(url))
        except Exception as e:
            scan_progress['results'].append({
                'url': url,
                'category': '❌ Fehler',
                'total_score': 0,
                'visual_score': 0,
                'visual_era': 'Fehler',
                'visual_details': [],
                'opportunities': [str(e)],
                'mail': None,
                'telefon': None,
                'domain_age': None,
                'load_time': 0
            })
        time.sleep(0.3)
    
    rp = os.path.join(app.config['REPORTS_FOLDER'], f'report_{int(time.time())}.html')
    generate_html_report(scan_progress['results'], rp)
    scan_progress['report_file'] = rp
    scan_progress['running'] = False


@app.route('/scan-single', methods=['POST'])
def scan_single():
    data = request.get_json()
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'Keine URL'}), 400
    result = analyze_website(url)
    return jsonify(result)


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
        w.writerow(['URL', 'Kategorie', 'VisuellerScore', 'VisuelleAera', 'Mail', 'Telefon', 'Domainalter', 'Ladezeit', 'Verkaufsargumente'])
        for r in scan_progress['results']:
            w.writerow([
                r['url'],
                r['category'],
                r['visual_score'],
                r['visual_era'],
                r['mail'] or '',
                r['telefon'] or '',
                r['domain_age'] or '',
                r['load_time'],
                ' | '.join(r['opportunities'])
            ])
    return send_file(cp, as_attachment=True, download_name='bender-sites-scan.csv')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
