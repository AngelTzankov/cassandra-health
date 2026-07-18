FROM python:3.13-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn qrcode pillow python-multipart

COPY app.py parser.py llm.py ./
COPY fixtures ./fixtures

ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
