const fileInput = document.querySelector('#file');
const choose = document.querySelector('#choose');
const submit = document.querySelector('#submit');
const section = document.querySelector('#result-section');
const status = document.querySelector('#status');
const message = document.querySelector('#message');
const media = document.querySelector('#media');
const actions = document.querySelector('#result-actions');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let busy = false;
choose.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  const file = fileInput.files[0];
  document.querySelector('#filename').textContent = file ? file.name : 'Файл не выбран';
  document.querySelector('#filesize').textContent = file ? `${(file.size / 1024 / 1024).toFixed(1)} МБ` : '';
  submit.disabled = !file || busy;
  section.hidden = true;
});
function showError(text) { status.textContent = 'Ошибка'; message.textContent = text; message.className = 'message error'; }
async function readResponse(response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось выполнить запрос. Попробуйте ещё раз.');
  }
  return response.json();
}
submit.addEventListener('click', async () => {
  const file = fileInput.files[0];
  if (!file || busy) return;
  busy = true; submit.disabled = true; choose.disabled = true; section.hidden = false;
  media.hidden = true; media.replaceChildren(); actions.hidden = true; message.className = 'message';
  status.textContent = 'Загрузка'; message.textContent = 'Отправляем файл на сервер.';
  try {
    const data = new FormData(); data.append('file', file);
    const job = await readResponse(await fetch('/api/jobs', {method:'POST', body:data}));
    for (;;) {
      await wait(1200);
      const state = await readResponse(await fetch(`/api/jobs/${job.id}`, {cache:'no-store'}));
      if (state.status === 'queued') { status.textContent = 'В очереди'; message.textContent = 'Ожидаем свободный обработчик.'; }
      else if (state.status === 'processing') { status.textContent = 'Обработка'; message.textContent = 'Модель ищет объекты. Видео может обрабатываться долго.'; }
      else if (state.status === 'error') throw new Error(state.error || 'Не удалось обработать файл.');
      else if (state.status === 'done') {
        status.textContent = 'Готово'; message.textContent = 'Результат доступен в течение часа.';
        const url = `/api/jobs/${job.id}/result`;
        const element = document.createElement(state.kind === 'image' ? 'img' : 'video');
        if (state.kind === 'image') element.alt = 'Результат детекции объектов'; else {element.controls = true; element.playsInline = true;}
        element.src = url; media.replaceChildren(element); media.hidden = false;
        document.querySelector('#download').href = `${url}?download=true`;
        const time = Math.round(state.elapsed_ms).toLocaleString('ru-RU');
        document.querySelector('#timing').textContent = state.kind === 'image' ? `Время обработки: ${time} мс` : `Всего: ${time} мс · ${Math.round(state.frame_ms).toLocaleString('ru-RU')} мс/кадр · кадров: ${state.frames.toLocaleString('ru-RU')}`;
        actions.hidden = false; break;
      }
    }
  } catch (error) { showError(error.message || 'Ошибка соединения. Попробуйте ещё раз.'); }
  finally {busy = false; submit.disabled = !fileInput.files.length; choose.disabled = false;}
});
fetch('/api/config').then(response => response.json()).then(config => {
  if (config.colab_url) {
    const link = document.querySelector('#notebook');
    link.href = config.colab_url; link.textContent = 'Открыть в Colab'; link.target = '_blank'; link.rel = 'noopener noreferrer';
  }
}).catch(() => {});
