'use strict';

// The browser only renders state; Python is the single source of truth.
let state = null;
let replayId = null;
let videoStream = null;
let sourcePhoto = null;
let selectedRect = null;
let dragStart = null;
let toastTimer = null;

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const localTime = (iso) => iso ? new Intl.DateTimeFormat('zh-TW', {timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso)) : '—';
const fullTime = (iso) => iso ? new Intl.DateTimeFormat('zh-TW', {timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso)) : '—';
const money = (v) => `NT$ ${Number(v || 0).toLocaleString('zh-TW')}`;

function toast(message, error=false) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 4700);
}

async function post(route, data) {
  const response = await fetch(route, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失敗');
  return result;
}

async function refresh(silent=false) {
  try {
    const response = await fetch('/api/state', {cache:'no-store'});
    if (!response.ok) throw new Error('後端未正常回應');
    state = await response.json();
    render();
  } catch (error) {
    if (!silent) toast('連線失敗：' + error.message, true);
  }
}

function renderGrid(grid, root, interactive=true) {
  const target = $(root);
  if (!target) return;
  target.innerHTML = (grid || []).map((row, i) => `
    <div class="lot-row"><span class="zone-mark">${['A 區','B 區','C 區'][i]}</span>
      ${row.map(spot => `<button type="button" class="spot ${spot.plate?'occupied':''} ${spot.conflict?'conflict':''}"
        ${interactive?`data-spot="${esc(spot.id)}"`:'tabindex="-1"'}
        title="${esc(spot.id + ' 系統分配：' + (spot.plate || '空') + ' / 觀測：' + (spot.observed || '無'))}">
        <span class="spot-code">${esc(spot.id)}</span>
        <span class="spot-kind">${spot.kind==='ev'?'ϟ':spot.kind==='accessible'?'◆':''}</span>
        <span class="${spot.plate?'spot-plate':'spot-empty'}">${esc(spot.plate || 'EMPTY')}</span>
        ${spot.observed ? `<span class="spot-observed">↳ ${esc(spot.observed)}</span>` : ''}
      </button>`).join('')}
    </div>`).join('');
}

function render() {
  if (!state) return;
  const m = state.metrics;
  $('#system-time').textContent = fullTime(state.now);
  $('#metric-total').textContent = m.total;
  $('#metric-occupied').textContent = m.occupied;
  $('#metric-free').textContent = m.free;
  $('#metric-waiting').textContent = m.waiting;
  $('#metric-revenue').textContent = money(m.revenue);
  $('#metric-rate').textContent = `使用率 ${Math.round(m.occupied/m.total*100)}%`;
  $('#alert-count').textContent = `${m.alerts} 筆警示`;
  renderGrid(state.grid, '#parking-grid');

  $('#anomalies').innerHTML = state.alerts.length ? state.alerts.map(a => `
    <div class="alert-row"><strong>⚠ ${esc(a.spot)} · 分配與實際觀測不符</strong>
    <span>系統：${esc(a.assigned||'空位')} ／ 現場：${esc(a.observed)}</span></div>`).join('')
    : '<div class="empty-block"><b>✓ 目前沒有車位衝突</b><div>可使用「車位觀測模擬」輸入不同車牌，觸發即時對帳。</div></div>';

  $('#active-table').innerHTML = state.active.length ? state.active.map(v => `<tr><td class="strong">${esc(v.plate)}</td><td>${esc(v.spot)}</td><td>${localTime(v.entered_at)}</td><td>${money(v.current_fee)}</td></tr>`).join('')
    : '<tr><td colspan="4">目前沒有車輛停放</td></tr>';
  $('#waiting-list').innerHTML = state.waiting.length ? state.waiting.map((v, i) => `
    <div class="queue-item"><b>#${i+1}</b><strong>${esc(v.plate)}</strong><small>${esc(v.type)}</small>
    <button type="button" data-cancel="${esc(v.plate)}" title="取消候補">取消</button></div>`).join('')
    : '<div class="empty-block">候補佇列為空。可填滿一般車位以進入 FIFO 示範。</div>';
  $('#receipts-table').innerHTML = state.receipts.length ? state.receipts.map(v => `<tr><td class="strong">${esc(v.plate)}</td><td>${esc(v.spot)}</td><td>${localTime(v.entered_at)}</td><td>${localTime(v.exited_at)}</td><td>${money(v.fee)}</td></tr>`).join('')
    : '<tr><td colspan="5">尚無完成出場的紀錄</td></tr>';

  const ds = state.structures;
  $('#ds-array').textContent = ds.array.map(row => `[ ${row.join(', ')} ]`).join('\n');
  $('#ds-queue').innerHTML = ds.queue.length ? ds.queue.map((plate, i) => `<span class="ds-node">${i===0?'FRONT ':''}${esc(plate)}</span>`).join('<span class="ds-arrow">→</span>') : '<span class="ds-blank">EMPTY QUEUE / 等待入隊</span>';
  $('#ds-stack').innerHTML = ds.stack.length ? ds.stack.map(item => `<span class="ds-node">${esc(item.action)} ${esc(item.plate || '')}</span>`).join('') : '<span class="ds-blank">EMPTY STACK / 等待操作</span>';
  $('#ds-linked').innerHTML = ds.linked.length ? ds.linked.map(node => `<span class="ds-node">${esc(node.plate)}<br>${esc(node.spot)}<br><small>prev: ${esc(node.prev||'NULL')}</small><br><small>next: ${esc(node.next||'NULL')}</small></span>`).join('<span class="ds-arrow">↔</span>') : '<span class="ds-blank">HEAD → NULL</span>';

  $('#timeline').innerHTML = state.events.length ? state.events.map(e => `<button class="${replayId===e.id?'selected':''}" data-event="${e.id}"><span>${esc(e.action)}</span><div><strong>${esc(e.plate || e.note)}</strong><small>#${e.id} · ${fullTime(e.at)} ${esc(e.spot || '')}</small></div></button>`).join('')
    : '<div class="empty-block">尚無事件</div>';
  renderReplay();
}

function renderReplay() {
  if (!state) return;
  const item = state.events.find(e => e.id===replayId);
  const snapshot = item ? item.snapshot : {grid:state.grid,waiting:state.waiting.map(x=>x.plate),occupied:state.metrics.occupied,at:state.now};
  $('#replay-title').textContent = item ? `事件 #${item.id} · ${item.action}` : '目前即時狀態';
  $('#replay-time').textContent = fullTime(snapshot.at) + ` / ${snapshot.occupied} 輛停放中`;
  $('#replay-queue').textContent = `當時 FIFO：${snapshot.waiting.join(' → ') || '空佇列'}`;
  renderGrid(snapshot.grid, '#replay-grid', false);
}

async function runAction(route, data, describe) {
  try {
    const result = await post(route, data);
    if (route === '/api/entry') {
      toast(result.status==='parked' ? `${result.plate} 已入場，分配車位 ${result.spot}` : `${result.plate} 已加入 FIFO 候補佇列`);
    } else if (route === '/api/exit') {
      toast(`${result.plate} 已出場，應付 ${money(result.fee)}${result.promoted?'；'+result.promoted.plate+' 自動補到 '+result.promoted.spot:''}`);
    } else {
      toast(describe || '操作已完成');
    }
    await refresh(true);
    return result;
  } catch (error) {
    toast(error.message, true);
    return null;
  }
}

$('#entry-form').addEventListener('submit', async event => {
  event.preventDefault();
  const result = await runAction('/api/entry', {plate:$('#entry-plate').value,vehicle_type:$('#vehicle-type').value});
  if (result) $('#entry-plate').value = '';
});
$('#exit-form').addEventListener('submit', async event => {
  event.preventDefault();
  const result = await runAction('/api/exit', {plate:$('#exit-plate').value});
  if (result) $('#exit-plate').value = '';
});
$('#observe-form').addEventListener('submit', async event => {
  event.preventDefault();
  await runAction('/api/observe', {spot:$('#observe-spot').value,plate:$('#observe-plate').value}, '車位觀測已更新');
});

for (const button of document.querySelectorAll('[data-tab]')) {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active',b===button));
    document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.toggle('hidden',panel.id!=='tab-'+button.dataset.tab));
  });
}
$('#parking-grid').addEventListener('click', event => {
  const button = event.target.closest('[data-spot]');
  if (!button) return;
  $('#observe-spot').value = button.dataset.spot;
  const item = state.grid.flat().find(s=>s.id===button.dataset.spot);
  $('#observe-plate').value = item.observed || '';
  $('#observe-plate').focus();
  toast(`${button.dataset.spot} 已帶入觀測欄位`);
});
$('#waiting-list').addEventListener('click', async event => {
  const button = event.target.closest('[data-cancel]');
  if (button) await runAction('/api/cancel-wait', {plate:button.dataset.cancel}, '已取消候補');
});
document.querySelectorAll('[data-minutes]').forEach(button => button.addEventListener('click', async () => {
  await runAction('/api/advance', {minutes:Number(button.dataset.minutes)}, `系統時間已快轉 ${button.dataset.minutes} 分鐘`);
}));
$('#reset-btn').addEventListener('click', async () => {
  if (!confirm('這會清空所有模擬車輛、歷史紀錄、觀測資料和計費。確定重置？')) return;
  replayId = null;
  await runAction('/api/reset', {}, '模擬資料已全部重置');
});
$('#timeline').addEventListener('click', event => {
  const button = event.target.closest('[data-event]');
  if (!button) return;
  replayId = Number(button.dataset.event);
  render();
});
$('#replay-live').addEventListener('click', () => { replayId = null; render(); });

