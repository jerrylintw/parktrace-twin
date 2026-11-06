# jerrylintw 專用：GitHub + Render 部署步驟

目標 GitHub 帳號：`jerrylintw`  
建議 repository：`parktrace-twin`  
完成後 GitHub URL：`https://github.com/jerrylintw/parktrace-twin`

## 步驟 1：建立空白儲存庫

1. 登入 `jerrylintw`，開啟 https://github.com/new 。
2. Repository name 輸入 `parktrace-twin`，建議選 **Public**。
3. **不要**勾選「Add a README file」、「Add .gitignore」或「Choose a license」，以免首次 push 發生不必要的歷史衝突。
4. 按 **Create repository**。

> 本檔案提到的 GitHub URL 是預定路徑；只有完成上述步驟、成功 push 後才會存在。

## 步驟 2：從 VS Code 上傳專案

在 VS Code 使用 **File > Open Folder** 開啟解壓縮後的 `ParkTrace-Twin` 目錄，並在 Terminal 執行：

```bash
git init
git add .
git commit -m "Initial ParkTrace Twin midterm project"
git branch -M main
git remote add origin https://github.com/jerrylintw/parktrace-twin.git
git push -u origin main
```

如果 Git 提示 `Author identity unknown`，先設定你自己的 `git config --global user.name "..."` 和 `git config --global user.email "..."` 後重試 `git commit`。如果 `git remote add origin` 顯示已存在 origin，可改用 `git remote set-url origin https://github.com/jerrylintw/parktrace-twin.git`。

**不要把 `parking_local.db`、`.env`、真實車牌照片、GitHub Token 上傳到 GitHub**。專案 `.gitignore` 已排除本機資料庫、環境變數及快取資料。首次 push 可使用 GitHub 登入／憑證管理員。

## 步驟 3：使用 Render Blueprint 建立線上網站

1. 登入 https://dashboard.render.com ，在合適的 workspace 選 **New > Blueprint**。
2. 授權 Render 存取 `jerrylintw/parktrace-twin` repository，選擇 `main` 分支。
3. Render 會讀取根目錄的 `render.yaml`，規劃新加坡區域的 Docker Web Service 和 PostgreSQL 資料庫。
4. 檢查部署方案（檔案設定為 Free，實際是否適用以 Render 當下介面顯示為準），確認後按 **Apply**。
5. 等待部署成功，從 Render 服務資訊中取得 **實際產生的** `https://...onrender.com` URL；測試 `/healthz` 回傳 `{"ok":true}`，再開首頁操作。

不要推測 Render 產生的 URL；以 dashboard 顯示的實際網址為準。`DATABASE_URL` 透過 Blueprint 中 `fromDatabase` 引用 PostgreSQL，不需要把資料庫密碼寫入 GitHub。

## 步驟 4：驗收

- 網站首頁可載入，能模擬車牌進場並分配車位。
- 模擬時間快轉，出場後產生停車費與收據。
- 滿位候補按 FIFO 自動補位。
- 車位觀測故意輸入不同車牌，應出現異常警示與事件紀錄。
- 在 HTTPS 網站測試瀏覽器攝影機；不支援時可用照片上傳或手動輸入車牌。

## 部署限制

本專案是學校展示用公用沙盒，**沒有管理員登入**。網際網路上的訪客可修改或重置模擬資料，不應輸入真實個資、車牌或進行實際收費。照片上傳會在後端記憶體中處理，不會持久保存。免費方案的休眠、資料庫期限、儲存與備份限制請以 Render 服務當下顯示為準。
