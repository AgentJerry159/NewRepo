# Py-Feat 疲劳诱导视频分析（调试版）

这是一个面向疲劳诱导研究的 Windows 源码发行版。它从视频中提取逐帧的 Py-Feat 面部原始特征，**不输出疲劳诊断、微表情标签、均值、MAD、聚类或三级疲劳结论**。

## 先双击，不需要敲命令

1. 安装 [Miniconda 或 Anaconda](https://www.anaconda.com/download)。
2. 双击 `启动安装与分析助手.bat`。
3. 点击“检查环境”；首次使用再点击“创建／修复 GPU 环境”，在确认框中同意下载依赖。
4. 环境检查通过后，点击“打开分析界面”。以后日常使用直接双击 `启动微表情调试界面.bat`。

安装助手使用已测试的 Windows 配置：Python 3.11、Py-Feat 2.1.3、PyTorch 2.7.1 + CUDA 11.8。首次环境约占 7 GB；NVIDIA GPU/CUDA 不可用时，主界面仍可选择 CPU 分析（速度会明显变慢）。

## 研究与输出边界

- 视频命名：`受试者编号-时段代码-片段编号.ext`，如 `27-1-4.mp4`。
- 默认按时间轴以 **12 fps** 均匀抽样；24 fps 源视频通常每隔 2 个原始帧保留 1 帧。
- 一段视频只保留面积最大且时间上稳定的一张主脸。
- 默认“精简指标”输出约 35 个逐帧字段：时间轴、FaceScore、20 个 AU、Pitch/Roll/Yaw 和左右眨眼；身份向量不计算。
- “完整 Py-Feat”是可选项，约 1679 列，包含网格、关键点和 Blendshape，文件和汇总会大幅增加。
- 每段视频导出一个 Excel；批量可中断续跑；可使用同一个中间暂存目录分批处理，并以“汇总”按钮重建所有片段的统一 Excel。
- `FaceScore` 图仅是人脸检测质量控制图，不是疲劳、情绪或微表情强度。

## 隐私与仓库内容

本仓库**不包含**原始视频、任何受试者画面、Excel/CSV 结果、检查点、模型下载缓存或本机日志；`.gitignore` 会排除这些内容。请在本地选择数据与结果目录，并依照伦理审批与数据管理要求保存。

## TorchCodec / FFmpeg 兼容性

旧 Py-Feat Windows 环境可能在导入时出现 `torch_get_const_data_ptr`、`libtorchcodec_image.dll` 等动态链接库错误。本程序在导入 Py-Feat 前启用只接受图片输入的兼容层：OpenCV/FFmpeg 负责视频解码，Py-Feat 接收临时 PNG。这避免由 TorchCodec 直接打开视频所引发的该类 DLL 问题；若后续代码尝试使用 TorchCodec 视频解码，会明确报错而非悄悄混用路径。

## 文件

- `setup_assistant.py`：无需 Py-Feat 即可打开的环境检查、安装与启动界面。
- `fatigue_pyfeat_debug_gui.py`：分析、预检与既有结果汇总的点击式界面。
- `fatigue_pyfeat_debug.py`：分析核心。
- `requirements-torch-cu118.txt`、`requirements-pyfeat.txt`：固定依赖版本记录。

该工具服务于特征提取和后续研究分析；任何疲劳判定方法都仍需以独立标签、研究设计和统计验证为依据。
