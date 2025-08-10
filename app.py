from fastapi import FastAPI, HTTPException
import os, time, re
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up"}

CASAS = {
    "codere": {
        # vamos directo al login móvil
        "login_url": "https://m.apuestas.codere.es/deportesEs/#/SignIn",
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

def _visible_inputs_info(ctx):
    # Devuelve lista [(type, placeholder, name, id, aria), ...] de inputs visibles (útil para depurar)
    infos = []
    try:
        inputs = ctx.locator("input")
        n = inputs.count()
        for i in range(min(n, 30)):
            el = inputs.nth(i)
            try:
                if el.is_visible(timeout=200):
                    t  = el.get_attribute("type") or ""
                    ph = el.get_attribute("placeholder") or ""
                    nm = el.get_attribute("name") or ""
                    _id = el.get_attribute("id") or ""
                    aria = el.get_attribute("aria-label") or ""
                    infos.append((t.strip(), ph.strip(), nm.strip(), _id.strip(), aria.strip()))
            except:
                continue
    except:
        pass
    return infos

def _first_visible(ctx, selectors, each=1200):
    for sel in selectors:
        try:
            loc = ctx.locator(sel).first
            loc.wait_for(state="visible", timeout=each)
            return sel
        except:
            continue
    return None

def _pick_any_text_and_password(ctx):
    # último recurso: cualquier input text visible y cualquier password visible
    try:
        txt = ctx.locator('input[type="text"], input:not([type]), input[type="email"]').filter(has_text="").first
        txt.wait_for(state="visible", timeout=1200)
        pwd = ctx.locator('input[type="password"]').first
        pwd.wait_for(state="visible", timeout=1200)
        return txt, pwd
    except:
        return None, None

def _find_user_pass_ctx(page, user, pwd):
    # 1) candidatos amplios por placeholder/label/aria/name/id
    user_cands = [
        lambda c: c.get_by_placeholder(re.compile("correo|email|e-mail|usuario|dni|nie", re.I)),
        lambda c: c.get_by_label(re.compile("correo|email|usuario|dni|nie", re.I)),
        lambda c: c.locator('input[aria-label*="usuario" i], input[aria-label*="email" i]'),
        lambda c: c.locator('input[name*="user" i], input[id*="user" i]'),
        lambda c: c.locator('input[type="email"]'),
    ]
    pass_cands = [
        lambda c: c.get_by_placeholder(re.compile("contraseña|clave|password", re.I)),
        lambda c: c.get_by_label(re.compile("contraseña|clave|password", re.I)),
        lambda c: c.locator('input[aria-label*="contraseña" i]'),
        lambda c: c.locator('input[name*="pass" i], input[id*="pass" i]'),
        lambda c: c.locator('input[type="password"]'),
    ]

    # 2) probar página, luego frames
    contexts = [page] + list(page.frames)
    debug_seen = []
    for ctx in contexts:
        # info de inputs visibles (para error útil)
        debug_seen.extend(_visible_inputs_info(ctx))

        # intenta user
        u_sel = None
        for fn in user_cands:
            try:
                loc = fn(ctx).first
                loc.wait_for(state="visible", timeout=800)
                u_sel = loc
                break
            except:
                continue
        # intenta pass
        p_sel = None
        for fn in pass_cands:
            try:
                loc = fn(ctx).first
                loc.wait_for(state="visible", timeout=800)
                p_sel = loc
                break
            except:
                continue

        if not u_sel or not p_sel:
            # último recurso en este contexto
            txt, pwd_el = _pick_any_text_and_password(ctx)
            if not u_sel and txt: u_sel = txt
            if not p_sel and pwd_el: p_sel = pwd_el

        if u_sel and p_sel:
            # rellenar
            u_sel.fill(user, timeout=1500)
            p_sel.fill(pwd, timeout=1500)
            return ctx, u_sel, p_sel, debug_seen

    # si no lo encontramos en ningún sitio
    raise RuntimeError(f"No encontré campos de usuario/contraseña en página ni iframes. Inputs visibles: {debug_seen[:15]}")

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
        # móvil
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

        # 1) Ir a SignIn móvil directo (evita depender de botones)
        page.goto(cfg["login_url"], wait_until="domcontentloaded")
        _click_cookies(page)
        page.wait_for_load_state("networkidle")

        # 2) Encontrar y rellenar user/pass
        ctx, _, _, debug_seen = _find_user_pass_ctx(page, user, pwd)

        # 3) Click en botón login en el mismo contexto (y si no, en otros)
        btn_sels = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Entrar")',
            'button:has-text("Iniciar sesión")',
            'button:has-text("Acceder")',
            '[role="button"]:has-text("Entrar")',
        ]
        clicked = False
        for sel in btn_sels:
            try:
                ctx.click(sel, timeout=1500); clicked = True; break
            except: continue
        if not clicked:
            try:
                page.click('button[type="submit"]', timeout=1500); clicked = True
            except:
                for fr in page.frames:
                    for sel in btn_sels:
                        try:
                            fr.click(sel, timeout=1200); clicked = True; break
                        except: continue
                    if clicked: break
        if not clicked:
            raise RuntimeError(f"No encontré el botón de login (móvil). Inputs visibles vistos: {debug_seen[:15]}")

        # 4) Espera tras login
        page.wait_for_load_state("networkidle")
        time.sleep(1.0)  # pequeña espera extra por animaciones SPA

        # 5) Localizar saldo
        saldo_text = None
        try:
            page.wait_for_selector(cfg["selector_saldo"], timeout=15000)
            saldo_text = page.inner_text(cfg["selector_saldo"]).strip()
        except:
            for fr in page.frames:
                try:
                    fr.wait_for_selector(cfg["selector_saldo"], timeout=1200)
                    saldo_text = fr.inner_text(cfg["selector_saldo"]).strip()
                    break
                except: continue

        if not saldo_text:
            palabras = ["Saldo", "Balance", "Mi saldo", "Disponible"]
            def extract_in(ctx2):
                try:
                    for palabra in palabras:
                        loc = ctx2.get_by_text(palabra, exact=False).first
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
            raise RuntimeError(f"No pude encontrar el saldo tras el login (móvil). Frames vistos: {frame_info}. Inputs vistos: {debug_seen[:15]}")

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
