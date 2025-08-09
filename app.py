from fastapi import FastAPI, HTTPException
import os, time, re
from playwright.sync_api import sync_playwright

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up"}

# ===== CONFIGURA AQUÍ LAS CASAS QUE VAS A USAR =====
CASAS = {
    "codere": {
        # Ir a la home y abrir el modal de login desde ahí
        "login_url": "https://www.codere.es/",
        # Selectores candidatos para el saldo tras login (ajustaremos si hace falta)
        "selector_saldo": '[data-testid="balance"], .balance, .saldo, [class*="balance"]',
        # Variables de entorno en Render
        "user_env": "CODERE_USER",
        "pass_env": "CODERE_PASS",
    },
    # Puedes añadir más casas siguiendo el mismo esquema
}

def leer_saldo_playwright(casa_key: str):
    cfg = CASAS.get(casa_key)
    if not cfg:
        raise RuntimeError(f"Casa no soportada: {casa_key}")

    user = os.getenv(cfg["user_env"])
    pwd  = os.getenv(cfg["pass_env"])
    if not user or not pwd:
        raise RuntimeError(f"Faltan credenciales en variables de entorno para {casa_key}")

    with sync_playwright() as p:
        # En Render: headless=True. En local para depurar podrías usar False.
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            locale="es-ES"
        )
        page = context.new_page()

        # --- 1) Ir a la home
        page.goto(cfg["login_url"], wait_until="networkidle")

        # (Opcional) intentar cerrar banner de cookies si aparece (no rompe si no existe)
        try:
            page.get_by_role("button", name=re.compile("Aceptar|Aceptar todas|Consentir", re.I)).click(timeout=2000)
        except:
            pass

        # --- 1.1) Abrir el modal de "Iniciar sesión" / "Acceder"
        opened = False
        open_attempts = [
            lambda: page.get_by_text("Iniciar sesión", exact=False).first.click(timeout=3000),
            lambda: page.get_by_text("Acceder", exact=False).first.click(timeout=3000),
            lambda: page.get_by_role("button", name=re.compile("Iniciar sesión|Acceder", re.I)).click(timeout=3000),
            lambda: page.locator('a:has-text("Iniciar sesión")').first.click(timeout=3000),
            lambda: page.locator('a:has-text("Acceder")').first.click(timeout=3000),
            # A veces el icono de usuario abre el modal:
            lambda: page.get_by_role("button", name=re.compile("Mi cuenta|Entrar|Usuario", re.I)).click(timeout=3000),
        ]
        for fn in open_attempts:
            try:
                fn()
                opened = True
                break
            except:
                continue

        if not opened:
            raise RuntimeError("No pude abrir el modal de login (no encontré el botón Acceder/Iniciar sesión)")

        # --- 1.2) Esperar a que aparezcan los campos de formulario
        user_sel = 'input[name="username"], input[type="email"], input[autocomplete="username"]'
        pass_sel = 'input[name="password"], input[type="password"], input[autocomplete="current-password"]'
        page.wait_for_selector(user_sel, timeout=12000)
        page.wait_for_selector(pass_sel, timeout=12000)

        # --- 2) Rellenar usuario y contraseña
        page.fill(user_sel, user)
        page.fill(pass_sel, pwd)

        # --- 3) Clic en entrar/enviar dentro del modal
        clicked = False
        click_candidates = [
            lambda: page.click('button[type="submit"]', timeout=3000),
            lambda: page.get_by_role("button", name=re.compile("Entrar|Iniciar sesión|Acceder", re.I)).click(timeout=3000),
            lambda: page.locator('button:has-text("Entrar")').click(timeout=3000),
            lambda: page.locator('button:has-text("Iniciar sesión")').click(timeout=3000),
            lambda: page.locator('button:has-text("Acceder")').click(timeout=3000),
        ]
        for fn in click_candidates:
            try:
                fn()
                clicked = True
                break
            except:
                continue
        if not clicked:
            raise RuntimeError("No encontré el botón para enviar el login")

        # --- 4) Esperar a que termine la carga tras el login
        page.wait_for_load_state("networkidle")

        # --- 5) Intentar localizar el saldo
        saldo_text = None

        # Intento 1: selector directo
        try:
            page.wait_for_selector(cfg["selector_saldo"], timeout=12000)
            saldo_text = page.inner_text(cfg["selector_saldo"]).strip()
        except:
            pass

        # Intento 2: buscar por texto "Saldo"/"Balance" y extraer número cercano
        if not saldo_text:
            try:
                # Buscar un texto que contenga "Saldo" o "Balance"
                label_locator = None
                for palabra in ["Saldo", "Balance", "Mi saldo"]:
                    try:
                        label_locator = page.get_by_text(palabra, exact=False).first
                        # Si existe, intentamos leer el contenedor padre
                        container_text = ""
                        try:
                            container_text = label_locator.locator("xpath=..").inner_text().strip()
                        except:
                            pass
                        if not container_text:
                            try:
                                container_text = label_locator.inner_text().strip()
                            except:
                                pass

                        m = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*€|\d+,\d{2}\s*€", container_text)
                        if m:
                            saldo_text = m.group(0)
                            break
                    except:
                        continue
            except:
                pass

        if not saldo_text:
            # Aquí podríamos añadir navegación a "Mi cuenta" / "Cartera" si hiciera falta.
            raise RuntimeError("No pude encontrar el saldo tras el login")

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
