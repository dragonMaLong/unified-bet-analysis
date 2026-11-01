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
| Micromeritics ASAP 2020 | ASAP 2020 `V4.04` | `SMP` | 支持旧版 ASAP 2020 SMP 的等温线、样品信息和测量自由空间读取；BET 使用文件保存的报告区间并兼容旧版两点拟合。 |
| Micromeritics ASAP 2020 Plus | ASAP 2020 Plus | `SMP`、`XLS`、`XLSX`、`XLSM` | 支持 ASAP 2020 Plus 原始/导出数据读取与统一分析。 |
| MicrotracBEL BELSORP（BELMaster） | BELMaster DAT / Excel 导出 | `DAT`、`XLS`、`XLSX`、`XLSM` | 支持 BELMaster 等温线导入，并进入统一 BET / Langmuir / t-Plot / BJH 分析流程。 |
| Quantachrome Autosorb iQ | Autosorb iQ / QuadraSorb；QPS、NovaWin `version 11.02` 文本型 Excel 报告已验证 | `QPS`、`XLSX` | 支持 QPS 原始等温线导入；支持 NovaWin 文本型 Excel 报告读取等温线，并在 Broekhoff-De Boer 厚度 + 标准修正下直接采用官方 BJH adsorption/desorption 表。 |
| 贝士德 BSD-660 | BSD-660MC，软件 `V.9.1.15.0 Date 26.04.28` 已验证 | `XLS`、`XLSX`、`XLSM` | 支持官方 Excel 导出；BET、Langmuir、t-Plot 已按 BSD 报表口径复现，BJH 默认读取官方逐点表，孔容递推仍在反推中。 |
| 精微高博 | APAS1000_ZQ / Matrix1000 文本 RAW；`Info / Isotherm / BET Surface Area / Langmuir Surface Area / t-Plot / BJH` 多 sheet 官方 Excel 导出已验证 | `RAW`、`XLSX` | RAW 读取吸脱附等温线、样品质量、气体、日期和分析时长；`MAP` 点作为原厂多点 BET 选点，少于 3 点时明确报告无有效多点结果；另按名义压力计算单点 BET。官方 Excel 导入保留原厂点号、算法参数与校验表。 |

不同来源在分析时会自动匹配对应的默认算法，例如 TriStar II 3020 沿用其历史阿伏伽德罗常数、BSD / JWGB t-Plot 以吸附量（STP）而非液体体积作纵轴、ASAP 2460 对存储区间下限做特定修正等。这样默认结果会贴近原软件，而统一重算时又能切换到一致规则。

另支持软件名称为 `TriStar II Plus Version 3.02` 的 SMP：读取等温线点表、逐文件自由空间修正和测量条件，并将点表中的分钟换算为秒。BET、Langmuir 使用文件保存的区间；t-Plot 读取保存的 Harkins–Jura 厚度区间，允许两点拟合并保留负外表面积。三个参考样本的 BET、t-Plot 已与原厂摘要显示值核对；吸附量仍采用经验自由空间公式重建，Langmuir 存在小量尾差，完整等温线表及 BJH 逐点结果仍待核对。样品条件中显示定量校验状态。

## 当前功能

