"""Clickable desktop launcher for the resumable Py-Feat fatigue debugger.

Double-click ``启动微表情调试界面.bat`` in this folder.  This window only
collects settings and starts the audited command-line core in the background;
it does not implement a second, divergent analysis pipeline.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_DIR = Path(__file__).resolve().parent
CORE_SCRIPT = APP_DIR / "fatigue_pyfeat_debug.py"
CORE_MODE_FLAG = "--pyfeat-run-core"
IS_FROZEN = bool(getattr(sys, "frozen", False))
# A frozen application lives beside the user-visible EXE.  Source mode lives
# beside this .py file.  Neither path depends on a drive letter or username.
if IS_FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent


def build_core_command(core_arguments: list[str]) -> list[str]:
    """Create a path-independent child-process command for the analysis core.

    Source mode uses the same Python interpreter that opened the GUI.  A
    future PyInstaller build launches the same EXE in an explicit core mode,
    rather than trying to run an unavailable external .py script.
    """
    if IS_FROZEN:
        return [str(Path(sys.executable)), CORE_MODE_FLAG, *core_arguments]
    return [str(Path(sys.executable)), str(CORE_SCRIPT), *core_arguments]


def run_bundled_core() -> int:
    """Dispatch the PyInstaller child process to the bundled core module."""
    from fatigue_pyfeat_debug import main as core_main

    if len(sys.argv) < 2 or sys.argv[1] != CORE_MODE_FLAG:
        raise RuntimeError("未找到内置分析核心的启动标记。")
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    return core_main()


def default_input_root() -> Path:
    """Use the current common data-folder name when it exists; otherwise stay blank."""
    for name in ("正式实验数据", "测试实验数据"):
        candidate = APP_DIR.parent / name
        if candidate.is_dir():
            return candidate.resolve()
    return APP_DIR.parent


class DebuggerGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Py-Feat 疲劳视频调试与批处理")
        self.geometry("900x710")
        self.minsize(760, 560)

        self.input_root = tk.StringVar(value=str(default_input_root()))
        self.output_root = tk.StringVar(value=str((APP_DIR / "results").resolve()))
        self.device = tk.StringVar(value="cuda")
        self.sample_fps = tk.StringVar(value="12")
        self.output_profile = tk.StringVar(value="compact")
        self.batch_size = tk.StringVar(value="8")
        self.status = tk.StringVar(value="请选择文件夹后先运行“预检”。")
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.running = False
        self.task_started_at: float | None = None
        self.task_is_analysis = False
        self.latest_progress = ""

        self._build()
        self.after(100, self._drain_messages)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        container = ttk.Frame(self, padding=14)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(7, weight=1)

        ttk.Label(container, text="原始视频文件夹：").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(container, textvariable=self.input_root).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Button(container, text="选择…", command=self._choose_input).grid(row=0, column=2, padx=(8, 0), pady=5)

        ttk.Label(container, text="中间暂存／结果文件夹：").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(container, textvariable=self.output_root).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Button(container, text="选择…", command=self._choose_output).grid(row=1, column=2, padx=(8, 0), pady=5)

        options = ttk.LabelFrame(container, text="分析设置", padding=10)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 8))
        ttk.Label(options, text="设备：").grid(row=0, column=0, sticky="w")
        for column, (label, value) in enumerate((("GPU（推荐）", "cuda"), ("自动选择", "auto"), ("CPU", "cpu")), start=1):
            ttk.Radiobutton(options, text=label, variable=self.device, value=value).grid(row=0, column=column, padx=8)
        ttk.Label(options, text="抽样率（fps）：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(options, width=10, textvariable=self.sample_fps).grid(row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Label(options, text="默认 12；24 fps 视频会均匀每隔 2 个原始帧取 1 帧，可根据视频帧率自行调整。").grid(row=1, column=2, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(options, text="每批图片数：").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(options, width=10, textvariable=self.batch_size).grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Label(options, text="以RTX 3060 为例建议先用 8；显存不足时改为 4。").grid(row=2, column=2, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(options, text="输出字段：").grid(row=3, column=0, sticky="nw", pady=(10, 0))
        ttk.Radiobutton(
            options,
            text="精简指标（推荐，约 35 列）",
            variable=self.output_profile,
            value="compact",
        ).grid(row=3, column=1, sticky="w", pady=(10, 0))
        ttk.Radiobutton(
            options,
            text="完整 Py-Feat（约 1679 列；会产生大量数据）",
            variable=self.output_profile,
            value="full",
        ).grid(row=3, column=2, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            options,
            text="完整模式会保存三维网格、关键点、Blendshape 等高维字段，显著增加磁盘占用、Excel 写入和汇总时间",
            foreground="#a61c00",
            wraplength=740,
        ).grid(row=4, column=1, columnspan=3, sticky="w", pady=(2, 0))

        ttk.Label(
            container,
            text="文件名须为 受试者-时段-片段，例如 27-1-4.mp4。多批次分析时请始终选择同一个暂存/结果文件夹，完成后点“汇总已有暂存结果”，无需重新分析。",
            foreground="#555555",
            wraplength=780,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 8))

        controls = ttk.Frame(container)
        controls.grid(row=4, column=0, columnspan=3, sticky="w", pady=4)
        self.preflight_button = ttk.Button(controls, text="1. 预检（不分析）", command=lambda: self._start(dry_run=True))
        self.preflight_button.pack(side="left")
        self.run_button = ttk.Button(controls, text="2. 开始批量分析", command=lambda: self._start(dry_run=False))
        self.run_button.pack(side="left", padx=8)
        self.summary_button = ttk.Button(controls, text="3. 汇总已有暂存结果", command=self._start_summary)
        self.summary_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="停止当前任务（可断点续跑）", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(controls, text="打开结果文件夹", command=self._open_output)
        self.open_button.pack(side="left", padx=8)

        model_controls = ttk.LabelFrame(container, text="首次模型准备／无外网电脑", padding=8)
        model_controls.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(6, 4))
        self.prepare_models_button = ttk.Button(
            model_controls,
            text="准备／检查 Py-Feat 模型",
            command=self._start_prepare_models,
        )
        self.prepare_models_button.pack(side="left")
        self.export_models_button = ttk.Button(
            model_controls,
            text="导出离线模型包…",
            command=self._export_model_cache,
        )
        self.export_models_button.pack(side="left", padx=8)
        self.import_models_button = ttk.Button(
            model_controls,
            text="导入离线模型包…",
            command=self._import_model_cache,
        )
        self.import_models_button.pack(side="left")
        ttk.Label(
            model_controls,
            text="首次需联网下载；若工作站无法访问 Hugging Face，可在已成功运行的电脑导出 ZIP 后导入。",
            foreground="#555555",
            wraplength=430,
        ).pack(side="left", padx=(12, 0))

        ttk.Label(container, textvariable=self.status, foreground="#0b5394", wraplength=840).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 4))
        self.log = tk.Text(container, wrap="word", height=18, state="disabled", font=("Consolas", 10))
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        scroll = ttk.Scrollbar(container, orient="vertical", command=self.log.yview)
        scroll.grid(row=7, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        self._append("界面已就绪。建议先点“1. 预检（不分析）”。\n")

    def _choose_input(self) -> None:
        selected = filedialog.askdirectory(title="选择原始视频根文件夹", initialdir=self.input_root.get() or str(APP_DIR.parent))
        if selected:
            self.input_root.set(selected)

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="选择或新建结果文件夹", initialdir=self.output_root.get() or str(APP_DIR))
        if selected:
            self.output_root.set(selected)

    def _validate(self) -> tuple[Path, Path] | None:
        source = Path(self.input_root.get()).expanduser()
        output = Path(self.output_root.get()).expanduser()
        if not source.is_dir():
            messagebox.showerror("无法开始", "原始视频文件夹不存在，请重新选择。")
            return None
        if source.resolve() == output.resolve():
            messagebox.showerror("无法开始", "结果文件夹不能与原始视频文件夹相同。")
            return None
        try:
            if float(self.sample_fps.get()) <= 0 or int(self.batch_size.get()) <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("设置错误", "抽样率和每批图片数必须是正数。")
            return None
        return source.resolve(), output.resolve()

    def _validate_summary_root(self) -> Path | None:
        output = Path(self.output_root.get()).expanduser()
        if not output.is_dir():
            messagebox.showerror("无法汇总", "中间暂存文件夹不存在，请选择已经分析过的结果文件夹。")
            return None
        return output.resolve()

    def _start(self, dry_run: bool) -> None:
        if self.running:
            return
        paths = self._validate()
        if paths is None:
            return
        source, output = paths
        if not dry_run:
            profile_warning = ""
            if self.output_profile.get() == "full":
                profile_warning = (
                    "\n\n当前选择“完整 Py-Feat”：约 1679 列，会保存三维网格、关键点和 Blendshape，"
                    "显著增加磁盘空间、Excel 写入及汇总时间。"
                )
            confirmed = messagebox.askokcancel(
                "确认开始分析",
                "将以逐帧方式分析所选文件夹中的视频。已写入的检查点可在中断后续跑。"
                + profile_warning + "\n\n是否开始？",
            )
            if not confirmed:
                return
        core_arguments = [
            "--input-root", str(source),
            "--output-root", str(output),
            "--device", self.device.get(),
            "--sample-fps", self.sample_fps.get(),
            "--output-profile", self.output_profile.get(),
            "--batch-size", self.batch_size.get(),
        ]
        if dry_run:
            core_arguments.append("--dry-run")
        command = build_core_command(core_arguments)
        self._launch(command, "正在预检…" if dry_run else "正在分析…可用“停止当前任务”安全中止后续跑。")

    def _start_summary(self) -> None:
        if self.running:
            return
        output = self._validate_summary_root()
        if output is None:
            return
        confirmed = messagebox.askokcancel(
            "确认汇总",
            "会读取该中间暂存文件夹内所有已完成片段的 frame_features.xlsx，重建总 Excel 和统一时间轴。不会重新分析视频。\n\n是否开始？",
        )
        if not confirmed:
            return
        command = build_core_command(["--output-root", str(output), "--summarize-only"])
        self._launch(command, "正在读取已有暂存结果并生成总表…")

    def _start_prepare_models(self) -> None:
        if self.running:
            return
        confirmed = messagebox.askokcancel(
            "准备 Py-Feat 模型",
            "这一步不读取视频。首次运行会从 Py-Feat 的模型服务器下载人脸检测与面部特征模型，"
            "完成后通常不再需要联网。\n\n若当前电脑无法访问 Hugging Face，请在另一台已成功运行的电脑中导出离线模型包，再回到这里导入。\n\n现在开始吗？",
        )
        if confirmed:
            self._launch(
                build_core_command(["--prepare-models", "--device", self.device.get()]),
                "正在准备 Py-Feat 模型…首次下载所需时间取决于网络速度。",
                is_analysis=False,
            )

    def _export_model_cache(self) -> None:
        if self.running:
            return
        selected = filedialog.asksaveasfilename(
            title="保存 Py-Feat 离线模型包",
            defaultextension=".zip",
            initialfile="pyfeat_model_cache.zip",
            filetypes=[("ZIP 压缩包", "*.zip")],
        )
        if selected:
            self._launch(
                build_core_command(["--export-model-cache", selected]),
                "正在打包已缓存的 Py-Feat 模型…",
                is_analysis=False,
            )

    def _import_model_cache(self) -> None:
        if self.running:
            return
        selected = filedialog.askopenfilename(
            title="选择另一台电脑导出的 Py-Feat 离线模型包",
            filetypes=[("ZIP 压缩包", "*.zip")],
        )
        if selected:
            self._launch(
                build_core_command(["--import-model-cache", selected]),
                "正在导入 Py-Feat 离线模型包…",
                is_analysis=False,
            )

    def _launch(self, command: list[str], status_message: str, is_analysis: bool = True) -> None:
        self._append("\n运行：" + " ".join(f'\"{part}\"' if " " in part else part for part in command) + "\n")
        self.status.set(status_message)
        self.running = True
        self.task_started_at = time.monotonic()
        self.task_is_analysis = is_analysis
        self.latest_progress = ""
        self.preflight_button.configure(state="disabled")
        self.run_button.configure(state="disabled")
        self.summary_button.configure(state="disabled")
        self.prepare_models_button.configure(state="disabled")
        self.export_models_button.configure(state="disabled")
        self.import_models_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        thread = threading.Thread(target=self._run_subprocess, args=(command,), daemon=True)
        thread.start()

    def _run_subprocess(self, command: list[str]) -> None:
        environment = os.environ.copy()
        environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(APP_DIR.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                creationflags=flags,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.messages.put(("log", line))
            code = self.process.wait()
            self.messages.put(("finished", str(code)))
        except Exception as exc:
            self.messages.put(("error", f"无法启动程序：{type(exc).__name__}: {exc}"))

    def _stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        if messagebox.askyesno("停止当前任务", "会停止当前批次。已完成的检查点会保留，下次继续时自动跳过。确定停止吗？"):
            self.process.terminate()
            self._append("已请求停止当前任务；等待程序退出。\n")
            self.status.set("正在停止…")

    def _open_output(self) -> None:
        path = Path(self.output_root.get()).expanduser()
        if not path.exists():
            messagebox.showinfo("尚无结果", "结果文件夹尚未创建；请先运行预检或分析。")
            return
        os.startfile(str(path))

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self._append(value)
                    self._update_progress_status(value)
                elif kind == "finished":
                    self._set_finished(int(value))
                else:
                    self._append(value + "\n")
                    self._set_finished(1)
        except queue.Empty:
            pass
        self.after(100, self._drain_messages)

    def _set_finished(self, code: int) -> None:
        self.running = False
        self.process = None
        self.preflight_button.configure(state="normal")
        self.run_button.configure(state="normal")
        self.summary_button.configure(state="normal")
        self.prepare_models_button.configure(state="normal")
        self.export_models_button.configure(state="normal")
        self.import_models_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        elapsed = time.monotonic() - self.task_started_at if self.task_started_at is not None else None
        elapsed_text = self._format_elapsed(elapsed)
        self.task_started_at = None
        if code == 0:
            self.status.set(f"任务完成，本次用时 {elapsed_text}。可点击“打开结果文件夹”查看结果。")
            self._append(f"任务完成，本次用时 {elapsed_text}。\n")
        else:
            self.status.set(f"任务结束，返回代码 {code}，本次已用时 {elapsed_text}。请查看上方日志；检查点会保留。")
            self._append(f"任务结束，返回代码 {code}，本次已用时 {elapsed_text}。\n")

    @staticmethod
    def _format_elapsed(seconds: float | None) -> str:
        total = max(0, int(seconds or 0))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _update_progress_status(self, line: str) -> None:
        """Promote core timing logs into the always-visible GUI status line."""
        if "当前片段进度" not in line and "批处理总体进度" not in line:
            return
        message = re.sub(r"^.*?\|\s*(?:INFO\s*\|\s*)?", "", line.strip(), count=1)
        if "当前片段进度" in message:
            message = message[message.index("当前片段进度"):]
        elif "批处理总体进度" in message:
            message = message[message.index("批处理总体进度"):]
        self.latest_progress = message
        self.status.set(message)

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        if self.running and not messagebox.askyesno("退出界面", "分析仍在运行。退出会停止当前任务，但已完成的检查点可以续跑。确定退出吗？"):
            return
        if self.running and self.process:
            self.process.terminate()
        self.destroy()


if __name__ == "__main__":
    if IS_FROZEN and len(sys.argv) > 1 and sys.argv[1] == CORE_MODE_FLAG:
        raise SystemExit(run_bundled_core())
    DebuggerGUI().mainloop()

