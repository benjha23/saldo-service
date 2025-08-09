from fastapi import FastAPI, HTTPException
import os, time, re
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up"}

CASAS = {
    "codere": {
        "login_url": "https://www.codere.es/",
        "selector_saldo": '[data-testid="balance"], .balance, .saldo, [class*="balance"]',
        "user_env": "CODERE_USER",
        "pass_env": "CODERE_PASS",
    },
}

def _click_cookies(page):
    # intenta cerrar consentimientos comunes
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
    # intenta abrir el modal/menu de login con varios triggers
    open_attempts = [
        lambda: page.get_by_text("Iniciar sesión", exact=False).first.click(timeout=2000),
        lambda: page.get_by_text("Acceder", exact=False).first.click(timeout=2000),
        lambda: page.get_by_role("button", name=re.compile("Iniciar sesión|Acceder|Entrar", re.I)).click(timeout=2000),
        lambda: page.locator('a:has-text("Iniciar sesión")').first.click(timeout=2000),
        lambda: page.locator('a:has-text("Acceder")').first.click(timeout=2000),
        lambda: page.get_by_role("button", name=re.compile("Mi cuenta|Usuario|Perfil", re.I)).click(timeout=2000),
    ]
    for fn in open_attempts:
        try:
            fn()
            return True
        except:
            continue
    return False

def _find_in_page_or_frames(page, selector, timeout=12000):
    # busca un selector en la página principal; si no, recorre iframes
    try:
        page.wait_for_selector(selector, timeout=timeout)
        return page  # selector está en la página principal
    except PWTimeout:
        pass
    # buscar en iframes
    for fr in page.frames:
        try:
            fr.wait_for_selector(selector, timeout=1500)
            return fr
        except:
            continue
    raise PWTimeout(f"No se encontró selector en página ni iframes: {selector}")

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

        # 1) Ir a home
        page.goto(cfg["login_url"], wait_until="networkidle")
        _click_cookies(page)

        # 2) Abrir modal/login
        if not _open_login_modal(page):
            raise RuntimeError("No pude abrir el modal de login (Acceder/Iniciar sesión)")

        # 3) Localizar campos en página o en iframe
        user_sel = 'input[name="username"], input[type="email"], input[autocomplete="username"], input[id*="user"]'
        pass_sel = 'input[name="password"], input[type="password"], input[autocomplete="current-password"], input[id*="pass"]'

        ctx_user = _find_in_page_or_frames(page, user_sel, timeout=15000)
        ctx_pass = _find_in_page_or_frames(page, pass_sel, timeout=15000)

        # Nota: si user/pass están en el mismo frame, ctx_user == ctx_pass
        ctx_user.fill(user_sel, user)
        ctx_pass.fill(pass_sel, pwd)

        # 4) Click en botón de login (también considerar iframes)
        btn_candidates = [
            'button[type="submit"]',
            'button:has-text("Entrar")',
            'button:has-text("Iniciar sesión")',
            'button:has-text("Acceder")',
        ]
        clicked = False
        # primero intenta en el frame de password
        for sel in btn_candidates:
            try:
                ctx_pass.click(sel, timeout=1500)
                clicked = True
                break
            except:
                continue
        # si no, intenta en toda la página y en otros frames
        if not clicked:
            try:
                page.click('button[type="submit"]', timeout=1500)
                clicked = True
            except:
                for fr in page.frames:
                    for sel in btn_candidates:
                        try:
                            fr.click(sel, timeout=1000)
                            clicked = True
                            break
                        except:
                            continue
                    if clicked:
                        break
        if not clicked:
            raise RuntimeError("No encontré el botón para enviar el login")

        # 5) Esperar post-login
        page.wait_for_load_state("networkidle")

        # 6) Intentar localizar el saldo
        saldo_text = None

        # Intento 1: selector directo
        try:
            page.wait_for_selector(cfg["selector_saldo"], timeout=12000)
            saldo_text = page.inner_text(cfg["selector_saldo"]).strip()
        except:
            # probar en frames
            for fr in page.frames:
                try:
                    fr.wait_for_selector(cfg["selector_saldo"], ti_]()