- 读取上表所列的 SMP / RAW / XLS(X/M) / DAT / QPS 文件。
- 通过左右双栏导入窗口批量选择文件，顶部保留“选择文件夹”和“刷新”两个按钮。Windows 下使用系统原生文件浏览窗口，保留熟悉的地址栏、侧边栏和文件列表；详细信息视图在“名称”后显示“类型”，保留文件夹已有的 Mic Description、Mic States 等其他列及列宽。窗口提供“添加”“选择文件夹”“取消”：可用 Ctrl / Shift 多选文件并点击“添加”直接加入右栏，重复文件自动去重；也可只选择文件夹，返回后在左栏按文件名、格式、修改时间、大小排序并筛选，再挑选待导入文件。支持已导入文件回显、多选移动和待导入顺序调整，确认列表后点击“开始导入”。
- 多样品导入、显示、隐藏、排序、删除和拖拽调整顺序。
- 样品列表冻结前两列，便于横向滚动时查看样品名称。
- 吸附 / 脱附等温线多样品叠加显示。
- 等温线保留原始吸附量中的零值和负值，并正常显示点及连线；绘图选点与计算有效点筛选分离，不改变 BET 等分析的有效点规则。
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
- 合并后的结果参数/样品条件，以及实际等温线、目标压力表查看。
- BET、Langmuir、t-Plot 多样品结果表与样品栏同步排序和高亮，计算状态标记、误差独立列和按需显示的单点 BET 数据。
- BET、Langmuir、t-Plot 结果表支持行列切换，三表共用并记忆布局；未保存布局时默认文件名在顶部，左侧冻结参数。可通过左上角表头按钮切换，单击参数名排序，拖动仍可选取单元格复制。
- t-Plot 的厚度方程参数在样品作为列时按参数在同一单元格内分行，行高自动适配；文件名在左侧的样品行布局保持单行显示，复制到 Excel 时保留对应的单元格内换行。
- 下方七个详情标签不显示重复的表格悬浮提示，仅黄色警告图标悬停时显示原因；点击表格外部或空白处清除复制选区，右键复制保留选区。表头单击仅排序、不整列选中；文件名联动仅加粗，蓝色底色只用于实际选中的单元格。缺失值“—”居中显示，结果参数标签维持原有对齐。
- “选区孔容量”表并列显示每个样品的 BJH、DH、HK、DFT 绿色选区孔容，支持排序联动与拖选复制；BJH、DH 默认范围相同，调整和恢复默认互不影响。样品栏原有选区孔容列保留。
- 结果表、实际等温线与目标压力表支持拖选单元格、右键复制与 Ctrl+C，复制时带上所选列的表头，便于粘贴到 Excel。实际等温线的累计测试时间采用“分:秒”标注；两张压力点表的首列均为“测试点”，点号居中。
- 各分析标签独立保存等温线蓝色选区；轻量 Akima 曲线显示，绿色选框孔容连续更新。
- “选区孔容量”列头第二行实时显示对应绿色选框范围（nm，两位小数）；绿色孔容选框和等温线蓝色选框沿用压汞的边界数字样式，移动时显示，点击可编辑，回车/失焦确认、Esc 取消，停止操作约 1.8 秒后隐藏。
- 选中样品导出为 XLSX。
- 命令行解析 SMP / RAW / XLS(X/M) / DAT / QPS 并导出 CSV。

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

解析单个文件（SMP / RAW / XLS / XLSX / XLSM / DAT / QPS）：

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
tristar_bet/jwgb_raw.py             精微高博 APAS 文本 RAW 解析
tristar_bet/jwgb_native.py          精微高博 JWGBT XML 导入（BK200C 受限配置的重建吸附量）
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

## 使用统计

1.3.0 在已配置有效接收地址的新安装中默认开启使用统计，后台上报软件版本、随机安装标识、系统与显示信息、导入/导出结果及耗时、分析页面访问和标准错误码。实验内容、计算数值、样品名、文件名、路径和原始错误文本不进入统计。此前保存的关闭设置继续保留；配置损坏、存储不可写或另一个窗口占用时保持关闭。

首次启动不单独弹出统计提示。“隐私设置”按钮在本版暂时隐藏，设置处理逻辑仍保留。需要禁用时，可在 EXE 旁创建 `telemetry_config.json`，内容为 `{"endpoint":""}`，然后重新启动软件；该配置优先于 EXE 内置地址。已发出的请求无法撤回。

服务器提供受密码保护的统计页面（当前公网地址为 `/bet-stats/`）。已接收事件和连接 IP 长期保存，不再按天自动删除；页面可切换近 30 天、90 天、半年（180 天）、1 年（365 天），并查看全部历史 IP。页面时间及每日分组使用北京时间，数据库时间仍保存为 UTC。活跃量按随机安装标识估算，不能当作真实用户人数；分析功能指标是页面展示/切换，不是后台计算次数。当前错误统计覆盖导入和 Excel 导出。数据库有容量上限，后台显示用量和接近上限提示，长期使用需要定期检查磁盘与备份。

