import asyncio
import json
import logging
import multiprocessing
import os
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from app import store
from app.worker import run

ROOT = Path(__file__).resolve().parent
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp'}
VIDEO_EXT = {'.mp4', '.mov', '.webm', '.avi', '.mkv'}
IMAGE_LIMIT = 20 * 1024 * 1024
VIDEO_LIMIT = 100 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000
logging.basicConfig(level=logging.INFO)


async def cleanup_loop():
    while True:
        try:
            await asyncio.to_thread(store.cleanup)
        except Exception:
            logging.exception('Cleanup failed')
        await asyncio.sleep(60)


async def monitor_workers(processes, ctx):
    while True:
        await asyncio.sleep(5)
        for index, (kind, process) in enumerate(processes):
            if not process.is_alive():
                logging.error('%s worker %s stopped; restarting', kind, process.pid)
                store.recover_worker(process.pid)
                process.join()
                replacement = ctx.Process(target=run, args=(kind,), daemon=True)
                replacement.start()
                processes[index] = (kind, replacement)


@asynccontextmanager
async def lifespan(app):
    store.init()
    store.recover()
    processes = []
    ctx = multiprocessing.get_context('spawn')
    for kind, variable, default in [('image', 'IMAGE_WORKERS', '2'), ('video', 'VIDEO_WORKERS', '1')]:
        for _ in range(max(1, int(os.getenv(variable, default)))):
            process = ctx.Process(target=run, args=(kind,), daemon=True)
            process.start()
            processes.append((kind, process))
    task = asyncio.create_task(cleanup_loop())
    monitor = asyncio.create_task(monitor_workers(processes, ctx))
    yield
    task.cancel()
    monitor.cancel()
    for _, process in processes:
        process.terminate()
    for _, process in processes:
        process.join(timeout=5)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
def home():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/config')
def config():
    return {'colab_url': os.getenv('COLAB_URL', '').strip()}


@app.get('/notebook')
def notebook():
    return FileResponse(ROOT.parent / 'yolo26_colab.ipynb', filename='yolo26_colab.ipynb', media_type='application/x-ipynb+json')


def validate(path, kind):
    if kind == 'image':
        try:
            with Image.open(path) as img:
                if img.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError('format')
                if img.width * img.height > 40_000_000:
                    raise ValueError('dimensions')
                img.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise HTTPException(400, 'Не удалось прочитать изображение или оно слишком большое.') from exc
    else:
        try:
            probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                'stream=codec_type', '-of', 'json', str(path)], capture_output=True, timeout=20, check=True)
            if not json.loads(probe.stdout).get('streams'):
                raise ValueError('no video')
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise HTTPException(400, 'Не удалось прочитать видео.') from exc


@app.post('/api/jobs', status_code=202)
async def create_job(file: UploadFile = File(...)):
    ext = Path(file.filename or '').suffix.lower()
    if ext in IMAGE_EXT:
        kind, limit = 'image', IMAGE_LIMIT
    elif ext in VIDEO_EXT:
        kind, limit = 'video', VIDEO_LIMIT
    else:
        raise HTTPException(400, 'Поддерживаются JPG, PNG, WebP, MP4, MOV, WebM, AVI и MKV.')
    job_id = uuid.uuid4().hex
    path = store.DATA / f'{job_id}-input{ext}'
    accepted = False
    try:
        size = 0
        with path.open('wb') as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f'Файл больше {limit // (1024 * 1024)} МБ.')
                target.write(chunk)
        if not size:
            raise HTTPException(400, 'Файл пуст.')
        await asyncio.to_thread(validate, path, kind)
        if not store.enqueue(job_id, kind, path):
            raise HTTPException(503, 'Очередь заполнена. Попробуйте ещё раз через некоторое время.')
        accepted = True
        return {'id': job_id, 'status': 'queued'}
    finally:
        await file.close()
        if not accepted:
            path.unlink(missing_ok=True)


@app.get('/api/jobs/{job_id}')
def job_status(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, 'Задание не найдено или срок хранения истёк.')
    return {key: job[key] for key in ('id', 'kind', 'status', 'elapsed_ms', 'frame_ms', 'frames', 'error')}


@app.get('/api/jobs/{job_id}/result')
def result(job_id: str, download: bool = False):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, 'Результат не найден или срок хранения истёк.')
    if job['status'] != 'done':
        raise HTTPException(409, 'Результат ещё не готов.')
    path = Path(job['output_path'])
    if not path.is_file():
        raise HTTPException(404, 'Файл результата не найден.')
    media = 'image/jpeg' if job['kind'] == 'image' else 'video/mp4'
    response = FileResponse(path, media_type=media, filename=('yolo26-result.jpg' if job['kind'] == 'image' else 'yolo26-result.mp4'), content_disposition_type='attachment' if download else 'inline')
    response.headers['Cache-Control'] = 'no-store'
    return response
