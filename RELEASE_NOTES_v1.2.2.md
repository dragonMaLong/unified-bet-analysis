# v1.2.2

- 修复吸附/脱附等温线负值点的显示与连线：零值、负值正常参与绘图，保持原始数据；绘图选点与计算筛选分离，不改变 BET 等分析的有效点规则。
- t-Plot 厚度方程参数在文件名位于顶部时按参数分行，仍保留在同一单元格内，并自动适配行高；文件名位于左侧时维持单行显示。
- 缺失值“—”居中显示，结果参数标签保留原有对齐。
- 结果表、实际等温线与目标压力表复制时附带所选列的列名，支持 Excel 单元格内换行，不改变框选范围。
- 鼠标悬停黄色警告图标时显示原因；普通单元格、文件名文字及绿色对号不显示重复悬浮提示。
- BET、Langmuir、t-Plot 在未保存布局时默认文件名位于顶部；保留用户已保存的手动布局选择。
- 实际等温线的 Elapsed 列改为“累计测试时间(分:秒)”；实际等温线和目标压力表的首列统一为“测试点”，点号居中。

本次提供 Windows x64 单文件程序，沿用现有界面风格，未引入新的大型依赖。

验证：175 项自动化测试通过；打包后 EXE 的 81 个模型及多进程自检通过。

macOS 下载维持原有版本，不代表已发布 macOS 1.2.2。

- [Gitee 下载 Windows x64 EXE](https://gitee.com/dragonMalong/unified-bet-analysis/releases/download/v1.2.2/BET-DragonScience.exe)
- [Gitee SHA256SUMS.txt](https://gitee.com/dragonMalong/unified-bet-analysis/releases/download/v1.2.2/SHA256SUMS.txt)
- [GitHub 下载 Windows x64 EXE](https://github.com/dragonMaLong/unified-bet-analysis/releases/download/v1.2.2/BET-DragonScience.exe)
- [GitHub SHA256SUMS.txt](https://github.com/dragonMaLong/unified-bet-analysis/releases/download/v1.2.2/SHA256SUMS.txt)
