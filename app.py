from fastapi import FastAPI, HTTPException
import os, time

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up"}

# ======= CONFIGURACIÓN DE CASAS (puedes añadir más) =======
# El "key" (p. ej. "codere") será lo que pongas en la URL: /saldo/codere
CASAS = {
    "codere": {
        "user_env": "CODERE_USER",
        "pass_env": "CODERE_PASS",
    },
    # ejemplo extra (desactívalo si no lo usas todavía)
    # "bet365": {
    #     "user_env": "BET365_USER",
    #     "pass_env": "BET365_PASS",
    # },
}

def leer_saldo_placeholder(casa_key: str):
    """
    Placeholder temporal que simula la lectura del saldo.
    Más adelante aquí meteremos Playwright para loguear y extraer el saldo real.
    """
    cfg = CASAS.get(casa_key)
    if not cfg:
        raise RuntimeError(f"Casa no soportada: {casa_key}")

    user = os.getenv(cfg["user_env"])
    pwd  = os.getenv(cfg["pass_env"])
    if not user or not pwd:
        raise RuntimeError(f"Faltan credenciales en variables de entorno para {casa_key}")

    # Simulación de saldo (luego se reemplaza con scraping real)
    saldo_text = "123,45 €"
    try:
        saldo_num = float(saldo_text.replace("€","").replace(".","").replace(",","."))
    except:
        saldo_num = None

    return {
        "casa": casa_key,
        "saldo_raw": saldo_text,
        "saldo_num": saldo_num,
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S")
    }

@app.get("/saldo/{casa}")
def saldo(casa: str):
    try:
        data = leer_saldo_placeholder(casa)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
