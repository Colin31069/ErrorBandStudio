# Error Band Studio · 誤差區域圖

每組一條平均曲線，搭配半透明 SD／SEM 誤差區域。Excel 原始檔不會被修改。

## 開啟程式

- **macOS 原生包**：解壓縮後開啟 `ErrorBandStudio.app`。不需要安裝 Python；包名標示 `arm64` 或 `x86_64`，需符合電腦架構。本機製作的包未經 Apple 公證；如系統阻擋，依 macOS 的「隱私權與安全性」提示允許開啟。
- **Windows 原生包**：解壓縮完整資料夾，再開啟 `ErrorBandStudio.exe`。`_internal` 必須與 exe 放在一起。學弟妹不需要安裝 Python，也不需要網路。Windows 包必須在 Windows 建置、測試；macOS 包無法當成 exe 使用。
- **小體積原始碼包**：Windows 雙擊 `run_windows.bat`；macOS 可在 Terminal 執行 `zsh run_macos.command`。首次需要 Python（Windows 3.11+；macOS 3.12+ 且含 Tk）與網路，自動安裝三個直接依賴。之後使用同一啟動檔，通常不再需要網路。這個包不包含 Python runtime。

啟動腳本會先檢查 Python 版本與 Tk。macOS 有多個 Python 時，會略過不支援 Tk 的版本；已有虛擬環境但缺少 Tk 時，會提示修復環境，避免反覆安裝無法補上 Tk 的 Python 套件。

macOS 開發與封裝已使用 Python 3.13 + 對應 Tk 驗證，建議維護者使用此版本系列。原生分享包已包含自己的 runtime，學弟妹電腦上的其他 Python 不會影響它。

## 三步製圖

1. 選擇資料根目錄，例如包內的 `ExampleData`。
2. 勾選組別；雙擊組別名稱，可修改圖例文字。
3. 按「製作誤差區域圖」，再匯出 PNG、PDF、SVG 或 CSV。

資料格式沿用原專案：

```text
ROOT/
  2 uL/
    1.xlsx
    2.xlsx
  3 uL/
    1.xlsx
    2.xlsx
```

每個 `.xlsx` 的第一個工作表必須有 `pF - Plot 0` 欄位；一個檔案視為一筆獨立重複樣本。中間缺值、非數字、公式未儲存計算結果等檔案會被略過並列入注意事項，不會刪掉該列而改變時間軸。Excel 的 `~$` 暫存檔會被忽略。

## 單位、誤差與對齊

- 預設 **ΔC（pF）**：每筆樣本先扣除自己的 baseline 平均值，再逐時間點平均。
- **相對變化（%）**：`(C − baseline) / baseline × 100`，baseline 為 0 時無法計算。
- **原始電容（pF）**：保留原始電容，再計算組平均。
- **SD**：樣本標準差，使用 `ddof=1`，顯示重複樣本的分散程度。
- **SEM**：`SD / √n`，顯示平均值的標準誤。兩者都是平均值加減誤差，不是 95% 信賴區間。
- 每筆樣本先以原有的反應偵測方法對齊至 `t=0`，各組只分析所有納入樣本共有的時間範圍，讓同組曲線全程維持相同 n；不補零，也不延伸較短檔案。組與組之間的時間範圍可能不同。
- n=1 時只畫曲線，不畫誤差區域；CSV 中 SD、SEM 與範圍欄位留空。

「進階」可調整取樣間隔、baseline 長度、施加時間與搜尋範圍。預設為 **0.1 秒／20 秒／25 ± 5 秒**；Excel 若使用不同取樣間隔，請先調整。這一版從取樣間隔建立時間軸，不讀取 Excel 的時間欄位。

修改資料、顯示單位或分析參數後，需重新製圖。SD／SEM、格線、標題與圖例可直接更新。匯出圖片使用目前畫面，PNG 為 300 dpi；CSV 包含 mean、SD、SEM、上下界、實際 n、來源檔名、圖例、設定及注意事項。匯出時會把類似試算表公式的組名、檔名和備註轉為純文字，避免開啟 CSV 時觸發公式；科研數值不變。

如果資料夾已有 `.skin_analysis_metadata.json`，程式會遵循原本有效的排除規則並回報排除項目，無需編輯原始資料。若 metadata 損壞，會暫停納入該組並提示修正，避免錯誤重新納入已排除樣本。這個精簡介面只讀取 metadata。

`ExampleData` 是固定亂數產生的 **合成示範資料**，不能當作實驗結果。

## 製作分享包（維護者）

先安裝 `requirements-error-band.txt` 與 `pyinstaller`，再執行：

```bash
python build_portable.py
```

Windows 可直接雙擊 `build_windows.bat`。原生 ZIP 在 `dist/`。打包只保留 TkAgg 繪圖後端，排除 Pandas、SciPy、舊版統計與 Qt/Jupyter；PyInstaller 僅在打包時使用。

```bash
python build_portable.py --source-only
```

可產出更小的 `dist/ErrorBandStudio-source.zip`，供已有 Python 的使用者使用。Windows exe 不能在 macOS 建置或完成原生驗證；分享前，請在 Windows 解壓完整包，使用示範資料確認載入、SD／SEM、圖片及 CSV 匯出。

維護者應先在**完整原始碼 Repository** 執行自動化測試，確認分享 ZIP 包含每個 Python 執行模組及所有合成樣本。分享 ZIP 不包含開發用 `tests/` 與測試依賴：

```bash
python -m pip install -r requirements-tests.txt
python -m unittest discover -s tests -q
```

其中 `test_portable_source.py` 驗證 ZIP 清單、檔案完整性、合成 Excel 欄名與精簡依賴；這不等於原生 Windows/macOS GUI 的人工操作驗證。
