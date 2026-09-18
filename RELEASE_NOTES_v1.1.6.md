# v1.1.6

- BET、Langmuir、t-Plot、BJH、DH、HK、DFT 的等温线蓝色选区独立保存；t-Plot 首次显示范围为 P/P₀ = 0–0.6，不改变内部拟合规则。
- 修正 HK 平滑微分在 Original H-K 与 Cheng–Yang 校正下的处理，改善与官方导出参考数据的一致性。
- 等温线及孔径分布曲线采用轻量 Akima 插值，优化标签切换、绘图和 DFT 正则化滑块的响应，不引入大型插值库。
- 绿色选框的选区孔容按相邻数据区间连续计算，减少边界拖动时的数值跳变，并保持完整区间孔容守恒。
- 合并结果参数与样品条件，移除报告模块、日志/样品管标签；新增 BET、Langmuir、t-Plot 多样品结果表，排序与样品栏联动。
- 计算状态改为文件名右侧标记，保留异常提示；单点 BET 数据按需显示，±误差拆为相邻独立列。
- 结果表、实际等温线与目标压力表支持单元格拖选、右键复制和 Ctrl+C，便于粘贴到 Excel；结果表取消拖动换序，样品栏保留该功能。
- 统一选中颜色、文件名联动高亮与复制菜单；禁止文件名排序并隐藏排序箭头，其他结果列仍可点击排序。
- 优化表格列宽与右边框贴合，长文件路径省略显示、悬停查看、双击复制完整内容；修正样品栏圆点对齐。

验证：124 项自动化测试通过；发布前另验证 Windows 单文件程序的模型资源、DFT 多进程计算及下载文件 SHA-256。

本次发布 Windows x64 单文件程序。Gitee 仓库附件已达 1 GB 配额，本版 Gitee 发布页面和软件更新入口暂指向 GitHub 的同一 EXE，SHA-256 校验不变，未删除任何历史版本附件。macOS 下载维持原有版本，不代表已发布 macOS 1.1.6。算法与厂商软件的对照仅覆盖已验证参考数据，不保证所有样品和设置完全一致。

- [下载 Windows x64 EXE](https://github.com/dragonMaLong/unified-bet-analysis/releases/download/v1.1.6/BET-DragonScience.exe)
- [SHA256SUMS.txt](https://github.com/dragonMaLong/unified-bet-analysis/releases/download/v1.1.6/SHA256SUMS.txt)
