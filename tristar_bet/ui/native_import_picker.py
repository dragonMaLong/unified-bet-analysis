"""Windows common-item dialog with separate file and folder confirmations.

Uses the documented IFileOpenDialog / IFileDialogCustomize interfaces, without
subclassing or moving Windows controls. No third-party COM package is needed.
Interface layouts follow Microsoft's ShObjIdl_core.h and ShObjIdl.h.
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ImportSelection:
    directory: Path
    files: tuple[Path, ...] = ()


class NativeDialogError(OSError):
    """A Windows dialog failure, distinct from the user pressing Cancel."""


_HRESULT = ctypes.c_int32
_DWORD = ctypes.c_uint32
_ULONG = ctypes.c_uint32
_VOID = ctypes.c_void_p
_PPVOID = ctypes.POINTER(_VOID)
_COMCALL = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_S_OK = 0
_E_NOINTERFACE = -2147467262
_E_POINTER = -2147467261
_E_FAIL = -2147467259
_CANCELLED = 0x800704C7
_FOLDER_BUTTON = 1001


class _GUID(ctypes.Structure):
    _fields_ = [("data1", _DWORD), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


_IID_UNKNOWN = _GUID.parse("00000000-0000-0000-c000-000000000046")
_IID_EVENTS = _GUID.parse("973510db-7d7f-452b-8975-74a85828d354")
_IID_CONTROL_EVENTS = _GUID.parse("36116642-d713-4b97-9b83-7484a9d00433")
_IID_CUSTOMIZE = _GUID.parse("e6fdd21a-163f-4975-9c8c-a69f1ba37034")
_IID_OPEN_DIALOG = _GUID.parse("d57c7288-d4ad-4768-be02-9d969532d960")
_IID_SHELL_ITEM = _GUID.parse("43826d1e-e718-42ee-bc55-a1e261c37bfe")
_IID_SERVICE_PROVIDER = _GUID.parse("6d5140c1-7436-11ce-8034-00aa006009fa")
_SID_TOP_LEVEL_BROWSER = _GUID.parse("4c96be40-915c-11cf-99d3-00aa004ae837")
_IID_SHELL_BROWSER = _GUID.parse("000214e2-0000-0000-c000-000000000046")
_IID_COLUMN_MANAGER = _GUID.parse("d8ec27bb-3f3b-4042-b10a-4acfd924d453")
_CLSID_OPEN_DIALOG = _GUID.parse("dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7")
_CLIENT_GUID = _GUID.parse("db011c4f-e310-4c89-9292-155fd8f698fb")


class _FilterSpec(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("pattern", ctypes.c_wchar_p)]


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", _DWORD)]


class _ColumnInfo(ctypes.Structure):
    _fields_ = [("size", _DWORD), ("mask", _DWORD), ("state", _DWORD),
                ("width", _DWORD), ("default_width", _DWORD), ("ideal_width", _DWORD),
                ("name", ctypes.c_wchar * 80)]


_PKEY_NAME = _PropertyKey(_GUID.parse("b725f130-47ef-101a-a5f1-02608c9eebac"), 10)
_PKEY_TYPE = _PropertyKey(_GUID.parse("b725f130-47ef-101a-a5f1-02608c9eebac"), 4)


class _Interface(ctypes.Structure):
    _fields_ = [("vtable", ctypes.POINTER(_VOID))]


def _check(result: int, operation: str) -> None:
    if result < 0:
        error = NativeDialogError(f"{operation}失败（HRESULT 0x{result & 0xFFFFFFFF:08X}）")
        error.hresult = result & 0xFFFFFFFF
        raise error


def _call(pointer, index: int, argument_types: tuple = (), *args, result_type=_HRESULT):
    address = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(_VOID))).contents[index]
    method = _COMCALL(result_type, _VOID, *argument_types)(address)
    return method(pointer, *args)


def _release(pointer) -> None:
    if pointer:
        _call(pointer, 2, result_type=_ULONG)


def _with_type_column(columns: Iterable[_PropertyKey]) -> tuple[_PropertyKey, ...]:
    """Move/add Type after Name while preserving every other visible column."""
    original = tuple(columns)
    if not any(bytes(key) == bytes(_PKEY_NAME) for key in original):
        return original  # Do not reinterpret a special/virtual folder's schema.
    result = []
    for key in original:
        if bytes(key) == bytes(_PKEY_TYPE):
            continue
        result.append(key)
        if bytes(key) == bytes(_PKEY_NAME):
            result.append(_PKEY_TYPE)
    return tuple(result)


def _column_manager(dialog) -> _VOID:
    """Get this dialog's active view, never an unrelated Explorer window."""
    provider, browser, view, manager = (_VOID() for _ in range(4))
    try:
        _check(_call(dialog, 0, (ctypes.POINTER(_GUID), _PPVOID),
                     ctypes.byref(_IID_SERVICE_PROVIDER), ctypes.byref(provider)), "读取窗口服务")
        _check(_call(provider, 3, (ctypes.POINTER(_GUID), ctypes.POINTER(_GUID), _PPVOID),
                     ctypes.byref(_SID_TOP_LEVEL_BROWSER), ctypes.byref(_IID_SHELL_BROWSER), ctypes.byref(browser)), "读取文件浏览器")
        _check(_call(browser, 15, (_PPVOID,), ctypes.byref(view)), "读取当前文件视图")
        _check(_call(view, 0, (ctypes.POINTER(_GUID), _PPVOID),
                     ctypes.byref(_IID_COLUMN_MANAGER), ctypes.byref(manager)), "读取文件列")
        return manager
    except Exception:
        _release(manager)
        raise
    finally:
        _release(view)
        _release(browser)
        _release(provider)


