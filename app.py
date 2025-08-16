from fastapi import FastAPI, HTTPException
import os, time, re, base64
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up – v-storage"}


CASAS = {
    "codere": {
        "home_url": "https://m.apuestas.codere.es/deportesEs/#/HomePage",
        "alt_urls": [
            "https://m.apuestas.codere.es/deportesEs/#/MyAccount",
            "https://m.apuestas.codere.es/deportesEs/#/Wallet",
            "https://m.apuestas.codere.es/deportesEs/#/Account",
        ],
        "selector_saldo": (
            "[data-testid='balance'], .balance, .saldo, "
            "[class*='balance'], [class*='wallet'], [class*='account']"
        ),
        "state_env": "CODERE_STATE_B64",
    },
}

def _write_state_from_env(env_key: str) -> str:
    b64 = os.getenv(env_key)
    if not b64:
        raise RuntimeError(f"No hay variable de entorno {env_key} con el storage_state en base64.")
    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        raise RuntimeError(f"No pude decodificar {env_key}: {e}")
    tmp_path = Path("/tmp") / f"{env_key.lower()}.json"
    tmp_path.write_bytes(raw)
    return str(tmp_path)

def _launch_browser(p):
    engine = (os.getenv("PW_ENGINE") or "chromium").lower().strip()
    if engine == "webkit":
        return p.webkit.launch(headless=True)
    if engine == "firefox":
        return p.firefox.launch(headless=True)
    return p.chromium.launch(headless=True)  # default

def _new_mobile_context(browser, storage_state):
    return browser.new_context(
        storage_state=storage_state,
        user_agent=("Mozilla/5.0 (Linux; Android 12; Pixel 5) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Mobile Safari/537.36"),
        viewport={"width": 414, "height": 896},
        is_mobile=True,
        device_scale_factor=2,
        has_touch=True,
        locale="es-ES",
    )

def _try_read_balance(ctx, selector_saldo: str):
    # 1) Por selector directo
    try:
        ctx.wait_for_selector(selector_saldo, timeout=6000)
        return ctx.inner_text(selector_saldo).strip()
    except:
        pass
    # 2) Por texto cercano
    palabras = ["Saldo", "Balance", "Mi saldo", "Disponible"]
    for palabra in palabras:
        try:
            loc = ctx.get_by_text(palabra, exact=False).first
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

    state_file = _write_state_from_env(cfg["state_env"])

    with sync_playwright() as p:
        browser = _launch_browser(p)
        context = _new_mobile_context(browser, state_file)
        page = context.new_page()

        # límites de espera para que no "se quede pensando"
        page.set_default_timeout(8000)
        page.set_default_navigation_timeout(15000)

        # 1) Home
        page.goto(cfg["home_url"], wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)

        # 2) Intentar leer en home
        saldo_text = _try_read_balance(page, cfg["selector_saldo"])

        # 3) Probar frames
        if not saldo_text:
            for fr in page.frames:
                saldo_text = _try_read_balance(fr, cfg["selector_saldo"])
                if saldo_text:
                    break

        # 4) Probar rutas alternativas
        if not saldo_text:
            for url in cfg["alt_urls"]:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_load_state("networkidle", timeout=10000)
                    saldo_text = _try_read_balance(page, cfg["selector_saldo"])
                    if not saldo_text:
                        for fr in page.frames:
                            saldo_text = _try_read_balance(fr, cfg["selector_saldo"])
                            if saldo_text: break
                    if saldo_text:
                        break
                except:
                    continue

        browser.close()

    if not saldo_text:
        raise RuntimeError("No pude encontrar el saldo con la sesión guardada (tras timeouts).")

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

@app.get("/debug/{casa}")
def debug_casa(casa: str):
    """Diagnóstico: comprueba sesión, recorre rutas internas y busca indicios de saldo."""
    cfg = CASAS.get(casa)
    if not cfg:
        raise HTTPException(status_code=400, detail="Casa no soportada")

    state_file = _write_state_from_env(cfg["state_env"])

    with sync_playwright() as p:
        browser = _launch_browser(p)
        context = _new_mobile_context(browser, state_file)
        page = context.new_page()
        page.set_default_timeout(7000)
        page.set_default_navigation_timeout(12000)

        tried = []

        def scan(ctx, label):
            # intenta recoger título, url, fragmentos de texto y números con €
            out = {"label": label, "url": ctx.url if hasattr(ctx, "url") else "", "foundText": [], "foundNumber": None}
            try:
                # por palabras clave
                for palabra in ["Saldo", "Balance", "Mi saldo", "Disponible", "Cuenta", "Wallet"]:
                    try:
                        loc = ctx.get_by_text(re.compile(palabra, re.I), exact=False).first
                        snippet = loc.inner_text()[:160]
                        out["foundText"].append({palabra: snippet})
                    except:
                        continue
                # por HTML (aunque no sea visible)
                try:
                    html = ctx.content()
                    m = re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*€|\d+,\d{2}\s*€", html)
                    if m:
                        out["foundNumber"] = m.group(0)
                except:
                    pass
            except:
                pass
            return out

        # 1) Home
        page.goto(cfg["home_url"], wait_until="domcontentloaded", timeout=12000)
        try: page.wait_for_load_state("networkidle", timeout=8000)
        except: pass
        tried.append(scan(page, "Home"))
        for fr in page.frames:
            tried.append(scan(fr, f"HomeFrame:{fr.name or 'unnamed'}"))

        # 2) Rutas internas
        for url in cfg.get("alt_urls", []):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=12000)
                try: page.wait_for_load_state("networkidle", timeout=8000)
                except: pass
                tried.append(scan(page, url))
                for fr in page.frames:
                    tried.append(scan(fr, f"{url}::Frame:{fr.name or 'unnamed'}"))
            except:
                tried.append({"label": url, "error": "navigation failed"})

        # 3) Señales de login / cookies
        cookies = context.cookies()
        cookie_domains = sorted(list({c.get("domain","") for c in cookies}))
        title = ""
        try: title = page.title()
        except: pass

        info = {
            "engine": (os.getenv("PW_ENGINE") or "chromium"),
            "currentUrl": page.url,
            "title": title,
            "cookieDomains": cookie_domains,
            "cookiesCount": len(cookies),
            "tried": tried,
        }
        browser.close()

    return info
