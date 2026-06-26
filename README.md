# 统一 BET 分析平台

这是一个面向 BET 比表面积与孔结构数据处理的中文分析工具。它把不同仪器厂商、不同软件版本的等温线数据汇集到同一个界面里，提供多样品对比、BET / Langmuir / t-Plot 拟合、BJH 孔径分布、图形化选区调整和 Excel 导出，并尽量保留各家原版软件的算法细节，方便结果之间相互核对。

## 项目目标

市面上的 BET 分析软件、仪器厂商软件和报告格式并不统一。不同软件的默认区间、拟合规则、厚度方程、物理常数和结果展示方式都存在差异，这会给不同样品之间的横向对比带来困难。

这个项目的目标很简单：

- 把常见 BET 分析软件的核心分析流程逐步集成到一个工具里。
- 让不同来源、不同样品的数据能用统一规则重新分析。
- 让 BET、Langmuir、t-Plot、BJH 等结果可以透明地查看、调整和比较。
- 尽可能复现各家软件的默认算法，同时允许用户明确地进行人工区间调整。

最终希望它成为一个开放、可验证、方便扩展的 BET 统一分析平台。

## 支持的数据来源

平台为每个厂商/软件单独编写解析器，并在分析阶段按来源保留各自的默认规则（区间选取、厚度方程、物理常数、t-Plot 纵轴定义等）。当前已支持：

| 厂商 / 软件 | 已验证型号 / 版本 | 支持导入格式 | 解析与算法状态 |
| --- | --- | --- | --- |
| Micromeritics TriStar II 3020 | TriStar II 3020 原始数据 | `SMP` | 支持等温线、BET、Langmuir、t-Plot、BJH；保留 TriStar II 3020 的默认点数与常数差异。 |
| Micromeritics MicroActive for TriStar II Plus | MicroActive for TriStar II Plus | `SMP`、`XLS`、`XLSX`、`XLSM` | 支持原始 SMP 与官方 Excel 导出；BET 读取官方选点区间，BJH 正在按 MicroActive 标准修正继续逼近。 |
| Micromeritics 3Flex 3500 | 3Flex 3500 / Flex `6.03` 官方报表、MicroActive 可打开的手动点表 SMP 已验证 | `SMP`、`XLS` | 支持 SMP 内部手动等温线点表，并按 Flex `6.03` 官方 XLS 反推路径做自由空间修正与 BJH adsorption/desorption 标准修正；支持正式 Flex XLS 报表，临时导出的 `Entered Data Table` 不作为正式兼容格式。 |
| Micromeritics ASAP 2460 | ASAP 2460 | `SMP`、`XLS`、`XLSX`、`XLSM` | 支持 ASAP 等温线与官方 Excel 导出；BET 默认区间包含 ASAP 2460 特定修正。 |
| Micromeritics ASAP 2020 Plus | ASAP 2020 Plus | `SMP`、`XLS`、`XLSX`、`XLSM` | 支持 ASAP 2020 Plus 原始/导出数据读取与统一分析。 |
| MicrotracBEL BELSORP（BELMaster） | BELMaster DAT / Excel 导出 | `DAT`、`XLS`、`XLSX`、`XLSM` | 支持 BELMaster 等温线导入，并进入统一 BET / Langmuir / t-Plot / BJH 分析流程。 |
| Quantachrome Autosorb iQ | Autosorb iQ / QuadraSorb；QPS、NovaWin `version 11.02` 文本型 Excel 报告已验证 | `QPS`、`XLSX` | 支持 QPS 原始等温线导入；支持 NovaWin 文本型 Excel 报告读取等温线，并在 Broekhoff-De Boer 厚度 + 标准修正下直接采用官方 BJH adsorption/desorption 表。 |
| 贝士德 BSD-660 | BSD-660MC，软件 `V.9.1.15.0 Date 26.04.28` 已验证 | `XLS`、`XLSX`、`XLSM` | 支持官方 Excel 导出；BET、Langmuir、t-Plot 已按 BSD 报表口径复现，BJH 默认读取官方逐点表，孔容递推仍在反推中。 |
| 精微高博 JWGB | `Info / Isotherm / BET Surface Area / Langmuir Surface Area / t-Plot / BJH` 多 sheet 官方 Excel 导出已验证 | `XLSX` | 支持官方 Excel 导入并读取等温线；BET、Langmuir、t-Plot 使用官方导出点号反推默认区间，t-Plot 厚度曲线为 Harkins-Jura，BJH 默认反推为 Halsey + standard + 不平滑，官方 BJH 表保存为校验数据。 |

