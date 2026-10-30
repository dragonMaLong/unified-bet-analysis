import ctypes
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tristar_bet.ui import native_import_picker as picker


class NativePickerContractTests(unittest.TestCase):
    def test_non_windows_call_fails_without_loading_native_libraries(self):
        with patch.object(picker.sys, "platform", "linux"):
            with self.assertRaises(picker.NativeDialogError):
                picker.choose_import_files(0, Path.cwd(), (".smp",))

    def test_failed_com_initialization_is_not_uninitialized(self):
        api = Mock()
        api.ole32.CoInitializeEx.return_value = ctypes.c_int32(0x80010106).value
        with patch.object(picker, "_WindowsApi", return_value=api):
            with self.assertRaises(picker.NativeDialogError) as raised:
                picker.choose_import_files(0, Path.cwd(), (".smp",))
        self.assertEqual(raised.exception.hresult, 0x80010106)
        api.ole32.CoUninitialize.assert_not_called()

    def test_event_interfaces_share_one_com_identity(self):
        events = picker._DialogEvents(Mock(), None, None)
        controls = picker._VOID()
        result = picker._call(events.events_pointer, 0,
                              (ctypes.POINTER(picker._GUID), picker._PPVOID),
                              ctypes.byref(picker._IID_CONTROL_EVENTS), ctypes.byref(controls))
        self.assertEqual(result, 0)
        self.assertEqual(controls.value, events.controls_pointer.value)
        identity = picker._VOID()
        result = picker._call(controls, 0,
                              (ctypes.POINTER(picker._GUID), picker._PPVOID),
                              ctypes.byref(picker._IID_UNKNOWN), ctypes.byref(identity))
        self.assertEqual(result, 0)
        self.assertEqual(identity.value, events.events_pointer.value)
        self.assertEqual(events.references, 3)
        picker._release(identity)
        picker._release(controls)
        self.assertEqual(events.references, 1)

    def test_callback_keeps_original_error_if_close_also_fails(self):
        events = picker._DialogEvents(Mock(), None, None)
        error = RuntimeError("selection failed")
        with patch.object(picker, "_call", side_effect=RuntimeError("close failed")):
            self.assertEqual(events._fail(error), picker._E_FAIL)
        self.assertIs(events.error, error)


