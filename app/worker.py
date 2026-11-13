import logging
import os
import subprocess
import time
from pathlib import Path

from app import store

log = logging.getLogger(__name__)


def process_image(model, job):
    from PIL import Image, ImageOps
    start = time.perf_counter()
    # Apply phone camera orientation before detection.
    with Image.open(job['input_path']) as image:
        image = ImageOps.exif_transpose(image).convert('RGB')
        results = model.predict(source=image, verbose=False, device='cpu')
    output = store.DATA / f"{job['id']}-result.jpg"
    results[0].save(filename=str(output))
    return output, (time.perf_counter() - start) * 1000, None, None


def process_video(model, job):
    import cv2
    start = time.perf_counter()
    cap = cv2.VideoCapture(job['input_path'])
    if not cap.isOpened():
        raise ValueError('Не удалось открыть видео')
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not 0 < fps <= 240:
        cap.release()
        raise ValueError('Не удалось определить частоту кадров видео')
    output = store.DATA / f"{job['id']}-result.mp4"
    encoder = None
    frames = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            annotated = model.predict(source=frame, verbose=False, device='cpu')[0].plot()
            if encoder is None:
                height, width = annotated.shape[:2]
                encoder = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size', f'{width}x{height}',
                    '-framerate', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264',
                    '-preset', 'veryfast', '-threads', '1', '-pix_fmt', 'yuv420p',
                    '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-movflags', '+faststart', str(output)],
                    stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            encoder.stdin.write(annotated.tobytes())
            frames += 1
        if not frames:
            raise ValueError('В видео нет доступных кадров')
        encoder.stdin.close()
        if encoder.wait() != 0:
            raise RuntimeError('Не удалось сохранить видео')
    except Exception:
        if encoder and encoder.poll() is None:
            encoder.kill()
            encoder.wait()
        output.unlink(missing_ok=True)
        raise
    finally:
        cap.release()
    elapsed = (time.perf_counter() - start) * 1000
    return output, elapsed, elapsed / frames, frames


def run(kind):
    # Each process has its own model; do not share an Ultralytics predictor between workers.
    os.environ.setdefault('OMP_NUM_THREADS', os.getenv('MODEL_THREADS', '1'))
    from ultralytics import YOLO
    import torch
    torch.set_num_threads(int(os.getenv('MODEL_THREADS', '1')))
    model = YOLO(os.getenv('MODEL_PATH', '/models/yolo26n.pt'))
    while True:
        job = store.claim(kind)
        if not job:
            time.sleep(0.4)
            continue
        try:
            result = process_image(model, job) if kind == 'image' else process_video(model, job)
            store.finish(job['id'], *result)
        except Exception:
            log.exception('Job %s failed', job['id'])
            store.fail(job['id'], 'Не удалось обработать файл. Попробуйте другой файл.')
