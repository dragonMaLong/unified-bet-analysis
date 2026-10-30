"""User-facing consent and privacy controls for optional usage statistics."""
from __future__ import annotations

import platform
import re
import sys

from pyqtgraph.Qt import QtCore, QtWidgets


class TelemetryDetailsDialog(QtWidgets.QDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用统计详情")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(480)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(14)
        description = QtWidgets.QLabel(
            "DragonScience 使用这些统计判断版本覆盖、文件兼容性和界面使用情况。\n\n"
            "包含：软件版本、随机安装标识、操作系统、屏幕分辨率与缩放；"
            "导入文件的格式、成功或失败及耗时；分析页面访问、导出结果和标准错误码。\n\n"
            "不包含：实验内容、计算数值、样品名称、文件名或路径。\n\n"
            "服务器会收到连接 IP，保留窗口为 7 天；其他统计为 90 天，并定期清理。"
            "随机安装标识用于估算活跃安装量，不代表真实人数。\n\n"
            "你可随时在主窗口右下角的“隐私设置”关闭统计。关闭会清空本机待发记录和安装标识，"
            "停止后续发送；已发出的请求无法撤回。是否启用都不影响分析功能。"
        )
        description.setWordWrap(True)
        description.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(description)
        if client.available:
            service_text = "接收地址：" + client.endpoint
        elif client.endpoint:
            service_text = "当前无法使用本机统计设置存储，使用统计保持关闭。"
        else:
            service_text = "统计服务尚未配置或地址无效，当前不会收集或发送使用统计。"
        service = QtWidgets.QLabel(service_text)
        service.setWordWrap(True)
        service.setTextFormat(QtCore.Qt.PlainText)
        service.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(service)
        buttons = QtWidgets.QDialogButtonBox(self)
        buttons.addButton("关闭", QtWidgets.QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _add_telemetry_summary(layout, client, parent):
    row = QtWidgets.QHBoxLayout()
    description = QtWidgets.QLabel("帮助 DragonScience 改进兼容性和使用体验")
    description.setWordWrap(True)
    row.addWidget(description, 1)
    details = QtWidgets.QPushButton("详情", parent)
    details.setAutoDefault(False)
    details.clicked.connect(lambda: TelemetryDetailsDialog(client, parent).exec_())
    row.addWidget(details)
    layout.addLayout(row)


class TelemetrySettingsDialog(QtWidgets.QDialog):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.setWindowTitle("隐私设置")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(440)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(14)
        _add_telemetry_summary(layout, client, self)
        self.enable_check = QtWidgets.QCheckBox("启用使用统计")
        self.enable_check.setChecked(bool(client.enabled))
        self.enable_check.setEnabled(bool(client.available))
        layout.addWidget(self.enable_check)
        if not client.available:
            status = QtWidgets.QLabel("当前统计服务不可用，使用统计保持关闭。")
            status.setWordWrap(True)
            layout.addWidget(status)
        buttons = QtWidgets.QDialogButtonBox(self)
        buttons.addButton("保存", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton("取消", QtWidgets.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TelemetryUpdateDialog(QtWidgets.QDialog):
    """One update decision with an explicit, separate optional-statistics choice."""

    CONSENT_AND_UPDATE = "consent_and_update"
    UPDATE_ONLY = "update_only"

    def __init__(self, title: str, message: str, client, parent=None):
        super().__init__(parent)
        self.choice = None  # Closing/Escape never changes a saved preference.
        self.setWindowTitle("软件更新")
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(480)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(14)
        heading = QtWidgets.QLabel(title)
        heading.setTextFormat(QtCore.Qt.PlainText)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        notes = QtWidgets.QPlainTextEdit()
        notes.setPlainText(message)
        notes.setReadOnly(True)
        notes.setMaximumHeight(220)
        layout.addWidget(notes)
        _add_telemetry_summary(layout, client, self)
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        self.agree_button = QtWidgets.QPushButton("同意统计并更新")
        self.agree_button.setEnabled(bool(client.available))
        self.agree_button.setAutoDefault(False)
        self.update_only_button = QtWidgets.QPushButton("仅更新")
        self.update_only_button.setDefault(True)
        self.agree_button.clicked.connect(lambda: self._choose(self.CONSENT_AND_UPDATE))
        self.update_only_button.clicked.connect(lambda: self._choose(self.UPDATE_ONLY))
        actions.addWidget(self.agree_button)
        actions.addWidget(self.update_only_button)
        layout.addLayout(actions)

    def _choose(self, choice):
        self.choice = choice
        self.accept()


def startup_environment(screen) -> dict:
    """Only bounded system categories, never a host name or user name."""
    system = platform.system()
    if system == "Windows":
        version = sys.getwindowsversion()
        release = f"{version.major}.{version.minor}.{version.build}"
    else:
        match = re.match(r"[0-9.]{1,32}", platform.release())
        release = match.group() if match else "unknown"
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x86_64", "x86_64": "x86_64", "x86": "x86", "i386": "x86",
        "i686": "x86", "arm64": "arm64", "aarch64": "arm64",
    }.get(machine, "other")
    size = screen.size() if screen is not None else QtCore.QSize(0, 0)
    return {
        "os": system if system in {"Windows", "Linux", "Darwin"} else "other",
        "os_version": release,
        "architecture": architecture,
        "screen_width": max(0, min(size.width(), 32768)),
        "screen_height": max(0, min(size.height(), 32768)),
        "scale": max(0.5, min(float(screen.devicePixelRatio()) if screen is not None else 1.0, 8.0)),
    }