class NativePickerColumnTests(unittest.TestCase):
    def setUp(self):
        self.name = picker._PKEY_NAME
        self.kind = picker._PKEY_TYPE
        self.description = picker._PropertyKey(picker._GUID.parse("1edd7de2-a911-4699-941a-ea826afcaf72"), 4)
        self.status = picker._PropertyKey(picker._GUID.parse("d73652b0-49bc-4cd1-99be-8334b872a8c6"), 3)
        self.modified = picker._PropertyKey(self.name.fmtid, 14)

    def test_type_is_added_after_name_without_losing_vendor_columns(self):
        original = (self.name, self.description, self.status, self.modified)
        result = picker._with_type_column(original)
        self.assertEqual(tuple(map(bytes, result)), tuple(map(bytes, (
            self.name, self.kind, self.description, self.status, self.modified))))

    def test_existing_type_is_moved_and_deduplicated(self):
        original = (self.name, self.description, self.kind, self.status, self.kind)
        result = picker._with_type_column(original)
        self.assertEqual(tuple(map(bytes, result)), tuple(map(bytes, (
            self.name, self.kind, self.description, self.status))))

    def test_special_view_without_name_keeps_its_schema(self):
        original = (self.description, self.status)
        self.assertEqual(tuple(map(bytes, picker._with_type_column(original))), tuple(map(bytes, original)))

    def _column_store(self, original, fail_read=False):
        """Model the documented SetColumns state reset, not just its output."""
        store = {"keys": tuple(original), "calls": [], "released": False}
        store["info"] = {bytes(key): (220 + index * 37, 1 | (2 if index == 0 else 0))
                         for index, key in enumerate(original)}

        def call(_pointer, index, _argument_types=(), *args, **_kwargs):
            store["calls"].append(index)
            if index == 5:
                ctypes.cast(args[1], ctypes.POINTER(picker._DWORD))[0] = len(store["keys"])
            elif index == 6:
                for offset, key in enumerate(store["keys"]):
                    args[1][offset] = key
            elif index == 4:
                if fail_read:
                    return picker._E_FAIL
                key = ctypes.cast(args[0], ctypes.POINTER(picker._PropertyKey)).contents
                info = ctypes.cast(args[1], ctypes.POINTER(picker._ColumnInfo)).contents
                self.assertEqual(info.mask, 0x11)
                self.assertEqual(info.size, ctypes.sizeof(picker._ColumnInfo))
                info.width, info.state = store["info"][bytes(key)]
            elif index == 7:
                store["keys"] = tuple(picker._PropertyKey.from_buffer_copy(bytes(args[0][i])) for i in range(args[1]))
                store["info"] = {bytes(key): (100, 1) for key in store["keys"]}
            elif index == 3:
                key = ctypes.cast(args[0], ctypes.POINTER(picker._PropertyKey)).contents
                info = ctypes.cast(args[1], ctypes.POINTER(picker._ColumnInfo)).contents
                store["info"][bytes(key)] = (info.width, info.state)
            elif index == 2:
                store["released"] = True
            else:
                raise AssertionError(f"Unexpected column method {index}")
            return 0

        return store, call

    def test_native_column_update_restores_existing_width_and_state(self):
        original = (self.name, self.description, self.status, self.modified)
        store, call = self._column_store(original)
        saved_info = dict(store["info"])
        with patch.object(picker, "_column_manager", return_value=picker._VOID(1)), patch.object(picker, "_call", side_effect=call):
            self.assertTrue(picker._ensure_type_column(None))
        self.assertTrue(store["released"])
        self.assertEqual([bytes(k) for k in store["keys"]],
                         [bytes(k) for k in (self.name, self.kind, self.description, self.status, self.modified)])
        self.assertTrue(all(store["info"][key] == value for key, value in saved_info.items()))

    def test_reapplying_correct_columns_does_not_reset_any_state(self):
        store, call = self._column_store((self.name, self.kind, self.description, self.status))
        saved_info = dict(store["info"])
        with patch.object(picker, "_column_manager", return_value=picker._VOID(1)), patch.object(picker, "_call", side_effect=call):
            self.assertTrue(picker._ensure_type_column(None))
        self.assertNotIn(7, store["calls"])
        self.assertEqual(store["info"], saved_info)

    def test_unreadable_column_settings_leave_columns_unchanged(self):
        original = (self.name, self.description, self.status)
        store, call = self._column_store(original, fail_read=True)
        with patch.object(picker, "_column_manager", return_value=picker._VOID(1)), patch.object(picker, "_call", side_effect=call):
            self.assertFalse(picker._ensure_type_column(None))
        self.assertNotIn(7, store["calls"])
        self.assertTrue(store["released"])
        self.assertEqual(tuple(map(bytes, store["keys"])), tuple(map(bytes, original)))

    def test_missing_column_interface_does_not_block_folder_change(self):
        events = picker._DialogEvents(Mock(), None, None)
        events._update_folder_button = Mock(return_value=0)
        with patch.object(picker, "_column_manager", side_effect=picker.NativeDialogError("not supported")):
            self.assertEqual(events._folder_changed(), 0)
        events._update_folder_button.assert_called_once_with()
        self.assertIsNone(events.error)


