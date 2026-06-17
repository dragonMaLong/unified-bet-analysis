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

平台为每个厂商/软件单独编写解析器，并在分析阶段按来源保留各自的默认规则（区间选取、厚度方程、物理常数、t-Plot 纵轴定义等）。当前已支持的导入格式：

| 厂商 | 仪器型号 | 配套软件 / 报表 | 导入文件格式 | 解析模块 |
| --- | --- | --- | --- | --- |
| Micromeritics（麦克） | TriStar II 3020、TriStar II Plus、ASAP 2460、ASAP 2020 Plus | TriStar II 3020 软件 / MicroActive | `.smp` 原生文件 | `smp.py` |
| Micromeritics（麦克） | 同上 | MicroActive 报表导出 | `.xls` / `.xlsx` / `.xlsm` | `excel_import.py` |
| MicrotracBEL（麦奇克拜尔） | BELSORP 系列 | BELMaster | `.dat` 导出文件 | `belmaster.py` |
| MicrotracBEL（麦奇克拜尔） | BELSORP 系列 | BELMaster 报表导出 | `.xls` / `.xlsx` / `.xlsm` | `excel_import.py` |
| Quantachrome（康塔） | Autosorb iQ | ASiQwin | `.qps` 文件 | `quantachrome.py` |
| 贝士德（BSD / Beishide） | BSD-660 等 | 仪器报表 | `.xls` / `.xlsx` / `.xlsm` | `excel_import.py` |

不同来源在分析时会自动匹配对应的默认算法，例如 TriStar II 3020 沿用其历史阿伏伽德罗常数、BSD t-Plot 以吸附量（STP）而非液体体积作纵轴、ASAP 2460 对存储区间下限做特定修正等。这样默认结果会贴近原软件，而统一重算时又能切换到一致规则。

> **关于 Micromeritics 3Flex 3500：** 其 `.smp` 原生文件目前**暂不支持**（SMP 为不同的二进制变体，等温线编码方式与现有 SMP 不同，直接读取得到 0 个点）。变通办法：在 MicroActive 中把等温线表格**复制粘贴到 Excel**（含 `Relative Pressure (p/p°)` 与 `Quantity Adsorbed (mmol/g)` 两列，以及 `... : Adsorption` / `... : Desorption` 分支标记），再用本工具导入即可——已验证可在 MicroActive 给定区间下精确复现其 BET 结果。后续若提供可对照的官方报表，会补上原生 `.smp` 解析。

## 支持的导出格式

- **Excel 工作簿（`.xlsx`）** — 界面中点击「导出文件」，把当前勾选的样品导出为多工作表的 XLSX：
  - `摘要`：每个样品一行，含测试时间、质量、点数、BET 面积 / Vm / C、Langmuir 面积、t-Plot 外比表面积 / 微孔体积，以及按当前 BJH 绿色选区命名的孔容量列（如 `2nm-10nm孔容量(cm3/g)`）。
  - `实际等温线`、`目标压力表`、`BET`、`Langmuir`、`t-Plot`、`样品条件` 等明细工作表。
- **CSV** — 命令行模式（见下文）对每个样品导出 CSV，便于脚本化批处理。

## 当前功能

- 读取上表所列的 SMP / XLS(X/M) / DAT / QPS 文件。
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
- 结果参数、样品条件、实际等温线、目标压力表、报告模块和日志信息查看。
- 通过「导出文件」把勾选样品导出为多工作表 XLSX（详见上文“支持的导出格式”）。
- 文件对话框首次默认打开桌面，并记住上次导入/导出所用目录，下次启动直接定位到该目录。
- 命令行解析 SMP / XLS(X/M) / DAT / QPS 并导出 CSV。

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

## Windows 可执行文件（exe）

无需安装 Python，可直接到 GitHub 的 **Releases** 页面下载打包好的单文件 `Micromeritics-BET.exe` 运行（首次启动会稍慢，因为需要解压到临时目录）。参比厚度曲线（默认 `sio2oh.thk`）已内置在程序中，运行不依赖任何仪器厂商软件。

自行打包（需先安装依赖与 PyInstaller）：

```
python -m pip install -r requirements.txt pyinstaller pillow
pyinstaller --noconfirm --clean BET.spec
```

打包配置见 [`BET.spec`](BET.spec)，会把内置参比曲线（`tristar_bet/reference_data`）和程序图标（`tristar_bet/assets`）一并收入 exe。

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

可用 `--prefix` 自定义导出文件名前缀。若未指定 `--out-dir`，默认导出到当前工作目录下的 `bet_csv_output` 文件夹（不再依赖任何特定机器上的绝对路径）。

## 项目结构

```
app.py                              启动入口，支持 GUI 和命令行解析
tristar_bet/models.py               数据模型（统一的 TriStarResult 等）
tristar_bet/smp.py                  Micromeritics SMP 解析与 CSV 导出
tristar_bet/excel_import.py         MicroActive / BSD 等 Excel 报表解析
tristar_bet/belmaster.py            MicrotracBEL BELSORP (BELMaster) DAT 解析
tristar_bet/quantachrome.py         Quantachrome Autosorb iQ QPS 解析
tristar_bet/reference_thickness.py  参考厚度曲线与插值
tristar_bet/reference_data/         内置参比厚度曲线（ref.thk / sio2oh.thk / sio2odms.thk）
tristar_bet/assets/                 程序图标（BET-logo.png / BET-logo.ico）
tristar_bet/analysis.py             BET、Langmuir、t-Plot、BJH 等分析计算
tristar_bet/ui/main_window.py       中文图形界面主窗口
tristar_bet/ui/plots.py             图表绘制
BET.spec                            PyInstaller 打包配置（生成 Windows exe）
validate_against_xls.py             与 XLS 导出结果对照验证的辅助脚本
```

## 当前状态

项目仍在持续开发中。各厂商解析器与 BET / Langmuir / t-Plot / BJH 的统一分析体验已可用，并以厂商默认规则为基线对齐原软件结果。

后续计划包括：

- 允许用户显式选择用哪一套厂商/软件版本的算法重算（逐样品下拉与全局统一选项），而不仅依赖文件自带的来源标识。
- 接入更多仪器厂商和 BET 软件的数据格式，包括 Micromeritics 3Flex 3500 的原生 `.smp`。
- 补充更多孔径分布算法（如 DH）与报告参数。
- 扩充标准样品数据集，完善与各软件结果的交叉验证文档与自动化测试。

## 说明

本项目的初衷不是替代任何仪器厂商软件，而是提供一个开放的统一分析入口，让研究者能够清楚地看到数据、区间、公式和结果之间的关系，并方便地进行不同样品之间的可重复比较。
