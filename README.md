# 统一 BET 分析平台

这是一个面向 BET 比表面积与孔结构数据处理的中文分析工具。项目当前以 Micromeritics TriStar II 3020 2.0 `SMP` 文件为起点，提供 SMP 解析、多样品对比、BET / Langmuir / t-Plot 拟合、BJH 孔径分布、图形化选区调整和 Excel 导出。

## 项目目标

市面上的 BET 分析软件、仪器厂商软件和报告格式并不统一。不同软件的默认区间、拟合规则、厚度方程和结果展示方式存在差异，这会给不同样品之间的横向对比带来困难。

这个项目的目标很简单：

- 把常见 BET 分析软件的核心分析流程逐步集成到一个工具里。
- 让不同来源、不同样品的数据能用统一规则重新分析。
- 让 BET、Langmuir、t-Plot、BJH 等结果可以透明地查看、调整和比较。
- 尽可能保留软件默认算法，同时允许用户明确地进行人工区间调整。

最终希望它成为一个开放、可验证、方便扩展的 BET 统一分析平台。

## 当前功能

- 读取 TriStar II 3020 2.0 `SMP` 文件。
- 多样品导入、显示、隐藏、排序、删除和拖拽调整顺序。
- 样品列表冻结前两列，便于横向滚动时查看样品名称。
- 吸附 / 脱附等温线多样品叠加显示。
- BET 拟合图、Langmuir 拟合图、t-Plot 图和 BJH 孔径分布图多样品叠加显示。
- BET、Langmuir、t-Plot 拟合区间可视化拖拽调整。
- 人工调整后的样品结果以蓝色标记，便于区分默认计算和人工调整结果。
- t-Plot 支持多种厚度方程：
  - Kruk-Jaroniec-Sayari
  - Halsey
  - Harkins-Jura
  - Broekhoff-De Boer
  - 碳黑 STSA
- t-Plot 支持 BET、Langmuir 或手动输入总表面积。
- BJH 支持吸附 / 脱附分支同时显示，并复用厚度曲线公式参数界面。
- 结果参数、样品条件、实际等温线、目标压力表、报告模块和日志信息查看。
- 选中样品导出为 XLSX。
- 命令行解析 SMP 并导出 CSV。

## 安装依赖

建议使用 Python 3.10 或更新版本。

```powershell
python -m pip install -r requirements.txt
```

如果不使用图形界面，只做命令行 SMP 解析，核心解析逻辑对 GUI 依赖较少；完整界面需要安装 `PyQt5`、`pyqtgraph`、`numpy` 和 `openpyxl`。

## 启动图形界面

在项目根目录运行：

```powershell
python app.py
```

或显式启动中文界面：

```powershell
python app.py --ui
```

## 命令行解析

解析单个 SMP 文件：

```powershell
python app.py path\to\sample.SMP
```

解析一个目录中的 SMP 文件并导出 CSV：

```powershell
python app.py path\to\smp_folder --out-dir path\to\output
```

仅打印摘要，不导出 CSV：

```powershell
python app.py path\to\sample.SMP --no-export
```

## 项目结构

```text
app.py                         启动入口，支持 GUI 和命令行解析
tristar_bet/models.py          数据模型
tristar_bet/smp.py             SMP 文件解析与 CSV 导出
tristar_bet/analysis.py        BET、Langmuir、t-Plot、BJH 等分析计算
tristar_bet/ui/main_window.py  中文图形界面主窗口
tristar_bet/ui/plots.py        图表绘制
validate_against_xls.py        与 XLS 导出结果对照验证的辅助脚本
```

## 当前状态

项目仍在持续开发中。当前重点是 TriStar II 3020 2.0 `SMP` 文件解析和 BET / Langmuir / t-Plot / BJH 的统一分析体验。

后续计划包括：

- 接入更多仪器厂商和 BET 软件的数据格式。
- 完善 BJH 校正细节，并继续补充 DH 等孔径分布算法。
- 增加更多 t-Plot 厚度方程和报告参数。
- 建立标准样品数据集，用于不同软件结果之间的交叉验证。
- 完善自动化测试与算法验证文档。

## 说明

本项目的初衷不是替代任何仪器厂商软件，而是提供一个开放的统一分析入口，让研究者能够清楚地看到数据、区间、公式和结果之间的关系，并方便地进行不同样品之间的可重复比较。
