"""Slay AI 桌面悬浮助手

真正的桌面级悬浮球（非网页）：
- 悬浮球：无边框、置顶、透明异形圆形窗口，可拖动，单击触发 /assist/stream
- 悬浮窗：无边框置顶窗口，展示 SSE 流式建议，可拖动
- 新请求清空旧内容；分析进度会显示在窗口中；支持手动关闭
- 右键悬浮球可退出程序

运行：
    .venv/Scripts/python.exe frontend/overlay.py

注意：不要在启动时自动请求接口，只有点击悬浮球才会调用 /assist/stream
（后端会真实截图并按 D / 滚轮 / Esc）。
"""

from __future__ import annotations

import ctypes
import json
import queue
import threading
import tkinter as tk
from pathlib import Path

import requests

API_BASE = "http://127.0.0.1:8000"
CLICK_THRESHOLD_PX = 5
POSITIONS_FILE = Path(__file__).with_name("overlay_positions.json")

# 透明关键色（球窗口上凡为此色的像素都会变透明）
TRANSPARENT = "#010101"

COLOR_BG = "#1e2129"
COLOR_BORDER = "#3a3f4d"
COLOR_TEXT = "#e8eaf0"
COLOR_DIM = "#9aa0ae"
COLOR_ACCENT = "#6c8cff"
COLOR_ACCENT2 = "#9a6cff"
COLOR_ERROR = "#ff6c6c"
COLOR_OK = "#7dff9a"
COLOR_WARN = "#ffc86c"
COLOR_INFO = "#6cd4ff"

STATUS_DOT = {
    "idle": COLOR_DIM,
    "connecting": COLOR_WARN,
    "collecting": COLOR_INFO,
    "answering": COLOR_OK,
    "done": COLOR_OK,
    "error": COLOR_ERROR,
}

