from fastapi import FastAPI, HTTPException
import os, time, re, base64
from pathlib import Path
from playwright.sync_api import sync_playwright

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up"}

# === Configuración de casas soportadas ===
CASAS = {
    "codere": {
        # Ya logueado con storage_state
        "home_url": "https://m.apuestas.codere.es/deportesEs/#/HomePage",
        "alt_urls": [
            "https://m.apuestas.codere.es/deportesEs/#/MyAccount",
            "https://m.apuestas.codere.es/deportesEs/#/Wallet",
            "https://m.apuestas.codere.es/deportesEs/#/Account",
        ],
        # Selectores y búsqueda por texto para saldo
        "selector_saldo": (
            "[data-testid='balance'], .balance, .saldo, "
            "[class*='balance'], [class*='wallet'], [class*='account']"
        ),
        # Variable de entorno con la sesión en base64
        "state_env": "CODERE_STATE_B64",
    },
}

def _write_state_from_env(env_key: str) -> str:
    """Decodifica el storage_state (base64) desde env y lo guarda en /tmp/*.json."""
    b64 = os.getenv(env_key)
    if not b64:
        raise RuntimeError(
            f"No hay variable de entorno {env_key} con el storage_state en base64."
        )
    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        raise RuntimeError(f"No pude decodificar {env_key}: {e}")
    tmp_path = Path("/tmp") / f"{env_key.lower()}.json"
    tmp_path.write_bytes(raw)
    return str(tmp_path)

def _try_read_balance(ctx, selector_saldo: str):
    """Intenta leer saldo por selector directo o por texto cercano ('Saldo', 'Balance', etc.)."""
    # 1) Selector directo
    try:
        ctx.wait_for_selector(selector_saldo, timeout=8000)
        return ctx.inner_text(selector_saldo).strip()
    except:
        pass

    # 2) Por texto visible
    palabras = ["Saldo", "Balance", "Mi saldo", "Disponible"]
    for palabra in palabras:
        try:
            loc = ctx.get_by_text(palabra, exact=False).first
            # Subir a contenedor y extraer cantidad con €
            try:
                txt = loc.locator("xpath=..").inner_text().strip()
            except:
                txt = loc.inner_text().strip()
            m = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*€|\d+,\d{2}\s*€", txt)
            if m:
                return m.group(0)
        except:
            continue
    return None

def leer_saldo_playwright(casa_key: str):
    cfg = CASAS.get(casa_key)
    if not cfg:
        raise RuntimeError(f"Casa no soportada: {casa_key}")

    # Cargar storage_state desde env
    state_file = _write_state_from_env(cfg["state_env"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Emulación móvil (igual que cuando guardaste la sesión)
        context = browser.new_context(
            storage_state=state_file,
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

        # 1) Ir a Home (debería reconocerte logueado)
        page.goto(cfg["home_url"], wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        # 2) Intentar leer saldo en Home
        saldo_text = _try_read_balance(page, cfg["selector_saldo"])

        # 3) Probar en iframes si no se encontró
        if not saldo_text:
            for fr in page.frames:
                saldo_text = _try_read_balance(fr, cfg["selector_saldo"])
                if saldo_text:
                    break

        # 4) Probar rutas alternativas (Mi cuenta / Wallet / Account)
        if not saldo_text:
            for url in cfg["alt_urls"]:
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_load_state("networkidle")
                    saldo_text = _try_read_balance(page, cfg["selector_saldo"])
                    if not saldo_text:
                        for fr in page.frames:
                            saldo_text = _try_read_balance(fr, cfg["selector_saldo"])
                            if saldo_text:
                                break
                    if saldo_text:
                        break
                except:
                    continue

        if not saldo_text:
            frame_info = [(fr.url, fr.name) for fr in page.frames]
            browser.close()
            raise RuntimeError(
                f"No pude encontrar el saldo con la sesión guardada. Frames vistos: {frame_info}"
            )

        browser.close()

    # Normalizar a número (opcional)
    try:
        saldo_num = float(
            saldo_text.replace("€", "").replace(".", "").replace(",", ".")
        )
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
