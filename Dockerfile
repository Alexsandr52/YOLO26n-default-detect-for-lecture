FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data MODEL_PATH=/models/yolo26n.pt YOLO_CONFIG_DIR=/tmp/ultralytics
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements.txt
# Download the pretrained weights at build time so the first lecture request does not wait for them.
RUN mkdir -p /models /data && cd /models && python -c "from ultralytics import YOLO; YOLO('yolo26n.pt')"
COPY app ./app
COPY yolo26_colab.ipynb ./yolo26_colab.ipynb
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