STATUS_TEXT = {
    "idle": "",
    "connecting": "正在连接后端...",
    "collecting": "正在分析局面...",
    "answering": "正在生成建议...",
    "done": "",
    "error": "连接失败",
}


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def load_positions() -> dict:
    try:
        return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_positions(pos: dict) -> None:
    try:
        POSITIONS_FILE.write_text(
            json.dumps(pos, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def clamp(x: int, y: int, w: int, h: int, root: tk.Tk) -> tuple[int, int]:
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    return max(0, min(x, sw - w)), max(0, min(y, sh - h))


def make_draggable(
    widget: tk.Misc,
    target: tk.Toplevel,
    root: tk.Tk,
    on_move_end=None,
    on_tap=None,
) -> None:
    """让 target 窗口可通过拖动 widget 移动；无位移的点击触发 on_tap。"""
    state = {"dx": 0, "dy": 0, "sx": 0, "sy": 0, "moved": False}

    def on_press(e):
        state["dx"] = e.x_root - target.winfo_x()
        state["dy"] = e.y_root - target.winfo_y()
        state["sx"], state["sy"] = e.x_root, e.y_root
        state["moved"] = False

    def on_drag(e):
        nx, ny = e.x_root - state["dx"], e.y_root - state["dy"]
        if not state["moved"]:
            if (
                abs(e.x_root - state["sx"]) < CLICK_THRESHOLD_PX
                and abs(e.y_root - state["sy"]) < CLICK_THRESHOLD_PX
            ):
                return
            state["moved"] = True
        target.geometry(f"+{nx}+{ny}")

    def on_release(_e):
        if state["moved"]:
            x, y = clamp(
                target.winfo_x(), target.winfo_y(),
                target.winfo_width(), target.winfo_height(), root,
            )
            target.geometry(f"+{x}+{y}")
            if on_move_end:
                on_move_end(x, y)
        elif on_tap:
            on_tap()

    widget.bind("<ButtonPress-1>", on_press)
    widget.bind("<B1-Motion>", on_drag)
    widget.bind("<ButtonRelease-1>", on_release)


class SseClient:
    """后台线程读取 /assist/stream 的 SSE 流，事件经队列回主线程。"""

    def __init__(self, events: queue.Queue):
        self.events = events
        self._stop = threading.Event()
        self._resp: requests.Response | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, cancel_backend: bool = True) -> None:
        self._stop.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass
        if cancel_backend:
            threading.Thread(target=self._send_cancel, daemon=True).start()

    @staticmethod
    def _send_cancel() -> None:
        try:
            requests.post(f"{API_BASE}/assist/cancel", timeout=1)
        except Exception:
            pass

    @staticmethod
    def _strip_one(s: str) -> str:
        return s[1:] if s.startswith(" ") else s

    def _run(self) -> None:
        try:
            with requests.get(
                f"{API_BASE}/assist/stream",
                stream=True,
                timeout=(5, None),
                headers={"Accept": "text/event-stream"},
            ) as resp:
                resp.raise_for_status()
                resp.encoding = "utf-8"
                self._resp = resp

                event, data_lines = None, []
                for line in resp.iter_lines(decode_unicode=True):
                    if self._stop.is_set():
                        return
                    if line is None:
                        continue
                    if line == "":
                        if event and data_lines:
                            self.events.put((event, "\n".join(data_lines)))
                        event, data_lines = None, []
                    elif line.startswith(":"):
                        continue
                    elif line.startswith("event:"):
                        event = self._strip_one(line[6:])
                    elif line.startswith("data:"):
                        data_lines.append(self._strip_one(line[5:]))
                # 流正常结束但服务端没发 done 事件时，补一个
                if not self._stop.is_set():
                    self.events.put(("done", "[DONE]"))
        except Exception as exc:
            if not self._stop.is_set():
                self.events.put(("error", str(exc)))


class FloatingBall:
    SIZE = 60

    def __init__(self, root: tk.Tk, app: "OverlayApp"):
        self.app = app
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", TRANSPARENT)
        self.win.configure(bg=TRANSPARENT)

        self.canvas = tk.Canvas(
            self.win, width=self.SIZE, height=self.SIZE,
            bg=TRANSPARENT, highlightthickness=0, cursor="hand2",
        )
        self.canvas.pack()
        s = self.SIZE
        self.ring = self.canvas.create_oval(
            4, 4, s - 4, s - 4, fill=COLOR_ACCENT, outline=COLOR_ACCENT2, width=3
        )
        self.canvas.create_oval(
            12, 10, s - 22, s - 30, fill="#8aa2ff", outline=""
        )  # 高光
        self.canvas.create_text(
            s // 2, s // 2 + 2, text="AI", fill="white",
            font=("Segoe UI", 15, "bold"),
        )

        self._busy = False
        self._pulse_on = False

        make_draggable(
            self.canvas, self.win, root,
            on_move_end=lambda x, y: app.save_position("ball", x, y),
            on_tap=app.start_assist,
        )

        menu = tk.Menu(self.win, tearoff=0)
        menu.add_command(label="退出", command=app.quit)
        self.canvas.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

        p = app.positions.get("ball")
        if p:
            x, y = clamp(p["x"], p["y"], self.SIZE, self.SIZE, root)
        else:
            x = root.winfo_screenwidth() - self.SIZE - 30
            y = root.winfo_screenheight() // 2 - self.SIZE // 2
        self.win.geometry(f"{self.SIZE}x{self.SIZE}+{x}+{y}")

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if not busy:
            self.canvas.itemconfigure(self.ring, outline=COLOR_ACCENT2, width=3)

    def pulse(self) -> None:
        if self._busy:
            self._pulse_on = not self._pulse_on
            color = COLOR_OK if self._pulse_on else COLOR_ACCENT2
            self.canvas.itemconfigure(self.ring, outline=color, width=4)


class AssistWindow:
    WIDTH = 380

    def __init__(self, root: tk.Tk, app: "OverlayApp"):
        self.app = app
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=COLOR_BORDER)
        self.win.withdraw()

        outer = tk.Frame(self.win, bg=COLOR_BORDER, padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=COLOR_BG)
        inner.pack(fill="both", expand=True)

        # ---- 标题栏（拖动把手）----
        bar = tk.Frame(inner, bg="#262a35")
        bar.pack(fill="x")

        self.dot = tk.Canvas(bar, width=12, height=12, bg="#262a35",
                             highlightthickness=0)
        self.dot_item = self.dot.create_oval(2, 2, 10, 10, fill=COLOR_DIM, outline="")
        self.dot.pack(side="left", padx=(10, 6), pady=8)

        tk.Label(bar, text="Slay AI 助手", bg="#262a35", fg=COLOR_TEXT,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")

        self.scene_badge = tk.Label(
            bar, text="", bg="#262a35", fg=COLOR_ACCENT,
            font=("Microsoft YaHei UI", 8),
        )
        self.scene_badge.pack(side="left", padx=8)

        close_lbl = tk.Label(
            bar, text="✕", bg="#262a35", fg=COLOR_DIM,
            font=("Microsoft YaHei UI", 10), padx=8, cursor="hand2",
        )
        close_lbl.pack(side="right", padx=(0, 4))
        close_lbl.bind("<Button-1>", lambda _e: app.close_window())
        close_lbl.bind("<Enter>", lambda _e: close_lbl.configure(fg=COLOR_ERROR))
        close_lbl.bind("<Leave>", lambda _e: close_lbl.configure(fg=COLOR_DIM))

        for w in (bar, self.dot):
            make_draggable(
                w, self.win, root,
                on_move_end=lambda x, y: app.save_position("window", x, y),
            )
        # 标题文字标签也作为拖动把手
        for child in bar.winfo_children():
            if isinstance(child, tk.Label) and child is not close_lbl:
                make_draggable(
                    child, self.win, root,
                    on_move_end=lambda x, y: app.save_position("window", x, y),
                )

        # ---- 状态行 ----
        self.status_line = tk.Label(
            inner, text="", bg=COLOR_BG, fg=COLOR_DIM, anchor="w",
            font=("Microsoft YaHei UI", 8, "italic"),
        )
        self.status_line.pack(fill="x", padx=12, pady=(6, 0))

        # ---- 正文（流式输出）----
        self.text = tk.Text(
            inner, width=44, height=12, wrap="word",
            bg=COLOR_BG, fg=COLOR_TEXT, insertbackground=COLOR_TEXT,
            font=("Microsoft YaHei UI", 10), relief="flat",
            padx=10, pady=8, spacing1=2, spacing3=2,
            state="disabled", cursor="arrow",
        )
        self.text.pack(fill="both", expand=True, padx=2, pady=(2, 2))
        self.text.bind("<MouseWheel>",
                       lambda e: self.text.yview_scroll(-1 * (e.delta // 120), "units"))

        self._cursor_visible = False

    # ---- 内容操作 ----
    def set_content(self, s: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if s:
            self.text.insert("1.0", s)
        if self._cursor_visible:
            self.text.insert("end", "▍")
        self.text.configure(state="disabled")
        self.text.see("end")

    def append_content(self, s: str) -> None:
        self.text.configure(state="normal")
        if self._cursor_visible:
            self.text.delete("end-2c", "end-1c")
        self.text.insert("end", s)
        if self._cursor_visible:
            self.text.insert("end", "▍")
        self.text.configure(state="disabled")
        self.text.see("end")

    def set_cursor(self, on: bool) -> None:
        if on == self._cursor_visible:
            return
        self._cursor_visible = on
        self.text.configure(state="normal")
        if on:
            self.text.insert("end", "▍")
        else:
            content = self.text.get("1.0", "end-1c")
            if content.endswith("▍"):
                self.text.delete("end-2c", "end-1c")
        self.text.configure(state="disabled")

    def set_status_line(self, s: str) -> None:
        self.status_line.configure(text=s)

    def set_dot(self, status: str) -> None:
        self.dot.itemconfigure(self.dot_item, fill=STATUS_DOT.get(status, COLOR_DIM))

    def set_scene(self, scene: str) -> None:
        self.scene_badge.configure(text=scene)

    def show(self, near: tuple[int, int] | None = None) -> None:
        p = self.app.positions.get("window")
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        h = self.win.winfo_reqheight()
        root = self.win
        if p:
            x, y = clamp(p["x"], p["y"], w, h, root)
        elif near:
            bx, by = near
            x, y = clamp(bx - w - 16, by - h // 2, w, h, root)
        else:
            x, y = clamp(100, 100, w, h, root)
        self.win.geometry(f"+{x}+{y}")
        self.win.deiconify()
        self.win.lift()

    def hide(self) -> None:
        self.win.withdraw()


class OverlayApp:
    """主控制器：状态机 + 事件分发。"""

    def __init__(self) -> None:
        enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()

        self.positions = load_positions()
        self.events: queue.Queue = queue.Queue()
        self.client: SseClient | None = None
        self.status = "idle"
        self.has_token = False

        self.ball = FloatingBall(self.root, self)
        self.window = AssistWindow(self.root, self)

        self.root.after(50, self._poll_events)
        self.root.after(500, self._pulse_ball)

    # ---- 持久化 ----
    def save_position(self, key: str, x: int, y: int) -> None:
        self.positions[key] = {"x": x, "y": y}
        save_positions(self.positions)

    # ---- 状态机 ----
    def _set_status(self, status: str) -> None:
        self.status = status
        self.window.set_dot(status)
        self.window.set_status_line(STATUS_TEXT.get(status, ""))
        self.ball.set_busy(status in ("connecting", "collecting", "answering"))

    def start_assist(self) -> None:
        """点击悬浮球：开始新请求（旧请求先关闭），清空旧建议。"""
        if self.client is not None:
            self.client.stop(cancel_backend=True)
            self.client = None

        self.has_token = False
        self.ball.win.update_idletasks()
        near = (self.ball.win.winfo_x(), self.ball.win.winfo_y())
        self.window.show(near=near)
        self.window.set_scene("")
        self.window.set_cursor(False)
        self.window.set_content("我先看一下当前局面...")
        self._set_status("connecting")

        self.client = SseClient(self.events)
        self.client.start()

    def close_window(self) -> None:
        """手动关闭窗口，并通知后端取消正在进行的分析。"""
        if self.client is not None:
            self.client.stop(cancel_backend=True)
            self.client = None
        self.window.hide()
        self._set_status("idle")

    def quit(self) -> None:
        if self.client is not None:
            self.client.stop(cancel_backend=True)
        self.root.destroy()

    # ---- SSE 事件处理 ----
    def _poll_events(self) -> None:
        try:
            while True:
                event, data = self.events.get_nowait()
                self._handle_event(event, data)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_events)

    def _handle_event(self, event: str, data: str) -> None:
        if self.status == "idle":
            return  # 窗口已关，丢弃迟到的事件

        if event == "status":
            self._set_status("collecting")
            if not self.has_token:
                self.window.set_status_line("")
                self.window.set_content(data)
            else:
                self.window.set_status_line(data)

        elif event == "scene":
            if self.status != "answering":
                self._set_status("collecting")
            self.window.set_scene(data)
            print(f"[SlayAI] scene: {data}")

        elif event == "state":
            try:
                print(f"[SlayAI] state: {json.loads(data)}")
            except Exception:
                print(f"[SlayAI] state(raw): {data}")

        elif event == "token":
            if not self.has_token:
                # 第一个 token：清空旧建议/占位内容，开始流式输出
                self.has_token = True
                self._set_status("answering")
                self.window.set_status_line("")
                self.window.set_content("")
                self.window.set_cursor(True)
            self.window.append_content(data)

        elif event == "done":
            if self.client is not None:
                self.client.stop(cancel_backend=False)
                self.client = None
            self.window.set_cursor(False)
            self._set_status("done")

        elif event == "error":
            if self.client is not None:
                self.client.stop(cancel_backend=False)
                self.client = None
            self.window.set_cursor(False)
            self._set_status("error")
            if not self.has_token:
                self.window.set_content("后端连接失败，稍后再试。")

    # ---- 悬浮球呼吸灯 ----
    def _pulse_ball(self) -> None:
        self.ball.pulse()
        self.root.after(500, self._pulse_ball)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    OverlayApp().run()
