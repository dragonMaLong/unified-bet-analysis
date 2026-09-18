# v1.2

- 在 t-Plot 结果表右侧新增“选区孔容量”标签，并列显示每个样品的 BJH、DH、HK、DFT 绿色选框孔容量；保留样品栏原有孔容列。
- 新表沿用现有样式、文件名冻结、样品排序和高亮联动，支持拖选单元格、右键复制与 Ctrl+C。
- BJH 与 DH 的绿色选区分别保存，初始默认范围相同，人工调整与恢复默认互不影响。
- 孔容量列头采用同一单元格内两行显示：方法与单位、当前绿色选区范围（nm）；拖动时实时更新，两位小数。
- BJH、DH、HK、DFT 绿色选框和等温线蓝色选框采用与压汞软件一致的边界数字样式及交互：移动时显示，点击编辑，回车或失焦确认、Esc 取消，停止操作约 1.8 秒后隐藏。
- 边界输入校验有效数字并限制在数据范围内，防止两侧交叉；未修改直接退出编辑不会舍入计算值。
- 修正跨标签查看 HK 孔容时对 HK 独立相对压力范围的使用；列头更新不扫描所有样品，复用现有计算缓存，隐藏孔容表时不额外计算四种方法。

验证：147 项自动化测试通过；使用 100.SMP、2000MM.SMP 检查选区范围、孔容表和数字编辑界面。发布前验证 Windows 单文件程序的 81 个模型资源、DFT 多进程计算及两站下载文件 SHA-256。

本次发布 Windows x64 单文件程序，GitHub 与 Gitee 提供相同 EXE 和校验文件。未引入新的大型依赖。macOS 下载维持原有版本，不代表已发布 macOS 1.2。

- [Gitee 下载 Windows x64 EXE](https://gitee.com/dragonMalong/unified-bet-analysis/releases/download/v1.2/BET-DragonScience.exe)
- [Gitee SHA256SUMS.txt](https://gitee.com/dragonMalong/unified-bet-analysis/releases/download/v1.2/SHA256SUMS.txt)
- [GitHub 下载 Windows x64 EXE](https://github.com/dragonMaLong/unified-bet-analysis/releases/download/v1.2/BET-DragonScience.exe)
- [GitHub SHA256SUMS.txt](https://github.com/dragonMaLong/unified-bet-analysis/releases/download/v1.2/SHA256SUMS.txt)
