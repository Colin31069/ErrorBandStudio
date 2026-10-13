# Scientific Behavior Contract — ErrorBandStudio

本文固定 Error Band Studio Python 程式的分析與統計定義，供回歸測試和人工複核使用。更改計算語意必須先審核此契約並更新 golden cases。

## 原始輸入與時間

- 每份 .xlsx 對應一個獨立重複樣本；使用第一張工作表且要求欄位 pF - Plot 0。
- 缺欄、內部缺值、非數值及非有限樣本不應被視為零，也不可默默縮短時間軸。
- 原始第 i 筆數值時間 = i × sample_interval_sec（i 從 0 起算）；預設 0.1 秒。

## Baseline 與事件偵測

- 預設 baseline 為起始 20 秒的平均值，每筆樣本獨立計算。
- 如果指定 baseline 區段侵入施藥搜尋窗，先縮短 baseline 並發出警示。
- 門檻為 baseline 均值 + 3 × max(baseline 母體標準差, 0.0001 pF)；在施藥時窗中取第一個高於門檻的點。
- 若施藥時窗沒有通過門檻的點，使用既有 fallback 搜尋並標示非 window 偵測，不得偽裝成 window hit。
- 反應時間以偵測的 drop_time 對齊到 t=0；最後值取末端 100 筆有效數據的平均值（資料過短則不可宣稱有效 delta）。

## 顯示與統計

- Raw 為原始 pF；Classic Norm = C / baseline × 100；Delta(pF) = C - baseline；Delta(%) = (C - baseline) / baseline × 100。
- Baseline 為零或極近零時不可做百分比正規化；須明確拒絕而非輸出 Infinity/NaN。
- 每組 n 必須反映實際有效且已納入的重複樣本；SD = sample std(ddof=1)，SEM = SD / sqrt(n)。n=1 時 SD/SEM 無法估計，不是零。SD、SEM 均非 95% CI。
- 誤差區域圖僅採所有有效樣本對齊後共有時間區間，不外插或填零；先對各樣本轉換再做平均。

## 排除紀錄與資料安全

- 手動排除名單與原因是持久化科研決策；metadata 不可因讀取錯誤或目前檔案數變化被無聲清空。
- 20% 排除上限與 Dixon 小樣本例外維持原規則；若有效名單與目前樣本數矛盾，暫停該組分析，不得自動重新納入。
- metadata 儲存必須以相同資料夾暫存檔及原子替換進行；失敗不得破壞前一版。
- Golden cases 使用精確的有限值和明確 NaN 斷言；SciPy 推論統計結果不得與描述統計 SD/SEM 混淆。

## 固定數值案例

- baseline=10 pF，t=25.0 s 第一次上升至 12 pF，末端維持 12 pF：drop_time=25.0 s、delta=+2 pF、Norm 從 100% 變為 120%。
- 相同前段基線，t=35 s 才上升至 15 pF：fallback drop_time=35 s、delta=+5 pF，且需要 timing warning。
- 描述統計 [10,20,30]：mean=20、SD=10、SEM=10/sqrt(3)；只有一筆資料時 SD/SEM=NaN。
- 五筆樣本中排除 2.xlsx 屬 20% 上限內；若檔案縮成四筆且 2.xlsx 仍存在，該組不得默默把它重新納入。

對應自動化入口：tests/test_scientific_golden.py；共享 parity 範例位於 shared/parity_cases.json。

## CSV 匯出安全與溯源

- Classic 統計 CSV 的逐樣本欄位包含實際檔名 file_name；報表也列出 root_path、組別及人工排除原因。
- Classic 統計與 Error Band CSV 匯出時，包含 Excel 公式前綴的文字欄位會加上單引號以防止公式執行；數值（包含負數、指數記號）不變。
- CSV 的字串跳脫僅改變檔案的安全呈現，不改變程式內的 sample、metadata 或統計數值。
