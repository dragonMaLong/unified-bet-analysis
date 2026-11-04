"""Instruments / software whose files the app reads (shown in Settings).

Keep in step with the compatibility table in README.md when support changes.
Levels: "已验证" results checked against the vendor's software or reports;
"部分支持" the file is read but some quantities are reconstructed estimates.
"""
from __future__ import annotations

from dataclasses import dataclass

LEVEL_VERIFIED = "已验证"
LEVEL_PARTIAL = "部分支持"


@dataclass(frozen=True)
class SupportedInstrument:
    vendor: str
    model: str
    formats: str
    level: str
    note: str


SUPPORTED_INSTRUMENTS: tuple[SupportedInstrument, ...] = (
    SupportedInstrument("Micromeritics 麦克", "TriStar II Plus（MicroActive for TriStar II Plus、Version 3.02）",
                        "SMP、XLS、XLSX", LEVEL_VERIFIED, "原始 SMP 与官方 Excel 报告"),
    SupportedInstrument("Micromeritics 麦克", "TriStar II 3020（含 V1.00 / V1.03）",
                        "SMP", LEVEL_VERIFIED, "已与 MicroActive 逐点对照"),
    SupportedInstrument("Micromeritics 麦克", "ASAP 2460",
                        "SMP、XLS、XLSX", LEVEL_VERIFIED, ""),
    SupportedInstrument("Micromeritics 麦克", "ASAP 2425（Version 1.00）",
                        "SMP", LEVEL_VERIFIED, "已与 MicroActive 逐点对照；含热流逸修正"),
    SupportedInstrument("Micromeritics 麦克", "ASAP 2020 Plus",
                        "SMP、XLS、XLSX", LEVEL_VERIFIED, "含真实气体修正、热流逸修正"),
    SupportedInstrument("Micromeritics 麦克", "ASAP 2020（V4.04）",
                        "SMP", LEVEL_VERIFIED, ""),
    SupportedInstrument("Micromeritics 麦克", "ASAP 2010（V5.00 旧版 SMP）",
                        "SMP", LEVEL_VERIFIED, "已与 MicroActive 对照，样本较少"),
    SupportedInstrument("Micromeritics 麦克", "Gemini VII 2390（V1.00）",
                        "SMP", LEVEL_VERIFIED, "已与 MicroActive 逐点对照"),
    SupportedInstrument("Micromeritics 麦克", "3Flex 3500（原始 SMP、Flex 6.03 报告）",
                        "SMP、XLS", LEVEL_VERIFIED,
                        "仅物理吸附；N2、Ar、Kr、CO2、正丁烷/戊烷/己烷共 18 份示例已与 MicroActive 逐点核对。"
                        "暖区按 298 K 近似，部分样品吸附量偏差约 0.03%–0.09%；Kr 的 t-Plot 偏差约 5%。"),
    SupportedInstrument("MicrotracBEL", "BELSORP（BELMaster 导出）",
                        "DAT、XLS、XLSX", LEVEL_VERIFIED, ""),
    SupportedInstrument("Quantachrome 康塔", "Autosorb iQ / QuadraSorb；NovaWin 11.02 Excel 报告",
                        "QPS、XLSX", LEVEL_VERIFIED, ""),
    SupportedInstrument("贝士德 BSD", "BSD-660MC（V.9.1.15.0）",
                        "XLS、XLSX", LEVEL_VERIFIED, "BJH 孔容递推仍在完善"),
    SupportedInstrument("精微高博 JWGB", "APAS1000_ZQ / Matrix1000",
                        "RAW、XLSX", LEVEL_VERIFIED, ""),
    SupportedInstrument("精微高博 JWGB", "BK200C / Sync440A（XML）",
                        "JWGBT", LEVEL_PARTIAL, "吸附量为重建值，仅供参考"),
)

NOT_SUPPORTED_NOTE = (
    "暂不支持：压汞、化学吸附、真密度等非物理吸附文件。"
    "未列出的型号或版本尚未拿到样本验证。"
)