def _visible_columns(manager) -> tuple[_PropertyKey, ...]:
    count = _DWORD()
    _check(_call(manager, 5, (_DWORD, ctypes.POINTER(_DWORD)), 2, ctypes.byref(count)), "读取可见列数量")
    keys = (_PropertyKey * count.value)()
    if count.value:
        _check(_call(manager, 6, (_DWORD, ctypes.POINTER(_PropertyKey), _DWORD), 2, keys, count.value), "读取可见列")
    return tuple(keys)


def _ensure_type_column(dialog) -> bool:
    """Best-effort per-view column adjustment; failures never block import."""
    manager = _VOID()
    try:
        manager = _column_manager(dialog)
        original = _visible_columns(manager)
        desired = _with_type_column(original)
        if tuple(map(bytes, original)) == tuple(map(bytes, desired)):
            return True
        # SetColumns clears column state. Preserve each existing column's width
        # and state (including vendor columns such as Mic Description/States).
        saved = []
        for key in original:
            info = _ColumnInfo()
            info.size = ctypes.sizeof(info)
            info.mask = 0x1 | 0x10  # CM_MASK_WIDTH | CM_MASK_STATE
            _check(_call(manager, 4, (ctypes.POINTER(_PropertyKey), ctypes.POINTER(_ColumnInfo)),
                         ctypes.byref(key), ctypes.byref(info)), "读取现有列设置")
            saved.append((key, info))
        keys = (_PropertyKey * len(desired))(*desired)
        _check(_call(manager, 7, (ctypes.POINTER(_PropertyKey), _DWORD), keys, len(keys)), "显示类型列")
        restored = True
        for key, info in saved:
            # Try every restoration even if one shell-extension column fails.
            result = _call(manager, 3, (ctypes.POINTER(_PropertyKey), ctypes.POINTER(_ColumnInfo)),
                           ctypes.byref(key), ctypes.byref(info))
            restored = result >= 0 and restored
        return restored
    except Exception:
        # Some Windows namespace extensions do not expose column management.
        # The native file/folder selection must remain usable in those places.
        return False
    finally:
        try:
            _release(manager)
        except Exception:
            pass


