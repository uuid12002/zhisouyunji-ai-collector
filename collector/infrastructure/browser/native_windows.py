"""Windows SendInput implementation used by the installed product, not a service API.

No input is sent unless the bound browser is foreground and the desktop is unlocked.
Coordinates are derived from its visible renderer HWND, never from fixed offsets.
"""

from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import os
import threading

from collector.domain.errors import CollectionError


_DESKTOP_LOCK = threading.Lock()


class MouseInput(C.Structure):
    _fields_ = [
        ('dx', W.LONG),
        ('dy', W.LONG),
        ('mouseData', W.DWORD),
        ('dwFlags', W.DWORD),
        ('time', W.DWORD),
        ('dwExtraInfo', C.c_size_t),
    ]


class KeyInput(C.Structure):
    _fields_ = [
        ('wVk', W.WORD),
        ('wScan', W.WORD),
        ('dwFlags', W.DWORD),
        ('time', W.DWORD),
        ('dwExtraInfo', C.c_size_t),
    ]


class HardwareInput(C.Structure):
    _fields_ = [('uMsg', W.DWORD), ('wParamL', W.WORD), ('wParamH', W.WORD)]


class InputUnion(C.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyInput), ('hi', HardwareInput)]


class Input(C.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', W.DWORD), ('data', InputUnion)]


def key_input(key=0, scan=0, flags=0):
    return Input(type=1, ki=KeyInput(key, scan, flags, 0, 0))


def unicode_inputs(text):
    raw = text.encode('utf-16-le')
    events = []
    for index in range(0, len(raw), 2):
        unit = int.from_bytes(raw[index : index + 2], 'little')
        events.extend((key_input(scan=unit, flags=4), key_input(scan=unit, flags=6)))
    return events


class WindowsDesktop:
    def __init__(self):
        if os.name != 'nt':
            raise CollectionError('RPA_UNSUPPORTED', '桌面 RPA 需要 Windows 交互式桌面。')
        self.api = C.WinDLL('user32', use_last_error=True)
        self.callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        signatures = {
            'EnumWindows': ([self.callback_type, W.LPARAM], W.BOOL),
            'EnumChildWindows': ([W.HWND, self.callback_type, W.LPARAM], W.BOOL),
            'GetForegroundWindow': ([], W.HWND),
            'SetForegroundWindow': ([W.HWND], W.BOOL),
            'GetAncestor': ([W.HWND, W.UINT], W.HWND),
            'GetClassNameW': ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
            'GetWindowTextW': ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
            'GetWindowThreadProcessId': ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            'IsWindowVisible': ([W.HWND], W.BOOL),
            'IsWindow': ([W.HWND], W.BOOL),
            'IsIconic': ([W.HWND], W.BOOL),
            'ShowWindow': ([W.HWND, C.c_int], W.BOOL),
            'GetClientRect': ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
            'ClientToScreen': ([W.HWND, C.POINTER(W.POINT)], W.BOOL),
            'WindowFromPoint': ([W.POINT], W.HWND),
            'GetSystemMetrics': ([C.c_int], C.c_int),
            'GetAsyncKeyState': ([C.c_int], W.SHORT),
            'SendInput': ([W.UINT, C.POINTER(Input), C.c_int], W.UINT),
            'SetThreadDpiAwarenessContext': ([W.HANDLE], W.HANDLE),
            'OpenInputDesktop': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'GetUserObjectInformationW': (
                [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)],
                W.BOOL,
            ),
            'CloseDesktop': ([W.HANDLE], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]
        self.kernel.CreateMutexW.restype = W.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        self.kernel.WaitForSingleObject.restype = W.DWORD
        self.kernel.ReleaseMutex.argtypes = [W.HANDLE]
        self.kernel.ReleaseMutex.restype = W.BOOL
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.CloseHandle.restype = W.BOOL
        self.mutex = None
        self.hwnd = None
        self.pid = None
        self.sent_events = 0

    def text(self, hwnd, class_name=False):
        buffer = C.create_unicode_buffer(2048)
        function = self.api.GetClassNameW if class_name else self.api.GetWindowTextW
        function(hwnd, buffer, len(buffer))
        return buffer.value

    def process_id(self, hwnd):
        pid = W.DWORD()
        self.api.GetWindowThreadProcessId(hwnd, C.byref(pid))
        return pid.value

    def ensure_desktop(self):
        desktop = self.api.OpenInputDesktop(0, False, 1)
        if not desktop:
            raise CollectionError(
                'RPA_DESKTOP_UNAVAILABLE', '桌面不可操作，请解锁并保持远程桌面连接。'
            )
        try:
            name = C.create_unicode_buffer(256)
            needed = W.DWORD()
            if (
                not self.api.GetUserObjectInformationW(
                    desktop, 2, name, C.sizeof(name), C.byref(needed)
                )
                or name.value != 'Default'
            ):
                raise CollectionError(
                    'RPA_DESKTOP_UNAVAILABLE', '当前不是普通交互桌面，已停止输入。'
                )
        finally:
            self.api.CloseDesktop(desktop)

    def close(self):
        if self.mutex:
            try:
                self.kernel.ReleaseMutex(self.mutex)
                self.kernel.CloseHandle(self.mutex)
            finally:
                self.mutex = None
                _DESKTOP_LOCK.release()

    def acquire_desktop(self):
        if self.mutex:
            return
        # A Windows mutex is recursive on the same thread. Also guard independent
        # async tasks in this process, then acquire the cross-process mutex.
        if not _DESKTOP_LOCK.acquire(blocking=False):
            raise CollectionError(
                'RPA_DESKTOP_BUSY', '另一个桌面 RPA 任务正在运行，未操作鼠标键盘。'
            )
        try:
            handle = self.kernel.CreateMutexW(None, False, 'Local\\WebCollector.DesktopRPA')
            if not handle:
                raise CollectionError('RPA_DESKTOP_BUSY', '无法独占桌面输入，已停止。')
            if self.kernel.WaitForSingleObject(handle, 0) not in (0, 0x80):
                self.kernel.CloseHandle(handle)
                raise CollectionError(
                    'RPA_DESKTOP_BUSY', '另一个桌面 RPA 任务正在运行，未操作鼠标键盘。'
                )
            self.mutex = handle
        except BaseException:
            _DESKTOP_LOCK.release()
            raise

    def bind(self, title_token):
        self.ensure_desktop()
        self.acquire_desktop()
        matches = []

        @self.callback_type
        def visit(hwnd, _):
            if (
                self.api.IsWindowVisible(hwnd)
                and self.text(hwnd, True) == 'Chrome_WidgetWin_1'
                and title_token in self.text(hwnd)
            ):
                matches.append(hwnd)
            return True

        self.api.EnumWindows(visit, 0)
        if len(matches) != 1:
            raise CollectionError(
                'RPA_WINDOW_AMBIGUOUS', '无法唯一识别本次专用浏览器窗口，未进行桌面输入。'
            )
        self.hwnd = matches[0]
        self.pid = self.process_id(self.hwnd)
        # Activation is allowed once when a new task binds its own window.
        self.api.ShowWindow(self.hwnd, 3)
        self.api.SetForegroundWindow(self.hwnd)

    def is_foreground(self):
        return self.api.GetForegroundWindow() == self.hwnd and not self.api.IsIconic(self.hwnd)

    def ensure_target(self):
        self.ensure_desktop()
        if (
            not self.hwnd
            or not self.api.IsWindow(self.hwnd)
            or self.process_id(self.hwnd) != self.pid
        ):
            raise CollectionError('RPA_WINDOW_LOST', '专用浏览器窗口已变化，停止输入。')
        if self.api.GetForegroundWindow() != self.hwnd or self.api.IsIconic(self.hwnd):
            raise CollectionError(
                'RPA_FOCUS_LOST',
                '桌面 RPA 的浏览器失去前台焦点，已停止；请核对原会话，避免重复发送。',
            )
        if self.api.GetAsyncKeyState(0x1B) & 0x8000:
            raise CollectionError('RPA_CANCELLED', '用户按下 Esc，已停止桌面输入。')
        if any(self.api.GetAsyncKeyState(k) & 0x8000 for k in (0x01, 0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise CollectionError('RPA_INPUT_BUSY', '鼠标或修饰键正被按住，已停止桌面输入。')

    @contextmanager
    def physical_coordinates(self):
        previous = self.api.SetThreadDpiAwarenessContext(W.HANDLE(-4))
        if not previous:
            raise CollectionError('RPA_COORDINATES', '无法校准屏幕缩放，停止点击。')
        try:
            yield
        finally:
            self.api.SetThreadDpiAwarenessContext(previous)

    def renderer_rect(self):
        self.ensure_target()
        rectangles = []
        with self.physical_coordinates():

            @self.callback_type
            def visit(hwnd, _):
                if (
                    self.api.IsWindowVisible(hwnd)
                    and self.text(hwnd, True) == 'Chrome_RenderWidgetHostHWND'
                ):
                    rect, origin = W.RECT(), W.POINT(0, 0)
                    if (
                        self.api.GetClientRect(hwnd, C.byref(rect))
                        and self.api.ClientToScreen(hwnd, C.byref(origin))
                        and rect.right > 100
                        and rect.bottom > 100
                    ):
                        rectangles.append((origin.x, origin.y, rect.right, rect.bottom))
                return True

            self.api.EnumChildWindows(self.hwnd, visit, 0)
        if len(rectangles) != 1:
            raise CollectionError(
                'RPA_COORDINATES', '浏览器内容区不唯一，停止点击；请关闭开发者工具或浮动面板。'
            )
        return rectangles[0]

    def emit(self, events):
        self.ensure_target()
        values = (Input * len(events))(*events)
        count = self.api.SendInput(len(events), values, C.sizeof(Input))
        self.sent_events += count
        if count != len(events):
            # Release only buttons/keys this batch could have pressed. Never retry
            # an uncertain click or key-down, and never leave a modifier held.
            releases = []
            for event in events:
                if event.type == 1 and not event.ki.dwFlags & 2:
                    releases.append(key_input(event.ki.wVk, event.ki.wScan, event.ki.dwFlags | 2))
                elif event.type == 0 and event.mi.dwFlags & 2:
                    releases.append(Input(type=0, mi=MouseInput(0, 0, 0, 4, 0, 0)))
            if releases:
                cleanup = (Input * len(releases))(*releases)
                self.api.SendInput(len(releases), cleanup, C.sizeof(Input))
            raise CollectionError(
                'RPA_INPUT_BLOCKED', '系统未完整接受桌面输入，已停止；请检查原会话，不会自动重试。'
            )

    def click(self, x, y):
        self.ensure_target()
        with self.physical_coordinates():
            at = self.api.WindowFromPoint(W.POINT(round(x), round(y)))
            if self.api.GetAncestor(at, 2) != self.hwnd:
                raise CollectionError('RPA_OCCLUDED', '目标位置被其他窗口遮挡，未点击。')
            left, top, width, height = [self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
            if (
                width <= 1
                or height <= 1
                or not (left <= x < left + width and top <= y < top + height)
            ):
                raise CollectionError('RPA_COORDINATES', '目标不在可用屏幕内，未点击。')
            dx, dy = (
                round((x - left) * 65535 / (width - 1)),
                round((y - top) * 65535 / (height - 1)),
            )
            self.emit(
                [
                    Input(type=0, mi=MouseInput(dx, dy, 0, flags, 0, 0))
                    for flags in (0xC001, 0x0002, 0x0004)
                ]
            )

    def chord(self, *keys):
        self.emit([key_input(k) for k in keys] + [key_input(k, flags=2) for k in reversed(keys)])

    def type_text(self, text):
        # A short batch limits the time between foreground checks; do not randomize.
        for index in range(0, len(text), 16):
            self.emit(unicode_inputs(text[index : index + 16]))
