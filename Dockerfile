# Usa a imagem Python slim para builds menores
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Copia só o requirements e instala (isso aproveita cache entre builds)
COPY requirements.txt .

# Atualiza pip e instala dependências (inclua gunicorn no seu requirements.txt!)
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia o restante do código
COPY . .

# Exponha a porta onde o Flask/Gunicorn irá rodar
EXPOSE 5000

# Variável de ambiente para o Railway
ENV PORT=5000

# Comando padrão para iniciar o app
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:5000"]