@unittest.skipUnless(sys.platform == "win32", "Uses Windows COM without displaying UI")
class NativePickerWindowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.file = self.directory / "含中文的样品.SMP"
        self.file.write_text("native picker fixture", encoding="utf-8")
        self.subdirectory = self.directory / "子文件夹"
        self.subdirectory.mkdir()

    def _run_dialog(self, on_show, *, result_files=None):
        """Exercise real COM configuration/ownership; replace only modal Show.

        File acceptance uses a real IShellItemArray too. Folder acceptance calls
        the real ctypes event vtable and only substitutes Close, since no UI is
        displayed. These checks never interact with the user's running apps.
        """
        original_call = picker._call
        original_events = picker._DialogEvents
        captured = []
        calls = []

        def events_factory(*args):
            events = original_events(*args)
            captured.append(events)
            return events

        def call(pointer, index, argument_types=(), *args, **kwargs):
            calls.append(index)
            if index == 3:
                return on_show(captured[-1], original_call)
            if index == 23:
                return 0
            if index == 27 and result_files is not None:
                api = captured[-1].api
                item = api.shell_item(result_files[0])
                array_iid = picker._GUID.parse("b63ea76d-1f85-456f-a19c-48159efa858b")
                create_array = api.shell32.SHCreateShellItemArrayFromShellItem
                create_array.argtypes = [picker._VOID, ctypes.POINTER(picker._GUID), picker._PPVOID]
                create_array.restype = picker._HRESULT
                try:
                    return create_array(item, ctypes.byref(array_iid), args[0])
                finally:
                    picker._release(item)
            return original_call(pointer, index, argument_types, *args, **kwargs)

        with patch.object(picker, "_DialogEvents", side_effect=events_factory), patch.object(picker, "_call", side_effect=call):
            result = picker.choose_import_files(0, self.directory, (".smp", ".raw"))
        self.assertEqual(captured[-1].references, 0, "Unadvise/Release must relinquish both event interfaces")
        return result, captured[-1], calls

    def test_real_com_configuration_and_cancel_release_event_references(self):
        result, _, calls = self._run_dialog(lambda *_: picker._HRESULT(picker._CANCELLED).value)
        self.assertIsNone(result)
        self.assertNotIn(27, calls)

    def test_folder_button_returns_current_directory_without_getresults(self):
        def show(events, native_call):
            return native_call(events.controls_pointer, 4, (picker._VOID, picker._DWORD), events.customize, picker._FOLDER_BUTTON)

        result, _, calls = self._run_dialog(show)
        self.assertEqual(result, picker.ImportSelection(self.directory))
        self.assertNotIn(27, calls, "Close(S_OK) has no GetResults result")

    def test_folder_button_uses_single_highlighted_subfolder(self):
        def show(events, native_call):
            events._selected_paths = lambda: (self.subdirectory,)
            return native_call(events.controls_pointer, 4, (picker._VOID, picker._DWORD), events.customize, picker._FOLDER_BUTTON)

        result, _, _ = self._run_dialog(show)
        self.assertEqual(result, picker.ImportSelection(self.subdirectory))

    def test_file_selection_does_not_turn_into_folder_confirmation(self):
        result, _, calls = self._run_dialog(lambda *_: 0, result_files=(self.file,))
        self.assertEqual(result, picker.ImportSelection(self.directory, (self.file,)))
        self.assertIn(27, calls)

    def test_native_failure_is_not_reported_as_cancel(self):
        with self.assertRaises(picker.NativeDialogError) as raised:
            self._run_dialog(lambda *_: picker._E_FAIL)
        self.assertEqual(raised.exception.hresult, 0x80004005)

    def test_nested_sta_initialization_keeps_callers_com_reference(self):
        api = picker._WindowsApi()
        picker._check(api.ole32.CoInitializeEx(None, 2), "test initialization")
        try:
            result, _, _ = self._run_dialog(lambda *_: picker._HRESULT(picker._CANCELLED).value)
            self.assertIsNone(result)
            token = ctypes.c_size_t()
            get_context = api.ole32.CoGetContextToken
            get_context.argtypes = [ctypes.POINTER(ctypes.c_size_t)]
            get_context.restype = picker._HRESULT
            self.assertEqual(get_context(ctypes.byref(token)), 0)
        finally:
            api.ole32.CoUninitialize()


if __name__ == "__main__":
    unittest.main()
