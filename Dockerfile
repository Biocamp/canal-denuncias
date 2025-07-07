# Usa a imagem Python slim para builds menores
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Copia só o requirements e instala (aproveita cache entre builds)
COPY requirements.txt .

# Atualiza pip e instala dependências (inclua gunicorn no seu requirements.txt!)
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia o restante do código
COPY . .

# Exponha a porta padrão (não precisa mexer aqui)
EXPOSE 5000

# Variável de ambiente para o Railway — auxilia em dev local, mas o binding real virá de $PORT
ENV PORT=5000

# Ajuste: usa a porta dinâmica do Railway em vez de hard-code 5000
CMD gunicorn main:app --bind 0.0.0.0:$PORT
