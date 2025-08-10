from fastapi import FastAPI, HTTPException
import os, time, re
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up"}

CASAS = {
    "codere": {
        # URL móvil que me pasaste
        "login_url": "https://m.apuestas.codere.es/deportesEs/#/HomePage?openlogin=true",
        "selector_saldo": '[data-testid="balance"], .balance, .saldo, [class*="balance"], [class*="wallet"], [class*="account"]',
        "user_env": "CODERE_USER",
        "pass_env": "CODERE_PASS",
    },
}

def _click_cookies(page):
    candidates = [
        lambda: page.get_by_role("button", name=re.compile("Aceptar", re.I)).click(timeout=1500),
        lambda: page.get_by_role("button", name=re.compile("Aceptar todas", re.I)).click(timeout=1500),
        lambda: page.locator("button:has-text('Aceptar')").first.click(timeout=1500),
        lambda: page.locator("button:has-text('Acepto')").first.click(timeout=1500),
        lambda: page.locator("[id*='onetrust'] button:has-text('Aceptar')").first.click(timeout=1500),
    ]
    for fn in candidates:
        try:
            fn(); break
        except: continue

def _open_login_mobile(page):
    # En móvil suele estar en un menú (≡), icono usuario o texto "Entrar / Iniciar sesión / Acceder"
    attempts = [
        lambda: page.get_by_role("button", name=re.compile("menu|menú|hamburguesa|abrir menú", re.I)).click(timeout=1500),
        lambda: page.get_by_role("button", name=re.compile("usuario|mi cuenta|perfil|entrar", re.I)).click(timeout=1500),
        lambda: page.get_by_text(re.compile("Iniciar sesión|Acceder|Entrar", re.I), exact=False).first.click(timeout=2000),
        lambda: page.locator('a:has-text("Iniciar sesión"), a:has-text("Acceder"), a:has-text("Entrar")').first.click(timeout=2000),
        lambda: page.locator('button:has-text("Iniciar sesión"), button:has-text("Acceder"), button:has-text("Entrar")').first.click(timeout=2000),
    ]
    for fn in attempts:
        try:
            fn(); return True
        except: continue
    return False

def _visible_first(ctx, selectors, timeout_each=1200):
    # Devuelve (ctx, selector) del primer selector visible en este contexto
    for sel in selectors:
        try:
            loc = ctx.locator(sel)
            count = loc.count()
            if count == 0:
                continue
            # espera a que el primero sea visible
            loc.first.wait_for(state="visible", timeout=timeout_each)
            return sel
        except:
            continue
    return None

def _find_and_fill_user_pass(page, user, pwd):
    # candidatos MUY amplios para móvil
    user_sels = [
        'input[type="email"]',
        'input[type="text"]',
        'input[autocomplete*="user" i]',
        'input[name*="user" i]',
        'input[id*="user" i]',
        'input[placeholder*="usuario" i]',
        'input[placeholder*="email" i]',
        'input[placeholder*="correo" i]',
        'input[aria-label*="usuario" i]',
        'input[aria-label*="email" i]',
    ]
    pass_sels = [
        'input[type="password"]',
        'input[autocomplete*="password" i]',
        'input[name*="pass" i]',
        'input[id*="pass" i]',
        'input[placeholder*="contraseña" i]',
        'input[aria-label*="contraseña" i]',
    ]

    # Busca primero en página
    u_sel = _visible_first(page, user_sels, 1200)
    p_sel = _visible_first(page, pass_sels, 1200)

    # Si no, recorre iframes
    if not u_sel or not p_sel:
        for fr in page.frames:
            if not u_sel:
                u_sel = _visible_first(fr, user_sels, 800)
                user_ctx = fr if u_sel else None
            if not p_sel:
                p_sel = _visible_first(fr, pass_sels, 800)
                pass_ctx = fr if p_sel else None
            if u_sel and p_sel: break

    # Decide contextos finales
    user_ctx = page if u_sel and _visible_first(page, [u_sel], 200) else None
    pass_ctx = page if p_sel and _visible_first(page, [p_sel], 200) else None

    if not user_ctx or not pass_ctx:
        # intentar deducir desde frames
        if not user_ctx:
            for fr in page.frames:
                if _visible_first(fr, [u_sel] if u_sel else [], 200): user_ctx = fr; break
        if not pass_ctx:
            for fr in page.frames:
                if _visible_first(fr, [p_sel] if p_sel else [], 200): pass_ctx = fr; break

    if not (u_sel and user_ctx):
        raise PWTimeout("No encontré el campo de usuario en página ni iframes (móvil)")
    if not (p_sel and pass_ctx):
        raise PWTimeout("No encontré el campo de contraseña en página ni iframes (móvil)")

    user_ctx.fill(u_sel, user, timeout=2000)
    pass_ctx.fill(p_sel, pwd, timeout=2000)

    # devuelve contexto más probable para el botón
    return pass_ctx

