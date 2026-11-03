FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Primero las dependencias (se cachean si no cambian)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Luego el código
COPY . .

# La API corre con un usuario sin privilegios
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]