无接收地址时不会收集或发送统计。本版发布包内置已验证的 HTTPS 接口地址。自行打包时，先复制 `telemetry_config.example.json` 为 `telemetry_config.json`，再运行 `pyinstaller --noconfirm BET.spec`；Windows 发布工作流也执行该步骤。可用 EXE 旁同名文件或 `BET_TELEMETRY_URL` 环境变量覆盖。管理密码只配置在服务器，不能写入客户端文件。更换接收地址后不会沿用原地址的开启偏好。

软件端仅使用 Python 标准库，无新增桌面依赖。离线记录最多保留 500 条、7 天；后台限时发送并重试，不阻塞分析和退出。多次启动/升级后的离线记录保留原版本与会话信息。同一用户同时打开多个窗口时，由一个窗口管理统计；其他窗口不记录。关闭管理窗口后重新启动软件可接管，避免偏好和队列被互相覆盖。

服务器独立依赖、SSH/宝塔部署、HTTPS、日志保留和验证步骤见 [统计服务部署说明](telemetry_server/DEPLOY.md)。

DragonScience 当前服务器的后台地址、运行环境和维护方法见 [当前部署说明](telemetry_server/LIVE_DEPLOYMENT.md)。

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

### JWGBT 初步支持

1.3.1 的主界面使用常规图表标题和数值，样品栏 BET 悬停显示“区间计算完成”，结果参数不再单列“导入状态”。这些是显示调整；内部验证状态、计算异常诊断及导出来源记录仍保留，未将候选重建升级为已验证算法。

支持版本 1、PressUnits=0 的精微高博 XML 文件；已用 BK200C 样本检查样品信息、压力、P/P₀、时间及日志。起始参考记录保存在原始记录中，不算额外测量点。支持公开 Sync440A 样本的 `1_7` 等补气编号、`Balance` 标记和浴温字段的小数逗号；全部补气记录保留，仅平衡记录进入等温线点表。对于已覆盖的 BK200C / 00.01.05.00 / 手动 Q 或完整氦气自动 Q 校准 / N₂ 77.35 K 配置，默认提供明确标记为“重建”的候选吸附量和 BET；来源及未核验说明随 CSV、JSON、Excel 导出保留。其他配置或缺失关键参数时，吸附量保留为空。负重建值不截断，BET 选区含非正重建吸附量时不拟合。BK200C 的完整定量算法仍需同次测试的原厂导出逐点校验。现有 RAW、Excel 定量导入不受此限制。

逆向研究工具：`python -m scripts.jwgb_reconstruction_probe "样本.jwgbt" --output "candidate_comparison.json"`。该工具基于原厂公开容量法原理及旧版手册的 Q 差值校准式，显式假设冷自由空间项为 `Q × Vd`，比较四种温度处理方式，并输出 37 点样本的逐项气量计算（实际点数随输入变化）、候选 BET、源文件 SHA-256 和未验证假设。它只生成标有 `unvalidated_research_only` 的研究报告，不启用软件中的定量导入。高拟合优度或平滑曲线不能代替同次测试的原厂点表验证。

2026-10-30 补充教程线索：`BET数据处理(1).pdf` 第 4–5 页展示 PAS 可保存 `.jwgbp`、导出 `.xlsx` / `.raw`；`孔径分析(1).pdf` 第 1 页展示 `PAS.exe` 0.5.9.0 和桌面 `jwgb.rar`。这些是截图中的程序，不是已取得的程序文件；尚未证实该版本直接读取 `.jwgbt` 的路径。后者第 5–6 页显示 NovaWin 的半径/直径、Å/nm 设置，第 7 页未展示 DFT 数值表或核模型，不能据此复现原厂孔径分布。

