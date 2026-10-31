"""Read JWGB XML and expose explicitly labelled candidate reconstruction.

Only the observed BK200C manual-Q configuration is enabled. Unsupported
profiles remain pressure-only; raw records and provenance are always retained.
"""
from __future__ import annotations

import math
import hashlib
import re
from datetime import datetime
from dataclasses import replace
from pathlib import Path
import xml.etree.ElementTree as ET

from .models import FreeSpaceInfo, IsothermPoint, MicString, RunConditions, SampleInfo, SmpHeader, TriStarResult
from .jwgb_reconstruction import reconstruct

IMPORT_NOTE = "仅导入原始压力记录；吸附量尚未还原，暂不能计算比表面积和孔径。请使用原厂导出的等温线 Excel 或 RAW 进行定量分析。"
RECONSTRUCTION_NOTE = "实验性重建：按文件内 Q 和压力/温度记录计算吸附量；BET 为重建值，尚未与本文件原厂结果核验。脱附未施加推测的闭合修正。"


class JwgbNativeParseError(ValueError):
    pass


def _number(value: str | None, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise JwgbNativeParseError(f"JWGBT 字段 {field} 缺失或不是数字") from exc
    if not math.isfinite(number):
        raise JwgbNativeParseError(f"JWGBT 字段 {field} 不是有限数值")
    return number


def _date(value: str | None, field: str) -> datetime:
    try:
        return datetime.strptime(value or "", "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise JwgbNativeParseError(f"JWGBT 字段 {field} 时间格式无效") from exc


def _reconstruct_bk200c(root, records, points, mass):
    required = {
        'StationConfig/Station/Model': 'BK200C',
        'Config/Base/Version': '00.01.05.00',
        'Config/Base/DoseGasType': '0',
        'Config/AutoTestQ': '0', 'Config/FastTestQ': '0',
        'Config/IsMicroproe': '0',
        'General/GeneralConfig/AdsorbGas': 'N2',
        'General/GeneralConfig/IsRealGas': '0',
        'General/GeneralConfig/IsInsitu': '0',
    }
    for field, expected in required.items():
        if root.findtext(field) != expected:
            raise ValueError(f'尚未覆盖的重建配置：{field}')
    if _number(root.findtext('General/GeneralConfig/AdsorbTemp'), 'AdsorbTemp') != 77.35:
        raise ValueError('重建仅覆盖 N2/77.35 K')
    if _number(root.findtext('General/GeneralConfig/CrossSectionalArea'), 'CrossSectionalArea') != .162:
        raise ValueError('重建仅覆盖氮气截面积 0.162 nm²')
    vd = _number(root.findtext('Config/Vd'), 'Vd')
    q = _number(root.findtext('Config/QValue'), 'QValue')
    factor = _number(root.findtext('General/GeneralConfig/NonIdealityFactor'), 'NonIdealityFactor')
    low = _number(root.findtext('Config/MultiBETReport/MinRange'), 'BET MinRange')
    high = _number(root.findtext('Config/MultiBETReport/MaxRange'), 'BET MaxRange')
    if not 0 < low < high < 1:
        raise ValueError('文件内 BET 选区无效')
    if any(r.get('Balance') not in ('0', '1') or 'SubIndex' not in r for r in records):
        raise ValueError('重建需要完整的平衡和补气标记')
    calculated = reconstruct({'records': records, 'vd_cm3': vd, 'mass_g': mass,
                               'stored_q': q, 'nonideality_factor': factor}, rounded_q=True)
    measured = [r for r in calculated['rows'] if not r['is_reference']]
    if len(measured) != len(points):
        raise ValueError('重建点数与压力点表不一致')
    rebuilt = []
    for point, row in zip(points, measured):
        if (point.phase, point.index) != (row['phase'], row['measurement']):
            raise ValueError('重建记录与压力点不对应')
        value = row['candidate_cm3_g']
        if not math.isfinite(value):
            raise ValueError('重建气量溢出')
        rebuilt.append(replace(point, raw_internal_cm3_stp=value * mass,
                               quantity_adsorbed_cm3_g_stp=value,
                               quantity_adsorbed_mmol_g=value / 22.414))
    return rebuilt, {
        'quantity_status': 'reconstructed_unvalidated',
        'jwgb_reconstructed': True, 'import_notice': RECONSTRUCTION_NOTE,
        'jwgb_reconstruction_profile': 'BK200C-00.01.05.00-manual-Q-N2-77.35K',
        'jwgb_reconstruction_q': q, 'jwgb_reconstruction_vd_cm3': vd,
        'jwgb_reconstruction_nonideality': factor,
        'jwgb_reconstruction_validation': 'cross_model_candidate_no_same_run_reference',
        'jwgb_reconstruction_calibration_source': 'manual_file_QValue',
        'jwgb_reconstruction_closure_applied': False,
        'jwgb_reconstruction_rows': calculated['rows'],
        'use_stored_fit_ranges': True,
        'stored_bet_pressure_min': low, 'stored_bet_pressure_max': high,
    }


def load_jwgb_native(path: str | Path, *, reconstruct_uptake: bool = True) -> TriStarResult:
    path = Path(path)
    with path.open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise JwgbNativeParseError("JWGBT 文件超过 16 MiB")
    try:
        text = data.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise JwgbNativeParseError("JWGBT 不支持 DTD 或实体声明")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError) as exc:
        raise JwgbNativeParseError("JWGBT 不是完整的 UTF-8 XML 文件") from exc
    if root.tag != "doc" or root.get("Ver") != "1":
        raise JwgbNativeParseError("尚不支持此 JWGBT 文档版本")
    for section in ("General", "StationConfig", "Config", "Data"):
        element = root.find(section)
        if element is None or element.get("Ver") != "1":
            raise JwgbNativeParseError(f"JWGBT 缺少或不支持 {section} 数据块")
    if root.findtext("Config/Base/PressUnits") != "0":
        raise JwgbNativeParseError("尚未验证此 JWGBT 压力单位，仅支持 PressUnits=0（kPa）")
    get = lambda key: (root.findtext(key) or "").strip()
    start = _date(get("Data/BaseInfo/StartTime"), "StartTime")
    end = _date(get("Data/BaseInfo/EndTime"), "EndTime")
    if end < start:
        raise JwgbNativeParseError("JWGBT 结束时间早于开始时间")
    mass = _number(get("Config/SampleInfo/SampleWeight"), "SampleWeight")
    # The public Sync440A sample uses a decimal comma for this field only.
    temperature_text = get("General/GeneralConfig/AdsorbTemp")
    if re.fullmatch(r"\d+,\d+", temperature_text):
        temperature_text = temperature_text.replace(",", ".")
    temperature = _number(temperature_text, "AdsorbTemp")
    if mass <= 0 or temperature <= 0:
        raise JwgbNativeParseError("JWGBT 样品质量及浴温必须大于零")
    points = []
    raw_records = []
    for branch, phase in (("Adsorb", "adsorption"), ("Doff", "desorption")):
        ids = set()
        balanced_ids = set()
        previous_time = None
        for row in root.findall(f"Data/{branch}/Item"):
            record = dict(row.attrib)
            raw_records.append({"phase": phase, **record})
            record_id = record.get("ID", "")
            match = re.fullmatch(r"(0|[1-9]\d*)(?:_([1-9]\d*))?", record_id)
            if not match:
                raise JwgbNativeParseError("JWGBT 压力记录缺少有效 ID")
            point_id = int(match[1])
            sub_index = match[2] or "0"
            if record.get("SubIndex", "0") != sub_index:
                raise JwgbNativeParseError("JWGBT ID 与 SubIndex 不一致")
            if record.get("Balance", "1") not in ("0", "1"):
                raise JwgbNativeParseError("JWGBT Balance 标记无效")
            if record_id in ids:
                raise JwgbNativeParseError(f"JWGBT {branch} 存在重复 ID {record_id}")
            ids.add(record_id)
            pd = _number(record.get("Pd"), "Pd")
            pressure = _number(record.get("Pcd"), "Pcd")
            p0 = _number(record.get("P0"), "P0")
            if p0 <= 0 or pressure < 0:
                raise JwgbNativeParseError("JWGBT 平衡压力或饱和压力无效")
            when = _date(f"{record.get('Day', '')} {record.get('Time', '')}", "Item/Time")
            if not start <= when <= end:
                raise JwgbNativeParseError("JWGBT 压力记录时间超出测试时间")
            if previous_time is not None and when < previous_time:
                raise JwgbNativeParseError("JWGBT 压力记录时间顺序无效")
            previous_time = when
            # Native branches start with a non-dose reference (Pd=-0.00001).
            # Preserve it in raw_records, not as an additional measured point.
            if pd == -0.00001:
                continue
            if pd < 0:
                raise JwgbNativeParseError("JWGBT 未知负充气压力记录")
            # Intermediate doses remain in raw_records for mass balance, but
            # only Balance=1 records are exported as equilibrium points.
            if record.get("Balance", "1") == "0":
                continue
            if point_id in balanced_ids:
                raise JwgbNativeParseError(f"JWGBT {branch} 存在重复平衡点 {point_id}")
            balanced_ids.add(point_id)
            points.append(IsothermPoint(
                index=point_id, phase=phase, record_rel_offset=0,
                absolute_pressure_mmHg=pressure * 760 / 101.325,
                relative_pressure=pressure / p0, raw_internal_cm3_stp=None,
                saturation_pressure_mmHg=p0 * 760 / 101.325,
                elapsed_seconds=int((when - start).total_seconds()),
                quantity_adsorbed_cm3_g_stp=None, quantity_adsorbed_mmol_g=None,
            ))
    if not points:
        raise JwgbNativeParseError("JWGBT 中没有可读取的压力记录")
    saved = datetime.fromtimestamp(path.stat().st_mtime)
    duration = int((end - start).total_seconds())
    gas = get("General/GeneralConfig/AdsorbGas")
    options = {
        "instrument_manufacturer": "JWGB", "instrument_model": get("StationConfig/Station/Model"),
        "instrument_software": get("Config/Base/Version"), "format_family": "JWGB native XML",
        "jwgb_native_import": True, "quantity_status": "unavailable_native_pressure_only",
        "import_notice": IMPORT_NOTE, "jwgb_native_pressure_records": raw_records,
        "jwgb_native_config_xml": ET.tostring(root.find("Config"), encoding="unicode"),
        "jwgb_native_general_xml": ET.tostring(root.find("General"), encoding="unicode"),
        "jwgb_native_sha256": hashlib.sha256(data).hexdigest(),
        "test_started_time": str(start), "test_started_raw": int(start.timestamp()),
        "test_completed_time": str(end), "test_completed_raw": int(end.timestamp()),
        "test_duration_seconds": duration, "test_duration_time": f"{duration // 3600:02}:{duration // 60 % 60:02}:{duration % 60:02}",
        "sample_saved_time": str(saved.replace(microsecond=0)), "sample_saved_raw": int(saved.timestamp()),
        "test_time_source": "JWGBT instrument local time (no timezone supplied)",
    }
    if reconstruct_uptake:
        try:
            rebuilt, metadata = _reconstruct_bk200c(root, raw_records, points, mass)
        except (ValueError, KeyError, OverflowError) as exc:
            options['jwgb_reconstruction_unavailable_reason'] = str(exc)
        else:
            points = rebuilt
            options.update(metadata)
    return TriStarResult(
        header=SmpHeader(str(path.resolve()), path.name, len(data), "JWGB_XML", "1", int(start.timestamp()), str(start), int(saved.timestamp()), str(saved.replace(microsecond=0)), 0, 0),
        subsets=[], sample=SampleInfo(get("Config/SampleInfo/SampleName") or get("Config/SampleInfo/SampleNum") or path.stem, get("Config/SampleInfo/Operator"), "", "", mass, None),
        run_conditions=RunConditions(None, None, None, None, None, None, None, None, None, points[-1].saturation_pressure_mmHg, temperature, gas, "Nitrogen" if gas == "N2" else gas),
        target_pressure_table=[], free_space=FreeSpaceInfo(None, None, None, None, None, None, None, None, "未验证原厂自由空间换算"),
        po_records=[], isotherm=points, adsorptive_properties=None,
        log_messages=[MicString(i, f"{row.get('Date', '')} {row.get('Log', '')}") for i, row in enumerate(root.findall("Log/Log/Item"))],
        sample_tube_strings=[], method_options=options, raw_strings={},
    )