class _WindowsApi:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise NativeDialogError("此文件选择窗口仅支持 Windows。")
        self.ole32 = ctypes.WinDLL("ole32")
        self.shell32 = ctypes.WinDLL("shell32")
        self.ole32.CoInitializeEx.argtypes = [_VOID, _DWORD]
        self.ole32.CoInitializeEx.restype = _HRESULT
        self.ole32.CoUninitialize.argtypes = []
        self.ole32.CoUninitialize.restype = None
        self.ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), _VOID, _DWORD, ctypes.POINTER(_GUID), _PPVOID]
        self.ole32.CoCreateInstance.restype = _HRESULT
        self.ole32.CoTaskMemFree.argtypes = [_VOID]
        self.ole32.CoTaskMemFree.restype = None
        self.shell32.SHCreateItemFromParsingName.argtypes = [ctypes.c_wchar_p, _VOID, ctypes.POINTER(_GUID), _PPVOID]
        self.shell32.SHCreateItemFromParsingName.restype = _HRESULT

    def shell_item(self, path: Path) -> _VOID:
        item = _VOID()
        _check(self.shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(_IID_SHELL_ITEM), ctypes.byref(item)), "读取文件夹")
        return item

    def path_from_item(self, item) -> Path:
        text = _VOID()
        try:
            # IShellItem::GetDisplayName(SIGDN_FILESYSPATH).
            _check(_call(item, 5, (_DWORD, _PPVOID), 0x80058000, ctypes.byref(text)), "读取文件路径")
            return Path(ctypes.wstring_at(text))
        finally:
            if text:
                self.ole32.CoTaskMemFree(text)

    def paths_from_array(self, items) -> tuple[Path, ...]:
        count = _DWORD()
        _check(_call(items, 7, (ctypes.POINTER(_DWORD),), ctypes.byref(count)), "读取文件数量")
        paths = []
        for index in range(count.value):
            item = _VOID()
            try:
                _check(_call(items, 8, (_DWORD, _PPVOID), index, ctypes.byref(item)), "读取选中文件")
                paths.append(self.path_from_item(item))
            finally:
                _release(item)
        return tuple(paths)


