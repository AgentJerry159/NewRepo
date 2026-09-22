# Py-Feat面部分析 调试版


## 点击式界面（推荐）

首次使用建议双击 `启动安装与分析助手.bat`，不需要输入命令。它会检查 Conda、`pyfeat_fatigue` 环境、Py-Feat、PyTorch 和 CUDA；只有在点击“创建／修复 GPU 环境”并确认后才下载或安装依赖。环境准备好后，点击其中的“打开分析界面”。日常使用可直接双击 `启动微表情调试界面.bat`。

在分析界面中依次选择原始视频文件夹和“中间暂存／结果文件夹”，保留默认 GPU、`12 fps`、每批 `8` 张图片，先点击“预检”，确认后再点击“开始批量分析”。对于原始 `24 fps` 视频，`12 fps` 会按时间轴均匀地每隔 2 个原始帧抽取 1 帧。界面会显示运行日志，可停止任务；下次再次开始时会从已保存的检查点续跑。

启动器不再写死任何磁盘路径。它会依次查找：同目录未来的 `PyFeatFatigue.exe`、环境变量 `PYFEAT_PYTHON` 指向的解释器、名为 `pyfeat_fatigue` 的 Conda 环境，以及 Python 3.11 启动器。尚未打包时，源代码模式仍需要已安装依赖的 Python 环境；未来绿色版则不需要 Python 或 Conda。

## 已锁定的研究边界

- 输入命名：`受试者编号-时段代码-片段编号.ext`，例如 `27-1-4.mp4`。
- 时段代码为 `1`、`2`、`3`；每个时段有 `1` 至 `6` 六个连续五分钟片段。
- 每个视频默认只保留一张主脸：面积优先，且在相邻帧以人脸框重叠度辅助保持稳定。
- 默认“疲劳精简”模式以 `12 fps` 输出 35 个逐帧字段：时间轴、FaceScore/人脸检测、20 个 AU、Pitch/Roll/Yaw 和左右眨眼。
- 身份向量/embedding 不计算，也不输出。
- 可在界面改选“完整 Py-Feat（约 1679 列；会产生大量数据）”。该模式保存三维网格、关键点和 Blendshape 等高维原始输出，仅适合明确需要面部几何探索时使用。
- 不计算均值、疲劳分数、MAD、聚类或三级标签。

## 首先运行预检

建议新建环境，不覆盖已有的 `feat_env`、`V2_3.11` 或旧脚本。以下是针对本机现有 CUDA 11.8 PyTorch 组合的安装起点；它固定 Py-Feat 2.1.3，便于后续复现：

若由于网络或权限问题必须手工安装，安装助手使用的固定命令保存在 `requirements-torch-cu118.txt` 与 `requirements-pyfeat.txt`；建议优先使用点击式安装，避免复制命令时混用 CUDA 或 PyTorch 版本。

安装完成后，在这个新环境中先对真实数据目录运行：

```powershell
python fatigue_pyfeat_debug.py --input-root "E:\你的原始视频目录" --output-root "E:\你的分析结果\pyfeat_debug" --device cuda --dry-run
```

若不想依赖 Conda 环境名称，可把当前环境的 `pythonw.exe` 写入用户环境变量 `PYFEAT_PYTHON`，再双击启动器；变量值可以位于任意磁盘或目录。源代码模式的 GUI 会使用启动它的同一个 Python 解释器运行核心分析，而不再依赖固定路径。

```powershell
& "你的Python环境\python.exe" .\fatigue_pyfeat_debug\fatigue_pyfeat_debug.py --input-root ".\测试实验数据" --output-root ".\fatigue_pyfeat_debug\results" --device cuda --dry-run
```

不要再从 `V2` 文件夹启动旧脚本，也不要在命令行直接 `from feat import Detectorv2`；它们会再次尝试加载 Windows 上不兼容的 TorchCodec DLL。调试版程序会在导入 Py-Feat 前主动绕开该 DLL。

预检只检查：文件命名、OpenCV 是否能解码、视频 FPS/时长/分辨率、FFmpeg 是否在 PATH，以及当前 Python 是否真的能看到 CUDA。它不会下载模型或开始分析。

