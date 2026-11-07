FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV STOCKPRO_HOME=/data HOST=0.0.0.0
VOLUME /data
CMD ["python", "run.py", "--no-browser"]