class _DialogEvents:
    """One COM identity exposing both documented event interfaces.

    Callback functions and both vtables are retained until Unadvise and Release
    have completed. Python exceptions never cross the native callback boundary.
    """

    def __init__(self, api: _WindowsApi, dialog, customize) -> None:
        self.api = api
        self.dialog = dialog
        self.customize = customize
        self.selection: ImportSelection | None = None
        self.error: Exception | None = None
        self.references = 1
        self._callbacks = []
        unknown = [
            (_HRESULT, (ctypes.POINTER(_GUID), _PPVOID), self._query_interface),
            (_ULONG, (), self._add_ref),
            (_ULONG, (), self._release_ref),
        ]
        self._events_vtable = self._make_vtable(unknown + [
            (_HRESULT, (_VOID,), self._ok),                         # OnFileOk
            (_HRESULT, (_VOID, _VOID), self._ok),                  # OnFolderChanging
            (_HRESULT, (_VOID,), self._folder_changed),           # OnFolderChange
            (_HRESULT, (_VOID,), self._update_folder_button),     # OnSelectionChange
            (_HRESULT, (_VOID, _VOID, ctypes.POINTER(_DWORD)), self._default_response),
            (_HRESULT, (_VOID,), self._ok),                         # OnTypeChange
            (_HRESULT, (_VOID, _VOID, ctypes.POINTER(_DWORD)), self._default_response),
        ])
        self._controls_vtable = self._make_vtable(unknown + [
            (_HRESULT, (_VOID, _DWORD, _DWORD), self._ok),         # OnItemSelected
            (_HRESULT, (_VOID, _DWORD), self._button_clicked),    # OnButtonClicked
            (_HRESULT, (_VOID, _DWORD, ctypes.c_int32), self._ok), # OnCheckButtonToggled
            (_HRESULT, (_VOID, _DWORD), self._ok),                 # OnControlActivating
        ])
        self._events = _Interface(self._events_vtable)
        self._controls = _Interface(self._controls_vtable)
        self.events_pointer = ctypes.cast(ctypes.pointer(self._events), _VOID)
        self.controls_pointer = ctypes.cast(ctypes.pointer(self._controls), _VOID)

    def _make_vtable(self, methods):
        callbacks = [_COMCALL(result, _VOID, *arguments)(method) for result, arguments, method in methods]
        self._callbacks.extend(callbacks)
        return (_VOID * len(callbacks))(*(ctypes.cast(callback, _VOID).value for callback in callbacks))

    def _query_interface(self, _this, iid, output):
        if not output:
            return _E_POINTER
        output[0] = None
        key = bytes(iid.contents)
        if key in (bytes(_IID_UNKNOWN), bytes(_IID_EVENTS)):
            output[0] = self.events_pointer.value
        elif key == bytes(_IID_CONTROL_EVENTS):
            output[0] = self.controls_pointer.value
        else:
            return _E_NOINTERFACE
        self.references += 1
        return _S_OK

    def _add_ref(self, _this):
        self.references += 1
        return self.references

    def _release_ref(self, _this):
        self.references -= 1
        return self.references

    @staticmethod
    def _ok(*_args):
        return _S_OK

    @staticmethod
    def _default_response(_this, _dialog, _item, response):
        if response:
            response[0] = 0
        return _S_OK

    def _selected_paths(self) -> tuple[Path, ...]:
        items = _VOID()
        item = _VOID()
        try:
            # GetSelectedItems is valid while the dialog is still displayed.
            _check(_call(self.dialog, 28, (_PPVOID,), ctypes.byref(items)), "读取当前选择")
            count = _DWORD()
            _check(_call(items, 7, (ctypes.POINTER(_DWORD),), ctypes.byref(count)), "读取当前选择数量")
            if count.value != 1:
                return ()
            # A folder shortcut only applies to one selected item. Do not
            # resolve thousands of file paths on every Ctrl/Shift selection.
            _check(_call(items, 8, (_DWORD, _PPVOID), 0, ctypes.byref(item)), "读取当前选择")
            return (self.api.path_from_item(item),)
        except NativeDialogError:
            # An empty selection or an unfinished file-name entry is normal.
            return ()
        finally:
            _release(item)
            _release(items)

    def _folder_candidate(self) -> Path | None:
        selected = self._selected_paths()
        if len(selected) == 1 and selected[0].is_dir():
            return selected[0]
        folder = _VOID()
        try:
            _check(_call(self.dialog, 13, (_PPVOID,), ctypes.byref(folder)), "读取当前文件夹")
            path = self.api.path_from_item(folder)
            return path if path.is_dir() else None
        except NativeDialogError:
            # Virtual locations such as "This PC" have no filesystem path.
            return None
        finally:
            _release(folder)

    def _fail(self, error: Exception) -> int:
        self.error = error
        try:
            _call(self.dialog, 23, (_HRESULT,), _E_FAIL)
        except Exception:
            # Even a secondary native-close failure must not let a Python
            # exception escape through a COM callback.
            pass
        return _E_FAIL

    def _folder_changed(self, *_args):
        # OnFolderChange runs for the initial folder as well as navigation.
        _ensure_type_column(self.dialog)
        return self._update_folder_button()

    def _update_folder_button(self, *_args):
        try:
            # IFileDialogCustomize::SetControlState; VISIBLE plus ENABLED only
            # when the current location (or one selected folder) can be used.
            state = 3 if self._folder_candidate() is not None else 2
            _check(_call(self.customize, 14, (_DWORD, _DWORD), _FOLDER_BUTTON, state), "更新选择文件夹按钮")
            return _S_OK
        except Exception as error:
            return self._fail(error)

    def _button_clicked(self, _this, _customize, control_id):
        if control_id != _FOLDER_BUTTON:
            return _S_OK
        try:
            folder = self._folder_candidate()
            if folder is not None:
                self.selection = ImportSelection(folder)
                # Close(S_OK) deliberately bypasses required-file validation.
                # GetResults is unavailable after Close, so save the result now.
                _check(_call(self.dialog, 23, (_HRESULT,), _S_OK), "确认文件夹")
            return _S_OK
        except Exception as error:
            return self._fail(error)


