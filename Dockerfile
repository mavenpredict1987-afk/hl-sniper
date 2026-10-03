FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip --no-cache-dir install -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
