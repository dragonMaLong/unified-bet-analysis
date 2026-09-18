# v1.2.1

- BET、Langmuir、t-Plot 结果表新增行列切换：点击“文件名”表头右侧按钮，可切换为样品作为列、参数作为行；三个标签共用并记忆布局。
- 转置后保留参数列冻结、计算完成标记，以及与样品栏一致的排序和高亮联动；两种布局均支持拖选单元格、右键复制和 Ctrl+C。
- 取消结果参数、BET、Langmuir、t-Plot、选区孔容量、实际等温线、目标压力表七个标签内的悬浮提示。
- 优化复制选区：左键点击表格外部或空白位置即可取消选择；右键打开复制菜单时保留选区。
- 统一单击排序操作，取消表头点击后整列选中的行为；转置布局下单击参数名排序，拖动仍用于框选复制。
- 文件名联动仅加粗，不再额外填充蓝色背景；蓝色底色仅表示实际选中的单元格。

本次提供 Windows x64 单文件程序，沿用现有界面风格，未引入新的大型依赖。

验证：160 项自动化测试通过；使用 100.SMP、2000MM.SMP 检查结果表两种布局。实际 EXE 的 81 个模型资源、DFT 双进程两轮计算自检通过，程序报告版本为 1.2.1。

macOS 下载维持原有版本，不代表已发布 macOS 1.2.1。

- [Gitee 下载 Windows x64 EXE](https://gitee.com/dragonMalong/unified-bet-analysis/releases/download/v1.2.1/BET-DragonScience.exe)
- [Gitee SHA256SUMS.txt](https://gitee.com/dragonMalong/unified-bet-analysis/releases/download/v1.2.1/SHA256SUMS.txt)
- [GitHub 下载 Windows x64 EXE](https://github.com/dragonMaLong/unified-bet-analysis/releases/download/v1.2.1/BET-DragonScience.exe)
- [GitHub SHA256SUMS.txt](https://github.com/dragonMaLong/unified-bet-analysis/releases/download/v1.2.1/SHA256SUMS.txt)
