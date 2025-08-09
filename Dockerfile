# Imagen oficial de Playwright con navegadores y deps preinstalados
FROM mcr.microsoft.com/playwright/python:v1.54.0-jammy


WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Render usará PORT, lo exponemos y arrancamos uvicorn
ENV PORT=10000
EXPOSE 10000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]
