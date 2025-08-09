from fastapi import FastAPI

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "service up"}

# Placeholder: más adelante aquí añadiremos /saldo/{casa} con login real