def choose_import_files(owner_hwnd: int, initial_dir: Path | str, suffixes: Iterable[str]) -> ImportSelection | None:
    """Show the native Windows picker on the calling (GUI/STA) thread.

    Return None for Cancel, files for "添加", or an empty files tuple for
    "选择文件夹". NativeDialogError signals an actual failure; callers should
    leave their existing selection untouched in that case.
    """
    api = _WindowsApi()
    # Both S_OK and S_FALSE add a COM initialization reference. On failure,
    # including RPC_E_CHANGED_MODE, there is no reference to balance.
    _check(api.ole32.CoInitializeEx(None, 0x2 | 0x4), "初始化 Windows 文件选择窗口")
    dialog = _VOID()
    customize = _VOID()
    cookie = _DWORD()
    advised = False
    events = None
    try:
        _check(api.ole32.CoCreateInstance(ctypes.byref(_CLSID_OPEN_DIALOG), None, 1,
                                         ctypes.byref(_IID_OPEN_DIALOG), ctypes.byref(dialog)), "创建 Windows 文件选择窗口")
        options = _DWORD()
        _check(_call(dialog, 10, (ctypes.POINTER(_DWORD),), ctypes.byref(options)), "读取文件选择选项")
        # FORCEFILESYSTEM | ALLOWMULTISELECT | NOCHANGEDIR | PATHMUSTEXIST |
        # FILEMUSTEXIST. Do not set PICKFOLDERS: the browser must show files.
        _check(_call(dialog, 9, (_DWORD,), options.value | 0x40 | 0x200 | 0x8 | 0x800 | 0x1000), "设置文件选择选项")
        patterns = ";".join(f"*{suffix}" for suffix in suffixes)
        if not patterns:
            raise NativeDialogError("至少需要一种支持的文件格式。")
        filters = (_FilterSpec * 1)(_FilterSpec("支持的数据文件", patterns))
        _check(_call(dialog, 4, (_DWORD, ctypes.POINTER(_FilterSpec)), 1, filters), "设置支持的文件格式")
        _check(_call(dialog, 17, (ctypes.c_wchar_p,), "选择数据文件夹"), "设置窗口标题")
        _check(_call(dialog, 18, (ctypes.c_wchar_p,), "添加"), "设置添加按钮")
        _check(_call(dialog, 24, (ctypes.POINTER(_GUID),), ctypes.byref(_CLIENT_GUID)), "设置窗口标识")
        directory = Path(initial_dir).resolve()
        if directory.is_dir():
            folder = api.shell_item(directory)
            try:
                _check(_call(dialog, 12, (_VOID,), folder), "设置初始文件夹")
            finally:
                _release(folder)
        _check(_call(dialog, 0, (ctypes.POINTER(_GUID), _PPVOID), ctypes.byref(_IID_CUSTOMIZE), ctypes.byref(customize)), "启用窗口按钮扩展")
        _check(_call(customize, 5, (_DWORD, ctypes.c_wchar_p), _FOLDER_BUTTON, "选择文件夹"), "添加选择文件夹按钮")
        _check(_call(customize, 28, (_DWORD,), _FOLDER_BUTTON), "设置选择文件夹按钮位置")
        events = _DialogEvents(api, dialog, customize)
        _check(_call(dialog, 7, (_VOID, ctypes.POINTER(_DWORD)), events.events_pointer, ctypes.byref(cookie)), "注册窗口操作")
        advised = True
        result = _call(dialog, 3, (_VOID,), owner_hwnd or None)
        if events.error is not None:
            raise NativeDialogError(f"处理文件选择操作失败：{events.error}") from events.error
        if result & 0xFFFFFFFF == _CANCELLED:
            return None
        _check(result, "显示 Windows 文件选择窗口")
        if events.selection is not None:
            return events.selection
        items = _VOID()
        try:
            _check(_call(dialog, 27, (_PPVOID,), ctypes.byref(items)), "读取选中文件")
            paths = api.paths_from_array(items)
        finally:
            _release(items)
        if not paths:
            raise NativeDialogError("文件选择窗口未返回选中的文件。")
        return ImportSelection(paths[0].parent, paths)
    finally:
        if advised:
            _call(dialog, 8, (_DWORD,), cookie.value)
        _release(customize)
        _release(dialog)
        # Keep both event interfaces alive through all native releases above.
        if events is not None:
            events._release_ref(None)
        api.ole32.CoUninitialize()
