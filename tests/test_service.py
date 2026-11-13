import io
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(store, 'MAX_QUEUE', 2)
    store.init()

    @asynccontextmanager
    async def no_workers(_):
        yield
    monkeypatch.setattr(main.app.router, 'lifespan_context', no_workers)
    with TestClient(main.app) as client:
        yield client


def image_bytes():
    buffer = io.BytesIO()
    Image.new('RGB', (20, 20), 'red').save(buffer, format='PNG')
    return buffer.getvalue()


def test_upload_status_result_and_cleanup(client, monkeypatch):
    response = client.post('/api/jobs', files={'file': ('test.png', image_bytes(), 'image/png')})
    assert response.status_code == 202
    job_id = response.json()['id']
    assert client.get(f'/api/jobs/{job_id}').json()['status'] == 'queued'
    job = store.claim('image')
    assert job['id'] == job_id
    assert client.get(f'/api/jobs/{job_id}/result').status_code == 409
    result = store.DATA / f'{job_id}-result.jpg'
    result.write_bytes(b'result')
    store.finish(job_id, result, 12.5)
    assert client.get(f'/api/jobs/{job_id}').json()['elapsed_ms'] == 12.5
    assert client.get(f'/api/jobs/{job_id}/result').content == b'result'
    assert 'attachment' in client.get(f'/api/jobs/{job_id}/result?download=true').headers['content-disposition']
    monkeypatch.setattr(store, 'TTL', -1)
    store.cleanup()
    assert client.get(f'/api/jobs/{job_id}').status_code == 404
    assert not result.exists() and not Path(job['input_path']).exists()


def test_reject_invalid_and_full_queue(client):
    assert client.post('/api/jobs', files={'file': ('bad.txt', b'text', 'text/plain')}).status_code == 400
    assert client.post('/api/jobs', files={'file': ('bad.png', b'not an image', 'image/png')}).status_code == 400
    for _ in range(2):
        assert client.post('/api/jobs', files={'file': ('test.png', image_bytes(), 'image/png')}).status_code == 202
    assert client.post('/api/jobs', files={'file': ('test.png', image_bytes(), 'image/png')}).status_code == 503
    assert len(list(store.DATA.glob('*-input*'))) == 2


def test_file_size_limit(client, monkeypatch):
    monkeypatch.setattr(main, 'IMAGE_LIMIT', 10)
    response = client.post('/api/jobs', files={'file': ('large.png', image_bytes(), 'image/png')})
    assert response.status_code == 413
    assert not list(store.DATA.glob('*-input*'))


def test_video_and_images_use_separate_workers(client, monkeypatch):
    monkeypatch.setattr(main, 'validate', lambda *_: None)
    video = client.post('/api/jobs', files={'file': ('clip.mp4', b'video', 'video/mp4')}).json()['id']
    image = client.post('/api/jobs', files={'file': ('test.png', image_bytes(), 'image/png')}).json()['id']
    assert store.claim('video')['id'] == video
    assert store.get(video)['status'] == 'processing'
    assert store.claim('image')['id'] == image
    assert store.get(image)['status'] == 'processing'
    store.recover()
    assert store.get(video)['status'] == 'queued'
    assert store.get(image)['status'] == 'queued'
