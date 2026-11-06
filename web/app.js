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

// Camera + OCR: the user can drag a rectangle over the preview to crop plate pixels.
function closeCamera() {
  $('#camera-modal').classList.add('hidden');
  if (videoStream) { videoStream.getTracks().forEach(track=>track.stop()); videoStream=null; }
  $('#camera-video').srcObject = null;
}
function openCamera() { $('#camera-modal').classList.remove('hidden'); }
$('#open-camera').addEventListener('click', openCamera);
$('#close-camera').addEventListener('click', closeCamera);
document.querySelector('[data-close-camera]').addEventListener('click', closeCamera);
window.addEventListener('keydown', e=> { if (e.key==='Escape') closeCamera(); });
$('#start-camera').addEventListener('click', async () => {
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('此瀏覽器或 HTTP 環境不支援攝影機，請改用上傳圖片');
    if (videoStream) videoStream.getTracks().forEach(track=>track.stop());
    videoStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
    $('#camera-video').srcObject = videoStream;
    toast('鏡頭已開啟；請將車牌放在畫面中央');
  } catch (error) { toast('無法開啟鏡頭：'+error.message, true); }
});
function loadPhoto(dataUrl) {
  const img = new Image();
  img.onload = () => { sourcePhoto=img;selectedRect=null;drawPhoto();$('#ocr-result').textContent='可拖曳框選車牌，然後按「辨識此照片」。'; };
  img.onerror = () => toast('圖片無法載入', true);
  img.src = dataUrl;
}
$('#snap-photo').addEventListener('click', () => {
  const video=$('#camera-video');
  if (!videoStream || !video.videoWidth) {toast('請先開啟鏡頭，或選擇圖片',true);return;}
  const canvas=document.createElement('canvas');
  canvas.width=Math.min(1280, video.videoWidth);
  canvas.height=Math.round(video.videoHeight*canvas.width/video.videoWidth);
  canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
  loadPhoto(canvas.toDataURL('image/jpeg',.86));
});
$('#upload-photo').addEventListener('change', event => {
  const file=event.target.files?.[0];
  if (!file) return;
  if (!file.type.startsWith('image/') || file.size>3000000) {toast('請選擇小於 3MB 的圖片',true);return;}
  const reader=new FileReader();reader.onload=()=>loadPhoto(reader.result);reader.readAsDataURL(file);
});
function drawPhoto() {
  if (!sourcePhoto) return;
  const canvas=$('#camera-canvas');
  const width=Math.min(640,sourcePhoto.naturalWidth);
  const height=Math.max(1,Math.round(sourcePhoto.naturalHeight*width/sourcePhoto.naturalWidth));
  canvas.width=width;canvas.height=height;
  const ctx=canvas.getContext('2d');ctx.drawImage(sourcePhoto,0,0,width,height);
  if (selectedRect && selectedRect.w>5 && selectedRect.h>5) {
    ctx.fillStyle='#49e1bb20';ctx.strokeStyle='#72e5c4';ctx.lineWidth=3;
    ctx.fillRect(selectedRect.x,selectedRect.y,selectedRect.w,selectedRect.h);
    ctx.strokeRect(selectedRect.x,selectedRect.y,selectedRect.w,selectedRect.h);
  }
}
function pointerCoord(event) {
  const c=$('#camera-canvas'), r=c.getBoundingClientRect();
  return {x:(event.clientX-r.left)*c.width/r.width,y:(event.clientY-r.top)*c.height/r.height};
}
$('#camera-canvas').addEventListener('pointerdown',e => {if(!sourcePhoto)return;dragStart=pointerCoord(e);$('#camera-canvas').setPointerCapture(e.pointerId);});
$('#camera-canvas').addEventListener('pointermove',e=>{
  if (!dragStart) return;
  const pt=pointerCoord(e); selectedRect={x:Math.max(0,Math.min(dragStart.x,pt.x)),y:Math.max(0,Math.min(dragStart.y,pt.y)),w:Math.abs(pt.x-dragStart.x),h:Math.abs(pt.y-dragStart.y)};
  drawPhoto();
});
$('#camera-canvas').addEventListener('pointerup',()=>{dragStart=null;});
function getPhotoData() {
  const src=$('#camera-canvas');
  const ratioX=sourcePhoto.naturalWidth/src.width,ratioY=sourcePhoto.naturalHeight/src.height;
  const rect=(selectedRect && selectedRect.w>15 && selectedRect.h>12)?selectedRect:{x:0,y:0,w:src.width,h:src.height};
  const cropped=document.createElement('canvas');
  const factor=Math.min(1,1600/(rect.w*ratioX));
  cropped.width=Math.max(1,Math.round(rect.w*ratioX*factor));cropped.height=Math.max(1,Math.round(rect.h*ratioY*factor));
  cropped.getContext('2d').drawImage(sourcePhoto,rect.x*ratioX,rect.y*ratioY,rect.w*ratioX,rect.h*ratioY,0,0,cropped.width,cropped.height);
  return cropped.toDataURL('image/jpeg',.84);
}
$('#run-ocr').addEventListener('click', async () => {
  if (!sourcePhoto) {toast('請先拍照或上傳圖片',true);return;}
  const button=$('#run-ocr');button.disabled=true;button.textContent='OCR 辨識中...';
  try {
    const result=await post('/api/ocr',{image:getPhotoData()});
    $('#ocr-result').innerHTML=result.candidates.length
      ? '<strong>候選車牌（人工核對後可用於進場或出場）</strong><div>'+result.candidates.map(p=>`<div>${esc(p)} <button type="button" data-ocr-entry="${esc(p)}">帶入進場</button><button type="button" data-ocr-exit="${esc(p)}">帶入出場</button></div>`).join('')+'</div><small>辨識可能有錯，請人工核對原始圖片再確認。</small>'
      : '這次沒有辨識到標準車牌；請將裁切框縮到車牌，或手動輸入。OCR 在模糊、背光、傾斜場景可能失敗。';
  } catch (error) { $('#ocr-result').textContent = '辨識失敗：'+error.message; }
  finally {button.disabled=false;button.textContent='辨識此照片';}
});
$('#ocr-result').addEventListener('click',e=>{
  const button=e.target.closest('[data-ocr-entry],[data-ocr-exit]');
  if(!button)return;
  const toExit = button.hasAttribute('data-ocr-exit');
  const plate = toExit ? button.dataset.ocrExit : button.dataset.ocrEntry;
  $(toExit ? '#exit-plate' : '#entry-plate').value = plate;
  closeCamera();
  toast(`已填入${toExit?'出場':'進場'}候選車牌，請核對無誤後再確認`);
});

refresh();
setInterval(() => refresh(true), 12000);
