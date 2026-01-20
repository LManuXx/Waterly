# Usamos una versión ligera de Python
FROM python:3.9-slim

# Creamos carpeta de trabajo dentro del contenedor
WORKDIR /app

# Copiamos los requisitos primero (para aprovechar la caché de Docker)
COPY requirements.txt .

# Instalamos librerías
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el resto del código
COPY . .

# Comando para arrancar la API
# --host 0.0.0.0 es obligatorio para que se vea desde fuera del contenedor
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]