const spotOptions = ['A','B','C'].flatMap(zone => Array.from({length:6}, (_,i) => `<option value="${zone}${String(i+1).padStart(2,'0')}">${zone}${String(i+1).padStart(2,'0')}</option>`));
$('#observe-spot').innerHTML = spotOptions.join('');

// Real laptop webcam: capture only on explicit user gesture; no background or automatic snapshots.
const cameraVideo = $('#camera-video');
const cameraCanvas = $('#camera-canvas');
const cameraPlaceholder = $('#camera-placeholder');
const cameraStatus = $('#camera-status');
const cameraLed = $('#camera-led');
const cameraDevice = $('#camera-device');
const cameraSnap = $('#snap-photo');
const cameraOcr = $('#run-ocr');

function setCameraStatus(message, isError=false) {
  cameraStatus.textContent = message;
  cameraStatus.style.color = isError ? '#ffae9e' : '#adc1ca';
}
function stopCamera(announce=true) {
  if (videoStream) {
    videoStream.getTracks().forEach(track => track.stop());
    videoStream = null;
  }
  cameraVideo.srcObject = null;
  cameraVideo.classList.add('hidden');
  cameraSnap.disabled = true;
  cameraLed.textContent = '● OFF';
  cameraLed.classList.remove('active');
  cameraLed.setAttribute('aria-label', '鏡頭已關閉');
  if (!sourcePhoto) cameraPlaceholder.classList.remove('hidden');
  if (announce) setCameraStatus('鏡頭已關閉。需要時可以重新啟動。');
}
async function listCameraDevices() {
  try {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(x => x.kind === 'videoinput');
    const activeDevice = videoStream?.getVideoTracks()[0]?.getSettings().deviceId || cameraDevice.value;
    cameraDevice.replaceChildren();
    const def = new Option('系統預設攝影機', '');
    cameraDevice.add(def);
    devices.forEach((device, i) => {
      if (device.deviceId) cameraDevice.add(new Option(device.label || `攝影機 ${i+1}`, device.deviceId));
    });
    if ([...cameraDevice.options].some(x => x.value === activeDevice)) cameraDevice.value = activeDevice;
  } catch (_) {
    // Device listing is optional; a single default laptop camera still works.
  }
}
function cameraErrorMessage(error) {
  switch (error?.name) {
    case 'NotAllowedError': case 'PermissionDeniedError': return '攝影機權限遭拒。請在網址列左側開啟攝影機權限，重新整理後重試。';
    case 'NotFoundError': return '找不到攝影機。請確認筆電相機未停用或接好 USB 攝影機。';
    case 'NotReadableError': return '攝影機可能被 Zoom、Teams 或其他程式佔用；請先關閉其他相機程式。';
    case 'OverconstrainedError': return '目前鏡頭不支援指定的解析度，請切換其他攝影機再試。';
    default: return `無法啟動攝影機：${error?.message || '未知錯誤'}`;
  }
}
async function startCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    setCameraStatus('相機僅支援 HTTPS 或 localhost；請使用本網站的 HTTPS 網址或改為上傳圖片。', true);
    return;
  }
  const button = $('#start-camera');
  button.disabled = true;
  setCameraStatus('正在向瀏覽器申請攝影機權限…');
  const deviceId = cameraDevice.value;
  stopCamera(false);
  try {
    const preferred = {width:{ideal:1920},height:{ideal:1080},frameRate:{ideal:30}};
    if (deviceId) preferred.deviceId = {exact:deviceId};
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({video:preferred,audio:false});
    } catch (error) {
      // A laptop camera may only support a low resolution. Try browser defaults.
      if (!['OverconstrainedError','NotFoundError'].includes(error.name) || deviceId) throw error;
      stream = await navigator.mediaDevices.getUserMedia({video:true,audio:false});
    }
    videoStream = stream;
    sourcePhoto = null;
    selectedRect = null;
    cameraCanvas.classList.add('hidden');
    cameraPlaceholder.classList.add('hidden');
    cameraVideo.classList.remove('hidden');
    cameraVideo.srcObject = stream;
    await cameraVideo.play();
    cameraSnap.disabled = false;
    cameraOcr.disabled = true;
    cameraLed.textContent = '● LIVE';
    cameraLed.classList.add('active');
    cameraLed.setAttribute('aria-label', '攝影機使用中');
    await listCameraDevices();
    const settings = stream.getVideoTracks()[0]?.getSettings() || {};
    const res = settings.width && settings.height ? `（${settings.width}×${settings.height}）` : '';
    setCameraStatus(`鏡頭已啟動 ${res}；將車牌靠近並保持清晰，按「拍攝車牌」。`);
  } catch (error) {
    stopCamera(false);
    const message = cameraErrorMessage(error);
    setCameraStatus(message,true);
    toast(message,true);
  } finally {
    button.disabled = false;
  }
}
$('#start-camera').addEventListener('click',startCamera);
$('#stop-camera').addEventListener('click',()=>stopCamera());
cameraDevice.addEventListener('change',()=>{if (videoStream) startCamera();});
$('#focus-camera').addEventListener('click',()=>{
  $('#camera-workbench').scrollIntoView({behavior:'smooth',block:'start'});
  if (!videoStream && !sourcePhoto) setCameraStatus('請按「啟動筆電鏡頭」，瀏覽器會要求授權。');
});
function loadPhoto(dataUrl) {
  const img = new Image();
  img.onload = () => {
    sourcePhoto = img;
    selectedRect = null;
    stopCamera(false);
    cameraPlaceholder.classList.add('hidden');
    cameraCanvas.classList.remove('hidden');
    cameraOcr.disabled = false;
    drawPhoto();
    $('#ocr-result').replaceChildren();
    $('#ocr-result').innerHTML = '<strong>辨識結果</strong><span>照片已備妥，建議拖曳框選車牌，按「辨識車牌」。</span>';
    setCameraStatus(`照片已載入（${img.naturalWidth}×${img.naturalHeight}），框選車牌可提高 OCR 準確度。`);
  };
  img.onerror=()=>setCameraStatus('圖片格式無法載入，請改用 JPG 或 PNG。',true);
  img.src = dataUrl;
}
cameraSnap.addEventListener('click',()=>{
  const w=cameraVideo.videoWidth, h=cameraVideo.videoHeight;
  if (!videoStream || !w || !h) {
    setCameraStatus('攝影機尚未準備好，請確認畫面有影像再試。',true);
    return;
  }
  // Preserve the largest practical frame; the browser later crops locally.
  const buffer = document.createElement('canvas');
  const ratio = Math.min(1,1920/w);
  buffer.width = Math.round(w*ratio);
  buffer.height = Math.round(h*ratio);
  buffer.getContext('2d').drawImage(cameraVideo,0,0,buffer.width,buffer.height);
  loadPhoto(buffer.toDataURL('image/jpeg',.90));
});
$('#upload-photo').addEventListener('change',event=>{
  const file=event.target.files?.[0];
  if (!file) return;
  if (!['image/jpeg','image/png','image/webp'].includes(file.type) || file.size > 3_000_000) {
    setCameraStatus('請選擇不超過 3MB 的 JPG、PNG 或 WebP 圖片。',true);
    return;
  }
  const reader=new FileReader();
  reader.onload=()=>loadPhoto(reader.result);
  reader.onerror=()=>setCameraStatus('讀取照片失敗。',true);
  reader.readAsDataURL(file);
  event.target.value = '';
});
function drawPhoto() {
  if (!sourcePhoto) return;
  const width=Math.min(1000,sourcePhoto.naturalWidth);
  const height=Math.max(1,Math.round(sourcePhoto.naturalHeight*width/sourcePhoto.naturalWidth));
  cameraCanvas.width=width;
  cameraCanvas.height=height;
  const ctx=cameraCanvas.getContext('2d');
  ctx.drawImage(sourcePhoto,0,0,width,height);
  if(selectedRect&&selectedRect.w>5&&selectedRect.h>5){
    ctx.fillStyle='#49e1bb25';ctx.strokeStyle='#72e5c4';ctx.lineWidth=4;
    ctx.fillRect(selectedRect.x,selectedRect.y,selectedRect.w,selectedRect.h);
    ctx.strokeRect(selectedRect.x,selectedRect.y,selectedRect.w,selectedRect.h);
  }
}
function pointerCoord(event) {
  const rect=cameraCanvas.getBoundingClientRect();
  return {x:Math.max(0,Math.min(cameraCanvas.width,(event.clientX-rect.left)*cameraCanvas.width/rect.width)),
    y:Math.max(0,Math.min(cameraCanvas.height,(event.clientY-rect.top)*cameraCanvas.height/rect.height))};
}
cameraCanvas.addEventListener('pointerdown',event=>{
  if(!sourcePhoto) return;
  dragStart=pointerCoord(event);
  cameraCanvas.setPointerCapture(event.pointerId);
});
cameraCanvas.addEventListener('pointermove',event=>{
  if(!dragStart)return;
  const pt=pointerCoord(event);
  selectedRect={x:Math.min(dragStart.x,pt.x),y:Math.min(dragStart.y,pt.y),
    w:Math.abs(pt.x-dragStart.x),h:Math.abs(pt.y-dragStart.y)};
  drawPhoto();
});
cameraCanvas.addEventListener('pointerup',()=>{dragStart=null;});
cameraCanvas.addEventListener('pointercancel',()=>{dragStart=null;});
// Keyboard users can OCR the full image without needing a crop.
function getPhotoData() {
  if (!sourcePhoto) throw new Error('尚未拍照');
  const scaleX=sourcePhoto.naturalWidth/cameraCanvas.width;
  const scaleY=sourcePhoto.naturalHeight/cameraCanvas.height;
  const box=(selectedRect&&selectedRect.w>20&&selectedRect.h>12)?selectedRect:{x:0,y:0,w:cameraCanvas.width,h:cameraCanvas.height};
  const crop=document.createElement('canvas');
  const sourceW=box.w*scaleX;
  const factor=Math.min(3,Math.max(.1,1400/sourceW));
  crop.width=Math.max(1,Math.round(sourceW*factor));
  crop.height=Math.max(1,Math.round(box.h*scaleY*factor));
  crop.getContext('2d').drawImage(sourcePhoto,box.x*scaleX,box.y*scaleY,sourceW,box.h*scaleY,0,0,crop.width,crop.height);
  return crop.toDataURL('image/jpeg',.89);
}
cameraOcr.addEventListener('click', async ()=>{
  if(!sourcePhoto){setCameraStatus('請先拍攝車牌或上傳照片。',true);return;}
  cameraOcr.disabled=true;
  cameraOcr.textContent='OCR 辨識中…';
  setCameraStatus('正在分析照片，請稍候；車牌結果需人工核對。');
  try {
    const response=await post('/api/ocr',{image:getPhotoData()});
    const result=$('#ocr-result');
    if(response.candidates?.length){
      const counts=response.votes || {};
      result.innerHTML='<strong>辨識候選車牌（請人工核對）</strong>'+response.candidates.map(p=>
        `<div class="ocr-candidate"><b>${esc(p)}</b><small>一致結果 ${Number(counts[p]||1)} 次</small>`+
        `<button type="button" data-ocr-entry="${esc(p)}">帶入進場</button><button type="button" data-ocr-exit="${esc(p)}">帶入出場</button></div>`
      ).join('')+'<small>多次辨識一致 ≠ 保證正確。登記前請比對照片。</small>';
      setCameraStatus(`找到 ${response.candidates.length} 組候選車牌。請核對後帶入表單。`);
    } else {
      result.innerHTML='<strong>本次未找到明確車牌</strong><span>請將車牌靠近鏡頭、增加光線、保持水平，或拍照後框選車牌重試；也可手動輸入。</span>';
      setCameraStatus('尚未辨識成功。可縮小框選範圍、提高亮度後再試。',true);
    }
  } catch(error) {
    setCameraStatus(`辨識失敗：${error.message}`,true);
    $('#ocr-result').textContent=`無法辨識：${error.message}`;
  } finally {
    cameraOcr.textContent='辨識車牌 →';
    cameraOcr.disabled=false;
  }
});
$('#ocr-result').addEventListener('click',event=>{
  const button=event.target.closest('[data-ocr-entry],[data-ocr-exit]');
  if(!button)return;
  const toExit=button.hasAttribute('data-ocr-exit');
  const plate=toExit?button.dataset.ocrExit:button.dataset.ocrEntry;
  const field=$(toExit?'#exit-plate':'#entry-plate');
  field.value=plate;
  // When pasted to an entry/exit field, make the form visible, but do not submit it.
  document.querySelector('[data-tab="overview"]').click();
  field.scrollIntoView({behavior:'smooth',block:'center'});
  field.focus({preventScroll:true});
  toast(`已填入${toExit?'出場':'進場'}候選車牌「${plate}」，請核對後手動確認`);
});
window.addEventListener('pagehide',()=>stopCamera(false));

refresh();
setInterval(() => refresh(true), 12000);