def leer_saldo_playwright(casa_key: str):
    cfg = CASAS.get(casa_key)
    if not cfg:
        raise RuntimeError(f"Casa no soportada: {casa_key}")

    user = os.getenv(cfg["user_env"])
    pwd  = os.getenv(cfg["pass_env"])
    if not user or not pwd:
        raise RuntimeError(f"Faltan credenciales en variables de entorno para {casa_key}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Simular móvil
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Linux; Android 12; Pixel 5) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Mobile Safari/537.36"),
            viewport={"width": 414, "height": 896},
            is_mobile=True,
            device_scale_factor=2,
            has_touch=True,
            locale="es-ES",
        )
        page = context.new_page()

        # 1) Ir a la home móvil
        page.goto(cfg["login_url"], wait_until="networkidle")
        _click_cookies(page)

        # 2) Abrir login (menú/modal)
        if not _open_login_mobile(page):
            # Si el botón no aparece, intenta ir a rutas típicas de login móvil
            fallback_routes = [
                "https://m.apuestas.codere.es/deportesEs/#/SignIn",
                "https://m.apuestas.codere.es/deportesEs/#/Login",
            ]
            opened = False
            for url in fallback_routes:
                try:
                    page.goto(url, wait_until="networkidle")
                    opened = True
                    break
                except:
                    continue
            if not opened:
                raise RuntimeError("No pude abrir el login en móvil (no encontré botón ni rutas alternativas)")

        # 3) Rellenar usuario/contraseña en página o iframes
        pass_ctx = _find_and_fill_user_pass(page, user, pwd)

        # 4) Click en enviar
        btn_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Entrar")',
            'button:has-text("Iniciar sesión")',
            'button:has-text("Acceder")',
            '[role="button"]:has-text("Entrar")',
        ]
        clicked = False
        for sel in btn_selectors:
            try:
                pass_ctx.click(sel, timeout=1500); clicked = True; break
            except: continue
        if not clicked:
            # intenta en página y en otros frames
            try:
                page.click('button[type="submit"]', timeout=1500); clicked = True
            except:
                for fr in page.frames:
                    for sel in btn_selectors:
                        try:
                            fr.click(sel, timeout=1000); clicked = True; break
                        except: continue
                    if clicked: break
        if not clicked:
            raise RuntimeError("No encontré el botón para enviar el login (móvil)")

        # 5) Esperar tras login
        page.wait_for_load_state("networkidle")

        # 6) Localizar saldo (página o iframes)
        saldo_text = None
        try:
            page.wait_for_selector(cfg["selector_saldo"], timeout=12000)
            saldo_text = page.inner_text(cfg["selector_saldo"]).strip()
        except:
            # probar en frames
            for fr in page.frames:
                try:
                    fr.wait_for_selector(cfg["selector_saldo"], timeout=1200)
                    saldo_text = fr.inner_text(cfg["selector_saldo"]).strip()
                    break
                except: continue

        # Fallback por texto
        if not saldo_text:
            palabras = ["Saldo", "Balance", "Mi saldo", "Disponible"]
            def extract_in(ctx):
                try:
                    for palabra in palabras:
                        loc = ctx.get_by_text(palabra, exact=False).first
                        txt = ""
                        try:
                            txt = loc.locator("xpath=..").inner_text().strip()
                        except:
                            try: txt = loc.inner_text().strip()
                            except: pass
                        m = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*€|\d+,\d{2}\s*€", txt)
                        if m: return m.group(0)
                except: return None
            saldo_text = extract_in(page)
            if not saldo_text:
                for fr in page.frames:
                    saldo_text = extract_in(fr)
                    if saldo_text: break

        if not saldo_text:
            frame_info = [ (fr.url, fr.name) for fr in page.frames ]
            raise RuntimeError(f"No pude encontrar el saldo tras el login (móvil). Frames vistos: {frame_info}")

        browser.close()

    # Normalizar a número
    try:
        saldo_num = float(saldo_text.replace("€", "").replace(".", "").replace(",", "."))
    except:
        saldo_num = None

    return {
        "casa": casa_key,
        "saldo_raw": saldo_text,
        "saldo_num": saldo_num,
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

@app.get("/saldo/{casa}")
def saldo(casa: str):
    try:
        data = leer_saldo_playwright(casa)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