新增可复核证据：Stepping 等人 2024 年论文 [DOI:10.1039/D4RA05672H](https://doi.org/10.1039/D4RA05672H) 的[公开原始数据](https://cloud.uni-jena.de/s/8stWzTWfXeYY37d)中有 5 个 Micro200B / PAS 10.06.00.72 项目。已逐字节恢复其 `.jwgbp` 编码：编码字节加上字节位置模 48 得到十六进制字符，再还原为 UTF-8 XML。5 个文件均完整解析且反向编码与原文件逐字节一致。`tristar_bet/jwgb_project.py` 实现严格解码，尚未将这种布局加入应用的定量导入。

`python -m scripts.jwgb_project_probe "项目.jwgbp" --output "project_audit.json"` 可核验项目内的氦气 Q 校准。按 `Q = 2.69578 × (Pd/(PdT+273.15) − Pcd/(PcdT+273.15)) / (Pcd−Pcd前点)` 回算，5 个项目均落在保存 Q 值的小数点后 6 位舍入区间；把两次压力统一使用充气前、平衡后或平均温度则各有 1 个项目不符。校准原始数字、来源和完整文件 SHA-256 保存在 `tests/fixtures/jwgb_public_q_calibration.json`。这支持该版本的 Q 参数及分时温度处理，不能代替 BK200C 吸附量逐点验证。项目中的 `Data/BaseInfo` 还存在与 2024 年测量记录不一致的 2018 年时间；核验报告同时保留这些字段，不将其静默当作测试时间。

独立检验尚未通过：将同一简化公式用于上述公开项目的氮气吸附记录，Pan fiber 的文件内 BET 区间产生非正的单层容量，另 4 个项目的选定区间含负候选吸附量。因此工具分别记录失败状态，不删去负值来生成貌似正常的结果。数值反例见 `tests/fixtures/jwgb_public_uptake_cases.json`。[论文补充材料](https://www.rsc.org/suppdata/d4/ra/d4ra05672h/d4ra05672h1.pdf)第 1 页虽有 4 组 BET 汇总值，但没有原厂逐点吸附量，且文件名与表内样品的配对仅能推测；它们不能直接作为 BK200C 的验收基准。此差异目前既不能证明原始实验有误，也不能确定缺少哪项补偿，定量重建仍未验证。

2026-10-31 同次测试对照取得进展：[AIF 开发组公开样本](https://github.com/AIF-development-team/adsorption-file-parser/tree/3a9a3248cd2b7a7965a53c61830221a832d2ce15/tests/data/3p)含 `CBV712_N2_77K.jwgbt`、Excel 和对应 JSON。仓库说明数据由 3P Instruments 的 Sebastian Ehrling 提供。样品、质量、起止时间以及全部 50 个导出点的压力、相对压力、时间、分支和编号相符；其中脱附起点重复吸附终点，应用导入时不重复计数。研究工具 `python -m scripts.jwgb_paired_probe "样本.jwgbt" "导出.json" --output "paired_audit.json"` 可复算该布局。

此 Sync440A 对照支持以下气量规则：从原始氦气校准重算未舍入的 Q，使用精确 `273.15/101.325`，逐次累计全部 74 次补气/抽气的 `ΔV = Vd × [(273.15/101.325) × (Pd/(PdT+273.15) − Pcd/(PcdT+273.15)) − Q × (Pcd−前一Pcd)] × (1+NonIdealityFactor×Pcd)`，再除以样品质量。31 个吸附点全部落在导出 5 位小数的舍入区间，最大误差约 `4.32×10⁻⁶ cm³(STP)/g`。未经闭合处理的脱附前 6 点（含重复起点）也吻合，后 13 点偏差最大约 `2.060 cm³(STP)/g`；报告保留这些原始计算值和误差。

对上述偏差的进一步核验得到一个完整复现该配对导出的闭合候选规则：以相对压力对吸附支作线性插值，计算脱附末点与吸附插值值的差 `gap`；对于 `P/P₀ < 0.8` 的脱附点，按平衡点序号添加 `gap × i/(N−1)`，再取该值与吸附插值值的较大者。起点序号为 0，补气中间记录不另占序号。所有修正量均由原始数据计算，不读取导出吸附量作为输入。此时全部 50 点落入导出舍入区间，最大误差约 `4.83×10⁻⁶ cm³(STP)/g`。报告的 `closure_hypothesis` 独立保留修正量、插值值和额外抬升标记；额外抬升发生于脱附 15–18 点，19 点由闭合量自然对齐。

边界尚未唯一确定：该样本只能把单阈值规则的阈值约束在 `(0.79485145, 0.84035697]`，0.8 是候选值，0.82 会产生完全相同的结果；不能据此宣称恢复了原厂阈值或所有设置。工具拒绝未覆盖的负闭合差和区间外插值。证据、原始数值、导出数值和源文件哈希见 `tests/fixtures/jwgb_sync440_paired.json`，来源许可随附。这是发现样本上的完整复现，尚非独立盲测；未推广为 BK200C、Micro200B 或所有气体的通用定量导入。

在配对工具命令中追加 `--bet-point-ids 1 2 3 4 5 6`，可按 Excel 的 BET 点表显式选点复算：`Vm=184.6748861021 cm³/g`、`C=2005.00416957`，均吻合原报告 5 位小数。使用候选历史常数组合 `N_A=6.02214129×10²³`、摩尔体积 `22413.83 cm³/mol` 和氮气截面积 `0.162 nm²` 得到 `803.81798918 m²/g`，也吻合报告；单个报告只能约束组合换算因子，不能唯一识别两个常数。选点来自独立导出的 BET 点表，不声称已恢复原 XML 舍入区间的边界选择机制；应用中的统一分析常数保持原有行为。

BK200C 研究工具也会在文件提供非理想因子时生成独立的 `sync440_transfer` 段，采用文件内手动 Q，明确标记跨型号迁移未验证。原用户样本 `AutoTestQ=0` 且 `Data/Q` 为空，无法从本次氦气记录复核手动校准。普通导入现已在上述受限配置下显示未核验的重建值；研究闭合规则仍未用于普通导入。`load_jwgb_native(..., reconstruct_uptake=False)` 可只读压力记录，研究脚本用此入口保持对照独立。

进一步独立核验：`python -m scripts.jwgb_export_bet_audit "1.xlsx" "2.xlsx" "3.xlsx" --output "export_bet_audit.json"` 按导出的 BET 点编号从等温线表取高精度压力和吸附量。用户提供的 `09_JWGB` 三个 Excel 分别复算为 `824.287813138`、`1042.893568063`、`974.796894825 m²/g`，均吻合报告三位小数。固定此前提出的历史常数组合后再检验这三份独立导出，未逐文件拟合常数。直接使用 BET 点表中五位小数的压力，或替换为应用的通用常数组合，都会失去这三份报告的末位吻合。选点、原报告数值和文件哈希见 `tests/fixtures/jwgb_local_export_bet.json`。这只验证“导出吸附量到 BET”，不验证 BK200C 原始压力重建；一个换算因子也不能唯一确定其内部各常数。

增加 [IUPAC 2015 第 5.2.2 节](https://doi.org/10.1515/pac-2014-1117)的选区一致性诊断。原 BK200C 文件按保存的 `0.08–0.30` 区间选择吸附点 2–5，应用常数组合得到候选 `118.022742153 m²/g`，`C=2.243588614`，`Vm=27.115595805 cm³/g`；但选点气量只有 `4.972416998–16.390629003 cm³/g`，不包含拟合单层容量，模型单层压力约 `0.40034248` 也在选区外。因此应用保留候选数值并显示“单层容量超出选区，需核对重建和选点”。不能据此宣称原实验有误，也不能只修改 Q 或选区使曲线看起来合理；同次原厂对照仍然缺失。诊断通过本身同样不代表真实比表面积已被验证。

气量拟合的可辨识性更正：`jwgb_balance` 的 `aP+bP²` 模型会完全吸收吸附量中的线性及二次项。即使吸附、脱附残差都接近零，也不能认定没有吸附，不能把拟合的表观 Q 替换为真实校准 Q。现已删除“未检出可测吸附”和“假等温线”的过强结论；`tests/test_jwgb_balance.py` 用已知非零、可逆吸附构造反例。`jwgb_free_space_probe` 的注入结果只表示指定形状在经验阈值下的残差变化，不是实际误差范围或仪器检出限。

补充数据检索：[He 等人 2025 年论文](https://doi.org/10.1038/s41467-025-60191-9)使用 JW-BK200C，公开的 Source Data 工作簿已检查。它提供计算后的相对压力和吸附量曲线，没有原始逐次压力、温度、Q 校准记录或嵌入的原生文件，因此不能用于验证本项目的压力重建。下载源、文件哈希和检查结论保存在本地研究目录 `tmp/jwgb_research/nature_60191/provenance.json`。


新增两份 BK200C/00.01.05.00 自动 Q 文件的诊断：两份氦气校准分别复算出 `0.010520399711102841`、`0.009975239798677153`，均吻合文件存储的六位小数 Q。但这仅验证校准关系；将现有候选吸附量公式用于两份文件，在保存的 `0.08–0.30` 区间内均得到无效单层容量，改用舍入 Q 也无法消除失败。自动 Q 候选气量计算现已接入普通导入：校准记录必须完整、时间属于本次测试且复算 Q 与文件六位小数一致，支持吸附前或吸附后校准。计算使用未舍入 Q；无效校准不退而使用存储 Q 凑数。两份文件在正式路径中仍返回“单层容量无效”，没有有效 BET 数字；这不构成定量算法已恢复的证明。可用 `python -m scripts.jwgb_bk200c_auto_q_probe "3.jwgbt" "4.jwgbt" --output "auto_q_audit.json"` 保留两种 Q 精度控制与失败结果；不调 Q、不删除负点、不更换区间来生成面积。

#### BK200C 同次原厂导出对照（2026-10-31，Claude 接手）

1 号原始文件与另一课题组导出的 `1(1).xlsx`（SHA256 `fa61ad59…c643`）逐点配对：保留文件 Q=0.008500、关闭逐次补气的非理想乘子后，21 个吸附点全部吻合导出 5 位小数（开启乘子时 0/21）。脱附按下式闭合后，17 行脱附（含峰值参考）也全部吻合，合计 38/38：

- `gap = 吸附支线性插值(脱附末点 P/P₀) − 原始脱附末点量`；
- 脱附序号 i（峰值参考为 0）在 `P/P₀ < 0.8` 时加 `gap × i/(N−1)`；
- **所有**脱附点再取该值与同压力吸附插值值的较大者（不再只限 `P/P₀ < 0.8`）。

同一规则对 Sync440A 配对仍为 50/50。说明：原 Sync 规则直接用于 BK 时只得 15/17（`P/P₀ ≥ 0.8` 的两点未被抬到吸附支），"全压力取下限"这一改动是在看到 BK 数据后确定的，因此两份都是发现样本，不是盲测；BK 中每个脱附点都取到吸附下限，平移项与阈值只受 Sync 约束。

生产导入（仅 BK200C/00.01.05.00 受限配置）现已：不施加 `NonIdealityFactor` 乘子（数值仍保留为来源，新增 `jwgb_reconstruction_nonideality_applied=False`）；应用上述脱附闭合（`jwgb_reconstruction_closure` 记录 gap、抬升/下限点数及拒绝原因；负 gap 或区间外插值时不修改并注明原因）。1 号多点 BET 随之由 118.022742 变为约 115.952 m²/g；这是本软件复算，导出 Info 页 BET 为字面 0，不是原厂报告值。3/4 号仍为单层容量无效，单点值改为 0.826134 / 2.324082 m²/g。自动 Q 文件（3–20 号）尚无原厂对照，`jwgb_reconstruction_validation` 分别标为 `bk200c_manual_q_one_same_run_export_match` 与 `bk200c_auto_q_no_same_run_reference`，`quantity_status` 仍为 `reconstructed_unvalidated`。Sync440A 文件没有 `IsRealGas` 字段且需开启乘子才匹配，BK 文件均为 `IsRealGas=0`；"该标志决定乘子"只是与两份配对一致的假设，未据此改动其他型号。

本机端到端复核：`python -m scripts.jwgb_bk200c_vendor_pair_check "1 [2_S1] 2026-10-12 110438.jwgbt" "1(1).xlsx" --output sample1_pair_check.json`（用实际完整路径）。回归：`tests/test_jwgb_desorption_closure.py`（派生夹具 `tests/fixtures/jwgb_bk200c_sample1_vendor_pair.json`，不含客户原始记录）、`tests/test_jwgb_vendor_pair_check.py`。
