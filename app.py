from fastapi import FastAPI, HTTPException
import os, time, re
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up"}

CASAS = {
    "codere": {
        "login_url": "https://m.apuestas.codere.es/deportesEs/#/HomePage",
        "selector_saldo": '[data-testid="balance"], .balance, .saldo, [class*="balance"]',
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
            fn()
            break
        except:
            continue

def _open_login_modal(page):
    attempts = [
        lambda: page.get_by_text("Iniciar sesión", exact=False).first.click(timeout=2500),
        lambda: page.get_by_text("Acceder", exact=False).first.click(timeout=2500),
        lambda: page.get_by_role("button", name=re.compile("Iniciar sesión|Acceder|Entrar", re.I)).click(timeout=2500),
        lambda: page.locator('a:has-text("Iniciar sesión")').first.click(timeout=2500),
        lambda: page.locator('a:has-text("Acceder")').first.click(timeout=2500),
        lambda: page.get_by_role("button", name=re.compile("Mi cuenta|Usuario|Perfil", re.I)).click(timeout=2500),
    ]
    for fn in attempts:
        try:
            fn()
            return True
        except:
            continue
    return False

def _find_ctx_for_selector(page, selector, timeout=30000):
    # busca selector en la página; si no, recorre iframes
    try:
        page.wait_for_selector(selector, timeout=timeout, state="visible")
        return page
    except PWTimeout:
        pass
    for fr in page.frames:
        try:
            fr.wait_for_selector(selector, timeout=1500, state="visible")
            return fr
        except:
            continue
    raise PWTimeout(f"selector no encontrado: {selector}")

def _fill_user_pass(page, user, pwd):
    # Candidatos por placeholder/label/name/type
    user_candidates = [
        lambda ctx: ctx.get_by_placeholder(re.compile("correo|email|e-mail|usuario|dni|nie", re.I)).fill(user, timeout=1200),
        lambda ctx: ctx.get_by_label(re.compile("correo|email|usuario|dni|nie", re.I)).fill(user, timeout=1200),
        lambda ctx: ctx.fill('input[name="username"]', user, timeout=1200),
        lambda ctx: ctx.fill('input[type="email"]', user, timeout=1200),
        lambda ctx: ctx.fill('input[autocomplete="username"]', user, timeout=1200),
        lambda ctx: ctx.fill('input[id*="user"]', user, timeout=1200),
    ]
    pass_candidates = [
        lambda ctx: ctx.get_by_placeholder(re.compile("contraseña|clave|password", re.I)).fill(pwd, timeout=1200),
        lambda ctx: ctx.get_by_label(re.compile("contraseña|clave|password", re.I)).fill(pwd, timeout=1200),
        lambda ctx: ctx.fill('input[name="password"]', pwd, timeout=1200),
        lambda ctx: ctx.fill('input[type="password"]', pwd, timeout=1200),
        lambda ctx: ctx.fill('input[autocomplete="current-password"]', pwd, timeout=1200),
        lambda ctx: ctx.fill('input[id*="pass"]', pwd, timeout=1200),
    ]

    # Intentar en la página principal
    for fn in user_candidates:
        try:
            fn(page); user_ctx = page; break
        except:
            user_ctx = None
            continue
    # Si no, buscar frame por frame
    if not user_ctx:
        for fr in page.frames:
            for fn in user_candidates:
                try:
                    fn(fr); user_ctx = fr; break
                except:
                    continue
            if user_ctx: break
    if not user_ctx:
        raise PWTimeout("No encontré el campo de usuario en página ni iframes")

    for fn in pass_candidates:
        try:
            fn(user_ctx); pass_ctx = user_ctx; break
        except:
            pass_ctx = None
            continue
    if not pass_ctx:
        # quizá pass está en otro frame
        for fr in page.frames:
            for fn in pass_candidates:
                try:
                    fn(fr); pass_ctx = fr; break
                except:
                    continue
            if pass_ctx: break
    if not pass_ctx:
        raise PWTimeout("No encontré el campo de contraseña en página ni iframes")

    return pass_ctx  # devolvemos el contexto más probable para el botón

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
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            locale="es-ES",
        )
        page = context.new_page()

        # 1) Home
        page.goto(cfg["login_url"], wait_until="networkidle")
        _click_cookies(page)

        # 2) Abrir login
        if not _open_login_modal(page):
            raise RuntimeError("No pude abrir el modal de login (Acceder/Iniciar sesión)")

        # 3) Rellenar user/pass buscando en página e iframes
        pass_ctx = _fill_user_pass(page, user, pwd)

        # 4) Click botón de login (probar en el mismo contexto, luego otros)
        btn_selectors = [
            'button[type="submit"]',
            'button:has-text("Entrar")',
            'button:has-text("Iniciar sesión")',
            'button:has-text("Acceder")',
            'input[type="submit"]',
        ]
        clicked = False
        for sel in btn_selectors:
            try:
                pass_ctx.click(sel, timeout=1500)
                clicked = True
                break
            except:
                continue
        if not clicked:
            try:
                page.click('button[type="submit"]', timeout=1500); clicked = True
            except:
                for fr in page.frames:
                    for sel in btn_selectors:
                        try:
                            fr.click(sel, timeout=1000); clicked = True; break
                        except:
                            continue
                    if clicked: break
        if not clicked:
            raise RuntimeError("No encontré el botón para enviar el login")

        # 5) Esperar post-login
        page.wait_for_load_state("networkidle")

        # 6) Buscar saldo (página y iframes)
        saldo_text = None
        try:
            page.wait_for_selector(cfg["selector_saldo"], timeout=12000)
            saldo_text = page.inner_text(cfg["selector_saldo"]).strip()
        except:
            for fr in page.frames:
                try:
                    fr.wait_for_selector(cfg["selector_saldo"], timeout=1200)
                    saldo_text = fr.inner_text(cfg["selector_saldo"]).strip()
                    break
                except:
                    continue

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
                            try:
                                txt = loc.inner_text().strip()
                            except:
                                pass
                        m = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*€|\d+,\d{2}\s*€", txt)
                        if m:
                            return m.group(0)
                except:
                    return None
            saldo_text = extract_in(page)
            if not saldo_text:
                for fr in page.frames:
                    saldo_text = extract_in(fr)
                    if saldo_text:
                        break

        if not saldo_text:
            # info de depuración útil
            frame_info = [ (fr.url, fr.name) for fr in page.frames ]
            raise RuntimeError(f"No pude encontrar el saldo tras el login. Frames vistos: {frame_info}")

        browser.close()

    try:
        saldo_num = float(saldo_text.replace("€", "").replace(".", "").replace(",", "