不同来源在分析时会自动匹配对应的默认算法，例如 TriStar II 3020 沿用其历史阿伏伽德罗常数、BSD / JWGB t-Plot 以吸附量（STP）而非液体体积作纵轴、ASAP 2460 对存储区间下限做特定修正等。这样默认结果会贴近原软件，而统一重算时又能切换到一致规则。

## 当前功能

- 读取上表所列的 SMP / XLS(X/M) / DAT / QPS 文件。
- 通过左右双栏导入窗口批量选择文件，支持已导入文件回显、格式排序、多选移动和待导入顺序调整。
- 多样品导入、显示、隐藏、排序、删除和拖拽调整顺序。
- 样品列表冻结前两列，便于横向滚动时查看样品名称。
- 吸附 / 脱附等温线多样品叠加显示。
- BET 拟合图、Langmuir 拟合图、t-Plot 图和 BJH 孔径分布图多样品叠加显示。
- BET 区间自动选取遵循 Rouquerol 准则，可视化拖拽手动调整 BET、Langmuir、t-Plot 区间。
- 人工调整后的样品结果以蓝色标记，便于区分默认计算和人工调整结果。
- t-Plot 支持多种厚度方程：
  - Kruk-Jaroniec-Sayari
  - Halsey
  - Harkins-Jura
  - Broekhoff-De Boer
  - 碳黑 STSA
  - 参考厚度曲线（Akima 插值）
- t-Plot 总表面积可取自 BET、Langmuir 或手动输入。
- BJH 支持吸附 / 脱附分支同时显示，并复用厚度曲线公式参数界面。
- BET、Langmuir、t-Plot 和 BJH 的重复计算结果会按样品与参数组合缓存，切换样品或调整显示范围时尽量复用已有结果。
- 结果参数、样品条件、实际等温线、目标压力表、报告模块和日志信息查看。
- 选中样品导出为 XLSX。
- 命令行解析 SMP / XLS(X/M) / DAT / QPS 并导出 CSV。

## 性能与缓存

图形界面对多样品叠加显示做了计算缓存，避免在切换样品、拖动拟合区间或调整 BJH 孔径显示范围时反复执行相同分析：

- BET / Langmuir 按“样品 + 拟合压力区间”缓存。
- t-Plot 分别按“压力区间点图”和“厚度拟合区间 + 厚度曲线参数”缓存。
- BJH 按“样品 + 吸附/脱附分支 + 厚度曲线 + 参数 + 修正方式 + 开孔比例 + 是否平滑”缓存。
- 等温线蓝色框或 BJH 孔径范围变化只筛选已有结果，不改变算法参数时不会重新计算分布。

缓存只影响运行速度，不改变计算结果。参数变化会生成新的缓存键，不会用旧参数结果覆盖新结果。为避免长时间拖动或频繁切换参数导致内存无限增长，BET / Langmuir / t-Plot 拟合缓存最多保留 `2048` 条，BJH 分布缓存最多保留 `1024` 条；超过上限后自动丢弃最早的缓存，必要时下次重新计算。

## 结果验证

仓库提供 `validate_against_xls.py`，用于把本工具计算的结果与厂商软件导出的 XLS 报表逐项对照，作为算法正确性的交叉验证手段。建立标准样品数据集、并在不同软件结果之间做可重复比较，是这个项目的长期方向之一。

## 安装依赖

建议使用 Python 3.10 或更新版本。

```
python -m pip install -r requirements.txt
```

完整界面依赖 `PyQt5`、`pyqtgraph`、`numpy`、`openpyxl` 和 `xlrd`（用于读取旧版 `.xls`）。如果只做命令行解析，核心解析逻辑对 GUI 依赖较少。

## 启动图形界面

在项目根目录运行：

```
python app.py
```

或显式启动中文界面：

```
python app.py --ui
```

## 命令行解析

解析单个文件（SMP / XLS / XLSX / XLSM / DAT / QPS）：

```
python app.py path\to\sample.SMP
```

解析一个目录中的受支持文件并导出 CSV：

```
python app.py path\to\data_folder --out-dir path\to\output
```

仅打印摘要，不导出 CSV：

```
python app.py path\to\sample.SMP --no-export
```

可用 `--prefix` 自定义导出文件名前缀。若未指定 `--out-dir`，命令行默认导出到桌面下的 `BET分析导出` 文件夹；没有桌面目录时会退回到用户主目录下的同名文件夹。

图形界面首次打开导入/导出窗口时默认使用桌面路径。用户选择过数据文件夹或导出文件夹后，软件会记住上次使用的目录，下次打开时自动回到该位置。

