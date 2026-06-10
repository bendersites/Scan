def analyze_visual_age(soup, html, url):
    """
    Analysiert das VISUELLE Alter.
    Lädt externe CSS-Dateien korrekt und gewichtet Bilder stark.
    """
    from urllib.parse import urljoin
    
    score = 50
    details = []
    
    # ─── CSS LADEN (inline + extern) ──────────────────────────────
    full_css = ""
    
    # Inline styles
    for s in soup.find_all('style'):
        full_css += s.get_text() + "\n"
    
    # Externe CSS-Dateien laden
    base_url = url if url.startswith('http') else 'https://' + url
    for link in soup.find_all('link', rel='stylesheet'):
        href = link.get('href', '')
        if not href:
            continue
        # URL auflösen (relativ → absolut)
        css_url = urljoin(base_url, href)
        try:
            cr = requests.get(css_url, headers=HEADERS, timeout=8)
            if cr.status_code == 200:
                full_css += cr.text[:8000] + "\n"  # Max 8KB pro CSS
        except Exception as e:
            pass  # CORS oder Timeout → ignorieren
    
    full_css_lower = full_css.lower()
    
    # ─── 1. RESPONSIVE (Viewport) ─────────────────────────────────
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
    
    # ─── 2. BILDER (sehr wichtig!) ─────────────────────────────────
    images = soup.find_all('img')
    total_imgs = len(images)
    
    if total_imgs == 0:
        score -= 15
        details.append("❌ Keine Bilder = Text-Only")
    else:
        big_images = 0
        small_images = 0
        data_uris = 0
        
        for img in images:
            src = img.get('src', '')
            if src.startswith('data:image'):
                data_uris += 1
                continue
            
            w = img.get('width', '')
            h = img.get('height', '')
            style = img.get('style', '').lower()
            cls = ' '.join(img.get('class', [])).lower()
            
            # Prüfe auf große Bilder
            is_big = (
                w in ['100%', 'auto', '100vw'] or
                'width:100%' in style or 'width: 100%' in style or
                any(x in cls for x in ['hero', 'banner', 'fullscreen', 'cover', 'bg', 'header-img'])
            )
            
            is_small = (
                (w and str(w).replace('px','').isdigit() and int(str(w).replace('px','')) < 200) or
                (h and str(h).replace('px','').isdigit() and int(str(h).replace('px','')) < 150)
            )
            
            if is_big:
                big_images += 1
            elif is_small:
                small_images += 1
        
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
        
        # SVG
        svgs = len(soup.find_all('svg'))
        if svgs > 0:
            score += 8
            details.append(f"✅ SVG ({svgs})")
        
        # Lazy loading
        lazy = sum(1 for img in images if img.get('loading') == 'lazy')
        if lazy > 0:
            score += 5
            details.append("✅ Lazy Loading")
        
        # Picture-Tag
        pictures = len(soup.find_all('picture'))
        if pictures > 0:
            score += 5
            details.append(f"✅ Picture-Tag ({pictures})")
    
    # ─── 3. HTML STRUKTUR ─────────────────────────────────────────
    semantic = ['header', 'nav', 'main', 'section', 'article', 'footer', 'aside']
    sem_count = sum(1 for tag in semantic if soup.find(tag))
    if sem_count >= 4:
        score += 10
        details.append(f"✅ Semantisch ({sem_count})")
    elif sem_count >= 2:
        score += 3
        details.append(f"⚠️ Semantisch ({sem_count})")
    else:
        score -= 10
        details.append("❌ Kein semantisches HTML")
    
    divs = len(soup.find_all('div'))
    if divs > 100:
        score -= 5
        details.append(f"⚠️ Div-Suppe ({divs})")
    
    tables = len(soup.find_all('table'))
    if tables > 2:
        score -= 20
        details.append(f"❌ Tables ({tables})")
    elif tables > 0:
        score -= 5
        details.append(f"⚠️ Tables ({tables})")
    
    # ─── 4. CSS LAYOUT (jetzt mit geladenem CSS!) ───────────────────
    has_flex = 'display:flex' in full_css_lower or 'display: flex' in full_css_lower
    has_grid = 'display:grid' in full_css_lower or 'display: grid' in full_css_lower
    
    if has_flex:
        score += 8
        details.append("✅ Flexbox")
    if has_grid:
        score += 12
        details.append("✅ CSS Grid")
    
    if not has_flex and not has_grid:
        # Prüfe auf Float oder Position-Absolute als Fallback
        floats = full_css_lower.count('float:')
        abs_pos = full_css_lower.count('position:absolute') + full_css_lower.count('position: absolute')
        if floats > 3:
            score -= 15
            details.append(f"❌ Float-Layout ({floats}x)")
        elif abs_pos > 5:
            score -= 10
            details.append(f"❌ Viel absolute Positionierung")
        else:
            # Weder Flex noch Grid noch Float → vermutlich sehr alt oder sehr einfach
            score -= 5
            details.append("⚠️ Kein modernes Layout erkannt")
    
    # Fixe Breiten
    fixed_px = len(re.findall(r'width:\s*\d{3,4}px', full_css_lower))
    if fixed_px > 5:
        score -= 15
        details.append(f"❌ Fixe Breiten ({fixed_px})")
    
    # Max-width
    if 'max-width' in full_css_lower:
        score += 3
        details.append("✅ Max-width")
    
    # ─── 5. SCHRIFTEN ─────────────────────────────────────────────
    has_webfonts = 'fonts.googleapis' in html or '@font-face' in full_css_lower
    if has_webfonts:
        score += 5
        details.append("✅ Webfonts")
    else:
        # Prüfe auf System-Fonts im CSS
        if 'font-family:arial' in full_css_lower or 'font-family: times' in full_css_lower:
            score -= 5
            details.append("⚠️ System-Fonts")
    
    # ─── 6. DESIGN ──────────────────────────────────────────────────
    gradients = len(re.findall(r'linear-gradient|radial-gradient', full_css_lower))
    if gradients > 5:
        score -= 10
        details.append(f"❌ Gradienten ({gradients}) = Web 2.0")
    elif gradients > 2:
        score -= 3
        details.append(f"⚠️ Gradienten ({gradients})")
    
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
        details.append("❌ Kantig (kein radius)")
    
    # ─── 7. FRAMEWORKS ────────────────────────────────────────────
    if 'bootstrap/3' in html or 'bootstrap-3' in html:
        score -= 10
        details.append("❌ Bootstrap 3")
    elif 'bootstrap/4' in html or 'bootstrap-5' in html or 'bootstrap@4' in html or 'bootstrap@5' in html:
        score += 2
        details.append("✅ Bootstrap 4/5")
    
    if 'jquery-1.' in html or 'jquery/1.' in html:
        score -= 8
        details.append("❌ jQuery 1.x")
    
    # ─── 8. ANIMATIONEN ───────────────────────────────────────────
    has_anim = 'transition' in full_css_lower or 'animation' in full_css_lower or '@keyframes' in full_css_lower
    if has_anim:
        score += 3
        details.append("✅ Animationen")
    
    # ─── 9. MARQUEE/BLINK ─────────────────────────────────────────
    if '<marquee' in html.lower():
        score -= 40
        details.append("❌❌ MARQUEE!")
    if '<blink' in html.lower():
        score -= 40
        details.append("❌❌ BLINK!")
    
    # ─── SCORE BEGRENZEN ──────────────────────────────────────────
    score = max(0, min(100, score))
    
    # Ära bestimmen
    if score < 20:
        era = "🔴 SEHR ALT (2000-2010)"
    elif score < 40:
        era = "🟠 ALT (2010-2015)"
    elif score < 65:
        era = "🟡 MITTEL (2015-2019)"
    else:
        era = "🟢 MODERN (2020+)"
    
    return score, details, era
