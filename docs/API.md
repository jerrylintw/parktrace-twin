# REST API 快速參考

API 與網頁是**同源**的。資料都在伺服器處理，瀏覽器不能自行計費或修改 SQLite/PostgreSQL。

| Method | 路徑 | JSON Body | 成功結果 |
|---|---|---|---|
| GET | `/healthz` | 無 | `{ "ok": true }` |
| GET | `/api/state` | 無 | 全部車位、目前在場、候補、帳單、異常、事件快照、四大資料結構 |
| POST | `/api/entry` | `{ "plate": "ABC-1234", "vehicle_type": "normal" }` | `parked` + spot 或 `queued` |
| POST | `/api/exit` | `{ "plate": "ABC-1234" }` | `fee` + 收據 + 候補補位 |
| POST | `/api/cancel-wait` | `{ "plate": "ABC-1234" }` | 取消候補 |
| POST | `/api/observe` | `{ "spot": "A02", "plate": "ABC-1234" }` | 實際觀測存入；空車牌清除 |
| POST | `/api/advance` | `{ "minutes": 60 }` | 後端虛擬時間快轉（15/30/60/180/1440） |
| POST | `/api/reset` | `{}` | 清空所有模擬資料並重設時鐘 |
| POST | `/api/ocr` | `{ "image": "data:image/jpeg;base64,..." }` | 單張候選、`confidence: low`、品質提示；永遠人工確認 |
| POST | `/api/ocr` | `{ "images": ["data:image/jpeg;base64,...", ...] }` | 1～5 張影格交叉比對、`frame_support`、`confidence`、`quality_tips` |

## 例子

```bash
curl -X POST http://localhost:8000/api/entry \
  -H "Content-Type: application/json" \
  -d '{"plate":"ABC-1234","vehicle_type":"normal"}'

curl -X POST http://localhost:8000/api/advance \
  -H "Content-Type: application/json" \
  -d '{"minutes":60}'

curl -X POST http://localhost:8000/api/exit \
  -H "Content-Type: application/json" \
  -d '{"plate":"ABC-1234"}'
```

## 資料表

- `spots(id, zone, kind, distance)`：車位目錄，示範 18 格。
- `visits(id, plate, vehicle_type, spot_id, entered_at, exited_at, fee)`：停車歷程與費用；`exited_at` NULL 代表仍在場。
- `wait_items(id, plate, vehicle_type, queued_at)`：FIFO 候補順序，以遞增主鍵表示。
- `observations(spot_id, plate, seen_at)`：車位觀測層，每格最新一筆。
- `events(id, action, plate, spot_id, note, created_at, snapshot_json)`：不可逆事件日誌＋回放快照（重置操作例外）。
- `settings(key, value)`：虛擬時鐘快轉分鐘數。

## 設計假設 / 已知限制

- 費率由後端 UTC 時刻加上模擬 offset 計算；網頁只負責呈現台灣時間。
- 每個事件儲存完整網格快照，不是節省儲存空間的正式 Event Sourcing 平台；用於說明與可視化。
- 一般車輛只使用一般車格；電動車優先充電格、無障礙車優先無障礙格，特殊格不足時回落到一般車格。
- FIFO 等候車輛不會開始計費；補位當下才寫入 `visits.entered_at`。
- 當候補隊列不空時，新進車輛先加入候補以避免插隊。若隊首車輛不符合目前空格類型，會等候下一個合適車位。
- 演算法和資料表適合單一展示 Web Service；多實例高併發需進一步加上資料庫鎖、唯一約束及真正交易隔離。

## OCR v1.3 回應結構

- `candidates`：最多 5 組車牌候選，依獨立影格支持數、單張辨識加權票數排序。
- `frame_support`：候選在多少**不同影格**中排第一；單張重複上傳不會重複計票。
- `frames_total`：去除完全重複內容後的影格數；`frames_received` 為實際送進來的張數。
- `votes`：候選在影像處理流程中的累積票數，並非正確率。
- `confidence`：`high`（至少 3 個影格、≥75% 支持且領先第二名至少 2 張）、`medium`（至少 2 影格且明確領先）、其餘為 `low`。**非校準的機率或準確率指標**。
- `quality_tips`：模糊、過曝、偏暗及單張無法交叉確認等提示。
- `review_required`：永遠為 `true`，OCR 不自動新增進出場紀錄。
- 請求大小上限約 10.5MB；每張最多 3MB、最多 800 萬像素，批次總像素上限 1800 萬。
- 無登入的公開展示 API，請勿上傳真實敏感照片或車牌資料。