## 正式调试分析

```powershell
python fatigue_pyfeat_debug.py --input-root "E:\你的原始视频目录" --output-root "E:\你的分析结果\pyfeat_debug" --device cuda --sample-fps 12 --output-profile compact --batch-size 8
```

如果 GPU 发生显存错误，先把 `--batch-size 8` 改为 `4`；不要悄悄切换到 CPU。

## 结果结构

```text
pyfeat_debug/
  preflight_environment.json
  batch_manifest.xlsx
  all_analysis_raw.xlsx
  clips/<受试者-时段>/segment_<编号>_<原文件名>/
    metadata.json
    status.json
    frame_features.xlsx
    face_detection_quality.png
    raw_emotion_timeseries.png
  sessions/<受试者-时段>/session_timeline_raw.xlsx
```

每个五分钟片段只有一个最终的 `frame_features.xlsx`，其中的 `metadata` 工作表记录分析条件与输出模式，`frames_raw` 工作表保存该片段的逐帧输出。默认精简模式约 35 列；完整模式约 1679 列，会显著增大 Excel、磁盘占用和汇总耗时。`checkpoint_parts` 仅在中断、失败或尚未完成时保留，用于断点续跑；成功导出 Excel 后会自动删除，不会再留存重复数据。

可以把视频分成多批、多天分析：每次都在界面中选择**同一个**“中间暂存／结果文件夹”。程序会直接复用已完成且源视频、文件大小、抽样率一致的片段，不会重新推理。全部批次完成后点击“3. 汇总已有暂存结果”，程序只读取已有的每片段 Excel，重新生成：

- `all_analysis_raw.xlsx`：本暂存目录全部完成片段的原始结果。首页说明抽样率，`clip_index` 列出各片段，`frames_raw_001` 起保存全量原始数据；达到 Excel 单工作表行数上限时自动续写下一个工作表。
- `sessions/<受试者-时段>/session_timeline_raw.xlsx`：同一受试者/时段按片段编号写入统一时间轴；缺段保留实际空档，不补数据。

日志会在每个推理批次后显示当前片段进度、推理速度和预计剩余时间，并在每个片段结束后更新批处理总体预估。若改变 Py-Feat 版本、目标 FPS、输出字段模式或输入视频，请使用新的中间暂存目录，避免混用不同分析条件的结果。

`batch_manifest.xlsx` 的 `session_integrity` 会检查每个「受试者-时段」实际存在的片段、缺少的片段和重复编号。缺段时仍会对已有片段分析，并生成只包含已有片段的统一时间轴；时间仍按片段编号映射（例如只有第 2 段时从 300 秒开始），缺少的区间不补值、不伪装为连续数据。出现同一片段编号重复时，正式分析会停止，避免两个文件写入同一个结果目录。

`face_detection_quality.png` 来自 Py-Feat 人脸检测器的 `FaceScore`（图中纵轴会标出实际字段名）。它表示每个抽样帧“检测到这张脸”的模型置信度，用于查看遮挡、转头、离镜或检测不稳定的时段，是**质量控制图**，不是情绪分数、微表情强度或疲劳指标；不应直接放进疲劳判断模型。

## 当前环境提醒

现有 `V2_3.11` 环境曾检出为 CPU-only 且缺少 OpenCV，不能直接作为本程序的 GPU 正式环境。正式运行前应另建并验证独立的 Py-Feat 2.x + CUDA + OpenCV 环境；程序的 `--dry-run` 用于验证该环境。

Py-Feat 2.x 在 Windows 上即使采用图片输入，也可能在导入阶段强制加载 TorchCodec DLL，并弹出 `torch_get_const_data_ptr` 之类的系统错误。为避免触发该 DLL，程序会在导入 Py-Feat **之前**启用受限的「仅图片输入」兼容层：OpenCV/FFmpeg 负责视频解码，Py-Feat 只接收临时 PNG；如果后续代码试图使用 TorchCodec 解码，会立即报错。运行记录会在 `metadata.json` 中写明该兼容层被使用，便于审计。

