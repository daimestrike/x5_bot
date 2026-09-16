'use strict';
const $ = id => document.getElementById(id);
let token = '', user = '', conversation = '', busy = false, pending = null;
function newId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('');
}
function add(text, own = false) {
  const box = document.createElement('article');
  box.className = own ? 'message own' : 'message';
  const p = document.createElement('p');
  p.textContent = text;
  box.appendChild(p);
  $('messages').appendChild(box);
  return box;
}
function show(message) {
  const box = add(message.text);
  if (message.card_id) {
    const meta = document.createElement('small');
    meta.textContent = `${message.card_id} · версия ${message.version}`;
    box.appendChild(meta);
  }
  const group = document.createElement('div');
  group.className = 'buttons';
  for (const option of message.buttons) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = option.label;
    b.addEventListener('click', () => send({type: 'action', action: option.action}));
    group.appendChild(b);
  }
  box.appendChild(group);
  box.scrollIntoView({behavior:'smooth', block:'nearest'});
}
async function send(fields, retry = false) {
  if (busy) return false;
  busy = true;
  $('status').textContent = 'Отправка…';
  const event = retry ? pending : {event_id:newId(), user_id:user, conversation_id:conversation, ...fields};
  pending = event;
  try {
    const response = await fetch('/api/console', {method:'POST', headers:{'Content-Type':'application/json', Authorization:`Bearer ${token}`}, body:JSON.stringify(event), signal:AbortSignal.timeout(12000)});
    if (!response.ok) {
      const error = new Error(response.status === 401 ? 'Неверный ключ. Перезагрузите страницу и войдите заново.' : `Сервис вернул ошибку ${response.status}.`);
      error.retryable = response.status === 429 || response.status >= 500;
      throw error;
    }
    const data = await response.json();
    for (const message of data.messages) show(message);
    pending = null;
    if (event.type === 'opened') {
      $('token').value = '';
      $('login').hidden = true; $('chat').hidden = false;
      $('query').focus();
    }
    if (event.type === 'closed') {
      $('messages').replaceChildren(); token = ''; user = ''; conversation = '';
      $('chat').hidden = true; $('login').hidden = false;
    }
    $('status').textContent = '';
    return true;
  } catch (err) {
    $('status').textContent = err.message + ' ';
    if (err.retryable !== false) {
      const retryButton = document.createElement('button');
      retryButton.textContent = 'Повторить запрос';
      retryButton.addEventListener('click', () => send(null, true));
      $('status').appendChild(retryButton);
    }
    return false;
  } finally { busy = false; }
}
$('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  token = $('token').value.trim();
  user = newId(); conversation = newId();
  $('messages').replaceChildren();
  if (await send({type:'opened'})) {
    $('token').value = '';
    $('login').hidden = true; $('chat').hidden = false;
    $('query').focus();
  }
});
$('message-form').addEventListener('submit', async event => {
  event.preventDefault();
  const text = $('query').value.trim();
  if (!text || busy) return;
  add(text, true);
  $('query').value = '';
  await send({type:'message', text});
});
$('end').addEventListener('click', async () => {
  if (await send({type:'closed'})) {
    $('messages').replaceChildren(); token = ''; user = ''; conversation = '';
    $('chat').hidden = true; $('login').hidden = false;
    $('status').textContent = 'Сеанс завершён. Бот не отправляет напоминаний.';
  }
});
$('menu').addEventListener('click', () => send({type:'action', action:'menu'}));
$('search').addEventListener('click', () => send({type:'action', action:'search'}));
