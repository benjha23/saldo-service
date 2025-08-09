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
        # Página de login (ajustable si cambia el flujo)
        "login_url": "https://codere.es/es/login",
        # Selectores de los campos de login y botón (ajustables si cambian en la web)
        "selector_user": 'input[name="username"]',
        "selector_pass": 'input[name="password"]',
        "selector_btn":  'button[type="submit"]',
        # Selector(es) candidatos para localizar el saldo tras iniciar sesión
        "selector_saldo": '[data-testid="balance"], .balance, .saldo, [class*="balance"]',
        # Variables de entorno donde pondrás tus credenciales en Render
        "user_env": "CODERE_USER",
        "pass_env": "CODERE_PASS",
    },
    # Puedes añadir más casas aquí siguiendo el mismo esquema
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
        # En Render: headless=True. Para depurar local, puedes poner False.
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        )
        page = context.new_page()

        # 1) Ir a la home
page.goto(cfg["login_url"], wait_until="networkidle")

# 1.1) Abrir el modal de "Iniciar sesión" / "Acceder"
opened = False
candidatos = [
    lambda: page.get_by_text("Iniciar sesión", exact=False).first.click(timeout=3000),
    lambda: page.get_by_text("Acceder", exact=False).first.click(timeout=3000),
    lambda: page.get_by_role("button", name="Iniciar sesión").click(timeout=3000),
    lambda: page.get_by_role("button", name="Acceder").click(timeout=3000),
    lambda: page.locator('a:has-text("Iniciar sesión")').first.click(timeout=3000),
    lambda: page.locator('a:has-text("Acceder")').first.click(timeout=3000),
]
for intento in candidatos:
    try:
        intento()
        opened = True
        break
    except:
        pass

if not opened:
    raise RuntimeError("No pude abrir el modal de login (no encontré el botón Acceder/Iniciar sesión)")

# 1.2) Esperar a que aparezcan los campos del formulario
# probamos varias opciones típicas
user_sel = 'input[name="username"], input[type="email"], input[autocomplete="username"]'
pass_sel = 'input[name="password"], input[type="password"], input[autocomplete="current-password"]'

page.wait_for_selector(user_sel, timeout=12000)
page.wait_for_selector(pass_sel, timeout=12000)

# 2) Rellenar usuario y contraseña
page.fill(user_sel, user)
page.fill(pass_sel, pwd)

# 3) Clic en entrar/enviar dentro del modal
login_btn_candidates = [
    'button[type="submit"]',
    'button:has-text("Entrar")',
    'button:has-text("Iniciar sesión")',
    'button:has-text("Acceder")',
]
clicked = False
for sel in login_btn_candidates:
    try:
        page.click(sel, timeout=3000)
        clicked = True
        break
    except:
        pass
if not clicked:
    raise RuntimeError("No encontré el botón para enviar el login")

# 4) Espera a que termine la carga tras el login
page.wait_for_load_state("networkidle")

        # 5) Intentar localizar el saldo
        saldo_text = None

        # Intento 1: selector directo
        try:
            page.wait_for_selector(cfg["selector_saldo"], timeout=12000)
            saldo_text = page.inner_text(cfg["selector_saldo"]).strip()
        except:
            pass

        # Intento 2: buscar por texto "Saldo" (y extraer el número cercano)
        if not saldo_text:
            try:
                saldo_locator = page.get_by_text("Saldo", exact=False).first
                container_text = ""
                try:
                    container_text = saldo_locator.locator("xpath=..").inner_text().strip()
                except:
                    pass
                if not container_text:
                    try:
                        container_text = saldo_locator.inner_text().strip()
                    except:
                        pass

                # Patrón típico de importes con €: 1.234,56 € o 123,45 €
                m = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*€|\d+,\d{2}\s*€", container_text)
                if m:
                    saldo_text = m.group(0)
            except:
                pass

        if not saldo_text:
            # Aquí podríamos añadir más estrategias (navegar a perfil/cartera, etc.)
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
