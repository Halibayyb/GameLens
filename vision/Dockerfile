FROM python:3.12-slim

# OpenCV's runtime libraries. The base image stays Debian slim.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU wheels by default so a local build runs without a GPU. The Azure GPU
# image is the same Dockerfile with the CUDA index:
#   docker build -f vision/Dockerfile --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124 .
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
COPY vision/requirements.txt /tmp/vision-requirements.txt
RUN pip install --no-cache-dir torch torchvision --index-url ${TORCH_INDEX} \
    && pip install --no-cache-dir -r /tmp/vision-requirements.txt

# Prepared models (vision/models.py): the COCO fallback and pose weights from Ultralytics, and the
# roboflow/sports football weights from the build context (fetch them with `python -m vision.models`).
ENV MODEL_DIR=/app/models
RUN mkdir -p /app/models && cd /app/models \
    && python -c "from ultralytics import YOLO; YOLO('yolo11n.pt'); YOLO('yolo11n-pose.pt')"
COPY models/football-player-detection.pt models/football-ball-detection.pt models/football-pitch-detection.pt /app/models/

COPY engine /app/engine
COPY feeds /app/feeds
COPY highlights /app/highlights
COPY scripts /app/scripts
COPY vision /app/vision
ENV PYTHONPATH=/app
ENV VISION_DEVICE=cpu
ENV VISION_OUT=/tmp/vision
EXPOSE 8000
CMD ["uvicorn", "vision.server:app", "--host", "0.0.0.0", "--port", "8000"]
