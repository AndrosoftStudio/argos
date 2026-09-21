# O site nao usa nenhuma biblioteca de fora: e a stdlib do Python servindo a pagina
# e repassando /api/status para o PC que o hub indicar.
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HUB_URL=https://backend-hub-vigilanciaepi.onrender.com

WORKDIR /app
COPY app.py ./
COPY api/ ./api/
COPY public/ ./public/

# nao roda como root
RUN adduser -D -H argos
USER argos

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=4)"

CMD ["python", "app.py"]