## 项目结构

```
app.py                              启动入口，支持 GUI 和命令行解析
tristar_bet/models.py               数据模型（统一的 TriStarResult 等）
tristar_bet/smp.py                  Micromeritics SMP 解析与 CSV 导出
tristar_bet/excel_import.py         MicroActive / BSD 等 Excel 报表解析
tristar_bet/belmaster.py            MicrotracBEL BELSORP (BELMaster) DAT 解析
tristar_bet/quantachrome.py         Quantachrome Autosorb iQ QPS 解析
tristar_bet/reference_thickness.py  参考厚度曲线与插值
tristar_bet/analysis.py             BET、Langmuir、t-Plot、BJH 等分析计算
tristar_bet/ui/main_window.py       中文图形界面主窗口
tristar_bet/ui/plots.py             图表绘制
validate_against_xls.py             与 XLS 导出结果对照验证的辅助脚本
```

## 当前状态

项目仍在持续开发中。各厂商解析器与 BET / Langmuir / t-Plot / BJH 的统一分析体验已可用，并以厂商默认规则为基线对齐原软件结果。

后续计划包括：

- 允许用户显式选择用哪一套厂商/软件版本的算法重算（逐样品下拉与全局统一选项），而不仅依赖文件自带的来源标识。
- 接入更多仪器厂商和 BET 软件的数据格式。
- 补充更多孔径分布算法（如 DH）与报告参数。
- 扩充标准样品数据集，完善与各软件结果的交叉验证文档与自动化测试。

## 说明

本项目的初衷不是替代任何仪器厂商软件，而是提供一个开放的统一分析入口，让研究者能够清楚地看到数据、区间、公式和结果之间的关系，并方便地进行不同样品之间的可重复比较。

## 软件更新发布

图形界面顶部提供“软件更新”按钮。软件会先读取 Gitee 上的 `updates/latest.json` 更新清单；如果 Gitee 不可用，再读取 GitHub raw 清单；最后才兜底访问 GitHub Releases API。软件启动后会延迟自动静默检查一次；如果发现新版，会在“软件更新”按钮左侧显示蓝色下载图标，用户点击图标或“软件更新”后即可在软件内下载新版 exe，绿色进度条会显示下载进度；下载完成并通过 SHA256 校验后，软件会自动关闭当前版本、替换原 exe 并启动新版。

发布新版时建议按下面流程操作：

1. 修改 `tristar_bet/version.py` 中的 `__version__`，例如从 `1.0.0` 改为 `1.0.1`。
2. 提交代码并创建同版本 tag，例如 `v1.0.1`。
3. 推送 tag 到 GitHub 后，`.github/workflows/release.yml` 会自动在 Windows 环境运行 `pyinstaller --noconfirm BET.spec`。
4. GitHub Actions 会创建或更新同名 Release，并上传 `BET-DragonScience.exe` 与 `SHA256SUMS.txt`。
5. 将同一份 `BET-DragonScience.exe` 和 `SHA256SUMS.txt` 上传或同步到 Gitee Release。
6. 更新 `updates/latest.json` 中的 `version`、`gitee_download_url`、`github_download_url` 和 `sha256`，并推送到 GitHub 与 Gitee 的 `main` 分支。同一份清单可以同时写 Gitee 和 GitHub 两套链接，软件会根据实际读取到的清单来源选择下载地址。如果 Gitee Release 附件还没上传，先让 `gitee_download_url` 临时指向 GitHub 下载链接，避免用户点到 404。
7. 用户点击“软件更新”后，会优先使用 Gitee 清单里的下载链接在软件内完成下载、校验和重启；如果清单不可用，会自动退回 GitHub。

Gitee Release 附件可以用辅助脚本上传。先在本机设置具有仓库写权限的 Gitee 私人令牌：

```powershell
$env:GITEE_TOKEN = "你的 Gitee 私人令牌"
python scripts/upload_gitee_release.py --tag v1.0.12 --file ".\dist\BET-DragonScience.exe" --file ".\dist\SHA256SUMS.txt"
```

当前更新检查默认使用 GitHub 仓库 `dragonMaLong/unified-bet-analysis` 和 Gitee 仓库 `dragonMalong/unified-bet-analysis`。如果以后迁移仓库，需要同步修改 `tristar_bet/update_checker.py` 中的 `DEFAULT_UPDATE_REPOSITORY`、`DEFAULT_GITEE_MANIFEST_URL` 和 `DEFAULT_GITHUB_MANIFEST_URL`。
