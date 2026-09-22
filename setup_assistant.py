"""Clickable setup and launch assistant for the source-code distribution.

This small program deliberately has no Py-Feat import. It can therefore be
opened on a new computer to inspect or create the analysis Conda environment
before the main analysis program itself is available.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

APP_DIR = Path(__file__).resolve().parent
ENV_NAME = "pyfeat_fatigue"
MAIN_GUI = APP_DIR / "fatigue_pyfeat_debug_gui.py"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_conda() -> str | None:
    configured = os.environ.get("CONDA_EXE")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("conda")


def run_capture(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=APP_DIR, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW, check=False)
    return completed.returncode, completed.stdout.strip()


def inspect_environment() -> dict[str, str]:
    """Inspect package/CUDA state without importing py-feat."""
    conda = find_conda()
    result = {"conda": "未找到（请先安装 Miniconda 或 Anaconda）", "environment": "未检查",
        "packages": "未检查", "gpu": "未检查", "detail": ""}
    if not conda:
        return result
    result["conda"] = conda
    code, env_text = run_capture([conda, "env", "list", "--json"])
    if code != 0:
        result["environment"], result["detail"] = "Conda 无法列出环境", env_text
        return result
    try:
        environments = json.loads(env_text).get("envs", [])
    except json.JSONDecodeError:
        environments = []
    matching = [Path(item) for item in environments if Path(item).name.lower() == ENV_NAME]
    if not matching:
        result["environment"] = f"未找到环境 {ENV_NAME}"
        return result
    result["environment"] = f"已找到：{matching[0]}"
    probe = (
        "import importlib.metadata as m, json; "
        "names=['py-feat','torch','torchvision','opencv-python','openpyxl','matplotlib']; "
        "versions={n:m.version(n) for n in names}; import torch; "
        "print(json.dumps({'versions':versions,'torch_cuda':torch.version.cuda,"
        "'cuda_available':torch.cuda.is_available(),"
        "'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None},ensure_ascii=False))"
    )
    code, probe_text = run_capture([conda, "run", "-n", ENV_NAME, "python", "-c", probe])
    if code != 0:
        result["packages"], result["detail"] = "环境存在，但关键依赖未完整安装", probe_text
        return result
    try:
        info = json.loads(probe_text.splitlines()[-1])
        result["packages"] = "已安装：" + "；".join(f"{n} {v}" for n, v in info["versions"].items())
        result["gpu"] = (f"可用：{info['device']}（CUDA {info['torch_cuda']}）"
                         if info["cuda_available"] else "未检测到可用 CUDA；仍可在主界面选择 CPU")
    except (json.JSONDecodeError, KeyError, IndexError):
        result["packages"], result["detail"] = "依赖检查返回格式异常", probe_text
    return result


class SetupAssistant(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Py-Feat 疲劳分析：安装与启动助手")
        self.geometry("860x650")
        self.minsize(720, 520)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.installing = False
        self.conda_status = tk.StringVar(value="尚未检查")
        self.environment_status = tk.StringVar(value="尚未检查")
        self.package_status = tk.StringVar(value="尚未检查")
        self.gpu_status = tk.StringVar(value="尚未检查")
        self._build()
        self.after(100, self._drain_messages)
        self.check_environment()

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Py-Feat 疲劳视频分析：源码安装与启动",
                  font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        ttk.Label(outer, text=("首次使用请先检查环境；如未就绪，再点“创建／修复 GPU 环境”。首次下载体积较大，"
            "可能需要较长时间。此助手不会读取、上传或修改您的视频和分析结果。"),
            wraplength=810, justify="left").pack(anchor="w", pady=(7, 12))
        card = ttk.LabelFrame(outer, text="当前状态", padding=10)
        card.pack(fill="x")
        for row, (label, variable) in enumerate((("Conda", self.conda_status),
            ("分析环境", self.environment_status), ("关键依赖", self.package_status), ("GPU / CUDA", self.gpu_status))):
            ttk.Label(card, text=f"{label}：", width=12).grid(row=row, column=0, sticky="nw", pady=3)
            ttk.Label(card, textvariable=variable, wraplength=670, justify="left").grid(row=row, column=1, sticky="w", pady=3)
        card.columnconfigure(1, weight=1)
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=12)
        ttk.Button(buttons, text="检查环境", command=self.check_environment).pack(side="left", padx=(0, 8))
        self.install_button = ttk.Button(buttons, text="创建／修复 GPU 环境", command=self.confirm_install)
        self.install_button.pack(side="left", padx=8)
        self.launch_button = ttk.Button(buttons, text="打开分析界面", command=self.launch_analysis)
        self.launch_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="打开本文件夹", command=lambda: os.startfile(APP_DIR)).pack(side="right")
        ttk.Label(outer, text="运行记录（安装或修复时会显示每一步；检查环境不会修改系统）：").pack(anchor="w")
        self.log = scrolledtext.ScrolledText(outer, height=18, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "status":
                    status = value
                    assert isinstance(status, dict)
                    self.conda_status.set(str(status["conda"]))
                    self.environment_status.set(str(status["environment"]))
                    self.package_status.set(str(status["packages"]))
                    self.gpu_status.set(str(status["gpu"]))
                    self._append("环境检查完成。")
                    if status.get("detail"):
                        self._append(str(status["detail"]))
                elif kind == "log":
                    self._append(str(value))
                elif kind == "install_done":
                    self.installing = False
                    self.install_button.configure(state="normal")
                    self.launch_button.configure(state="normal")
                    self._append("安装／修复流程结束；正在重新检查环境。")
                    self.check_environment()
        except queue.Empty:
            pass
        self.after(100, self._drain_messages)

    def check_environment(self) -> None:
        if not self.installing:
            self._append("正在检查 Conda、分析环境、关键依赖和 CUDA……")
            threading.Thread(target=lambda: self.messages.put(("status", inspect_environment())), daemon=True).start()

    def confirm_install(self) -> None:
        conda = find_conda()
        if not conda:
            messagebox.showerror("未找到 Conda", "请先安装 Miniconda 或 Anaconda，并重新打开本助手。")
            return
        if not messagebox.askyesno("确认创建／修复环境",
            "将创建或修复名为 pyfeat_fatigue 的 Conda 环境，并从网络下载 Python、PyTorch、Py-Feat 等依赖。\n\n"
            "这会占用约 7 GB 磁盘空间（另请为分析结果预留空间）。是否继续？"):
            return
        self.installing = True
        self.install_button.configure(state="disabled")
        self.launch_button.configure(state="disabled")
        threading.Thread(target=self.install_environment, args=(conda,), daemon=True).start()

    def install_environment(self, conda: str) -> None:
        _, text = run_capture([conda, "env", "list", "--json"])
        try:
            exists = any(Path(item).name.lower() == ENV_NAME for item in json.loads(text).get("envs", []))
        except json.JSONDecodeError:
            exists = False
        commands: list[list[str]] = []
        if exists:
            self.messages.put(("log", f"检测到现有环境 {ENV_NAME}；将只修复／补齐依赖，不会删除该环境。"))
        else:
            commands.append([conda, "create", "-n", ENV_NAME, "python=3.11", "-y"])
        commands += [
            [conda, "run", "-n", ENV_NAME, "python", "-m", "pip", "install", "--upgrade", "pip"],
            [conda, "run", "-n", ENV_NAME, "python", "-m", "pip", "install", "torch==2.7.1+cu118", "torchvision==0.22.1+cu118", "--index-url", "https://download.pytorch.org/whl/cu118"],
            [conda, "run", "-n", ENV_NAME, "python", "-m", "pip", "install", "py-feat==2.1.3", "opencv-python", "openpyxl", "matplotlib"],
        ]
        for command in commands:
            self.messages.put(("log", "$ " + subprocess.list2cmdline(command)))
            process = subprocess.Popen(command, cwd=APP_DIR, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
            assert process.stdout is not None
            for line in process.stdout:
                self.messages.put(("log", line.rstrip()))
            if process.wait() != 0:
                self.messages.put(("log", "安装未完成：请复制上方最后的报错信息以便排查。"))
                break
        self.messages.put(("install_done", None))

    def launch_analysis(self) -> None:
        conda = find_conda()
        if not conda:
            messagebox.showerror("无法启动", "未找到 Conda。请先安装 Miniconda 或 Anaconda。")
        elif not MAIN_GUI.is_file():
            messagebox.showerror("无法启动", f"未找到主界面文件：\n{MAIN_GUI}")
        else:
            try:
                subprocess.Popen([conda, "run", "-n", ENV_NAME, "pythonw", str(MAIN_GUI)],
                    cwd=APP_DIR, creationflags=CREATE_NO_WINDOW)
                self._append("已请求打开分析主界面。若未出现窗口，请先点击“检查环境”。")
            except OSError as exc:
                messagebox.showerror("无法启动", str(exc))


if __name__ == "__main__":
    SetupAssistant().mainloop()
