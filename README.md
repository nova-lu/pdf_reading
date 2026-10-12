# PDF 语音阅读助手

Windows 桌面端 PDF 语音朗读应用，支持打开 PDF 文档并使用 TTS 语音合成进行朗读。

## 功能特性

- **PDF 文档解析**：基于 PyMuPDF，支持普通 PDF 和加密 PDF（自动尝试空密码），按段落保留排版；**字符保真**——希腊字母、数学运算符、箭头、Unicode 分数、上下标等原样保留，不改写、不做 Unicode 归一化
- **双引擎语音朗读**：SAPI5 本地语音 + Edge-TTS 在线神经语音（共 9 个语音可选）
- **播放控制**：播放 / 暂停 / 恢复 / 停止，上一句 / 下一句跳转，进度条拖拽跳转
- **高亮同步**：当前朗读句子黄色高亮，与音频严格同步（基于 SAPI5 `started-word` 事件 / Edge-TTS MCI 播放状态）
- **朗读范围**：可选朗读全文或仅当前页，支持从任意页开始
- **选中文本朗读**：选中文本后右键或点按钮即可朗读选中内容
- **页面导航**：上一页 / 下一页 / 跳转指定页，跨页朗读自动翻页
- **参数调节**：语速（50-400）、音量（0-100%）实时调节
- **快捷键**：Ctrl+O 打开文件、空格 播放/暂停、Esc 停止、←/→ 上一句/下一句

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| PDF 解析 | PyMuPDF | 快速可靠的 PDF 文本提取，按 block 保留段落结构 |
| 本地语音 | pyttsx3 + SAPI5 | Windows 系统自带，离线可用 |
| 在线语音 | edge-tts | 微软 Edge 神经语音，音质更好 |
| GUI 界面 | tkinter + ttk | Python 内置，无需额外安装 |
| 线程模型 | threading + queue | TTS 在 worker 线程运行，UI 通过队列更新 |

## 快速开始

### 方式一：直接运行（开发模式）

1. 双击 `start.bat` 启动

### 方式二：手动运行

```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 安装依赖
venv\Scripts\pip.exe install -r requirements.txt

# 3. 启动应用
venv\Scripts\python.exe main.py
```

### 方式三：打包为 EXE（分发用）

见下方 [打包说明](#打包为-exe)。

## 项目结构

```
pdf_voice_reader/
├── main.py                    # 主程序入口（薄入口）
├── pdf_reader.py              # PDF 文本提取（字符保真 + span 级上下标标注）
├── math_unicode.py            # Unicode 数学字符工具（上下标映射、区块识别、字符画像）
├── tts_normalizer.py          # 朗读层规范化（符号 → 中文读法，仅发声时使用）
├── edge_tts_compat.py         # edge-tts 端点迁移兼容层（v1→v2 主机回退 + Date 解析修复）
├── tts_engine.py              # TTS 引擎（双后端 SAPI5 + Edge-TTS）
├── voice_manager.py           # 统一语音管理
├── reading_controller.py      # 页面感知朗读控制
├── ui/
│   ├── __init__.py
│   ├── main_window.py         # 主窗口编排
│   ├── text_panel.py          # 文本预览面板（段落排版 + 高亮 + 选择朗读）
│   └── control_panel.py       # 控制面板（播放控制 + 可拖拽进度 + 语音设置）
├── packaging/                  # 打包相关（与源码解耦）
│   ├── build_exe.py           # 自动化打包脚本
│   ├── pdf_reader.spec        # PyInstaller spec 配置（参考）
│   └── requirements_build.txt # 打包专用依赖
├── requirements.txt           # Python 运行时依赖
├── start.bat                  # Windows 启动脚本
├── test_text_fidelity.py      # 字符保真度测试（82 项）
├── test_tts_normalizer.py     # 朗读规范化测试（58 项）
├── test_edge_tts_compat.py    # edge-tts 兼容层回归测试（27 项，离线）
├── e2e_test.py                # 端到端测试（多场景，会实际发声）
├── .gitignore
└── README.md
```

## 文本处理架构：提取保真 + 朗读转换分层

提取与朗读是两层，互不影响，保证"看到的字符"与"读出的内容"各自最优：

| 层 | 模块 | 职责 | 是否改写字符 |
|---|---|---|---|
| 提取层 | `pdf_reader.py` + `math_unicode.py` | 忠实还原 PDF 字符：希腊字母、数学运算符、箭头、Unicode 分数、上下标等原样保留；用字号/基线识别视觉上下标并标注为 `x²`（或 `x^{2}`） | 不改写，只做结构性空白整理 |
| 朗读层 | `tts_normalizer.py`（由 `tts_engine` 在发声前调用） | 把符号转成中文读法：`x²`→“x的平方”、`∑ᵢ₌₁ⁿ`→“从i等于1到n求和”、`π`→“派”、`½`→“二分之一” | 只影响音频，不写回提取结果 |

- **字符保真**：提取过程不调用 `unicodedata.normalize`（不做 NFKC），不做任何"符号 → 中文"替换，不删除内容字符。
- **上下标结构化**：`PDFReader.load(path, script_mode=...)` 支持三种表示
  - `"unicode"`（默认）：`x²`、`a₁`，无法映射时回退 `x^{2}`
  - `"latex"`：统一 `x^{2}`、`a_{1}`
  - `"plain"`：不标注，按视觉顺序平铺
- **符号变体归一**（`normalize_script_signs=True`，默认开启）：PDF 字体常把负号渲染成各种破折号（如 `10⁻⁴³` 的负号被映射为 U+2012 FIGURE DASH）。开启后上标内会归一为负号，得到 `10⁻⁴³` / `10^{-43}`（合法 LaTeX）；设为 `False` 则严格保留原始码位（`10^{‒43}`）。**该归一仅作用于上下标标记内部，正文中的 – — 等排版字符始终原样保留。**
- **朗读开关**：`tts_engine.set_speech_normalize(False)` 可改为逐字原样朗读（严格保留 α、∑、² 等 Unicode 字符）。
- **手动转换**：`PDFDocument.get_page_text_for_speech(page)` 返回朗读文本；`get_page_text_clean(page)` 始终返回保真文本。
- **字符校验**：`math_unicode.check_critical_chars(text)` / `char_profile(text)` 可按 Unicode 区块统计字符构成，用于核对提取是否丢字或被改写。

## 打包为 EXE

### 环境要求

- **操作系统**：Windows 10 / 11（64 位）
- **Python**：3.10+（推荐 3.13）
- **虚拟环境**：已安装 `requirements.txt` 中的所有运行时依赖
- **磁盘空间**：至少 500 MB（PyInstaller 缓存 + 输出文件）
- **网络**：首次打包需要联网（PyInstaller 安装 + edge-tts 元数据）

### 打包步骤

```bash
# 1. 确保在项目根目录，且 venv 已安装运行时依赖
cd pdf_voice_reader
venv\Scripts\pip.exe install -r requirements.txt

# 2. 运行打包脚本（自动安装 PyInstaller，清理旧产物，执行打包）
venv\Scripts\python.exe packaging\build_exe.py
```

打包完成后，exe 文件输出到：

```
dist/PDF语音阅读助手.exe
```

### 打包模式

| 模式 | 命令 | 输出 | 适用场景 |
|------|------|------|----------|
| 单文件（默认） | `python packaging\build_exe.py` | `dist/PDF语音阅读助手.exe`（约 36 MB） | 分发方便，单文件即可运行 |
| 目录模式 | `python packaging\build_exe.py --dir-mode` | `dist/PDF语音阅读助手/`（文件夹） | 启动更快，适合本地使用 |

### 打包脚本参数

```bash
python packaging\build_exe.py [选项]

选项：
  --dir-mode     目录模式（onedir），启动更快但需要分发整个文件夹
  --no-clean     跳过清理旧产物（增量打包，加速二次打包）
  --output PATH  自定义输出目录（默认: dist/）
  --clean        仅清理旧产物，不执行打包
```

### 打包配置说明

打包配置与项目源码完全解耦，位于 `packaging/` 目录：

- **`packaging/build_exe.py`**：自动化打包脚本，处理依赖检查、清理、打包、验证全流程
- **`packaging/pdf_reader.spec`**：PyInstaller spec 配置文件（参考用，命令行模式自动生成 spec）
- **`packaging/requirements_build.txt`**：打包专用依赖（PyInstaller）

打包流程不影响版本迭代与开发：
- `packaging/` 目录独立于源码模块
- `.gitignore` 已排除 `dist/`、`packaging/_pyinstaller_cache/`、自动生成的 `.spec` 文件
- 开发时无需安装 PyInstaller

### 常见问题排查

**Q: 打包时报 `ModuleNotFoundError: No module named 'xxx'`？**

A: 在 `packaging/build_exe.py` 的 `--hidden-import` 列表中添加缺失的模块名，重新打包。

**Q: 打包后的 exe 启动后闪退？**

A: 用控制台模式重新打包来查看错误信息：
```bash
# 临时修改 build_exe.py 中的 --windowed 为 --console，或手动运行：
venv\Scripts\python.exe -m PyInstaller main.py --name PDF语音阅读助手 --onefile --collect-all edge_tts --collect-all pyttsx3 --collect-all pymupdf --collect-all aiohttp --hidden-import pythoncom --hidden-import win32com
```

**Q: exe 文件太大（> 50 MB）？**

A: 这是正常的。PyMuPDF（PDF 解析）和 aiohttp（Edge-TTS 网络库）体积较大。如需减小体积：
- 使用 `--exclude-module` 排除不需要的模块
- 使用 UPX 压缩（`--upx-dir` 指定 UPX 路径）

**Q: 杀毒软件误报？**

A: PyInstaller 打包的 exe 常被误报。解决方法：
- 将 exe 添加到杀毒软件信任列表
- 使用代码签名证书对 exe 进行签名

**Q: Edge-TTS 在线语音无法使用？**

A: Edge-TTS 需要网络连接。微软自 2026 年 10 月起将 Edge TTS 后端从 `speech.platform.bing.com`（v1）迁移到 `api.msedgeservices.com`（v2）并按区域灰度，旧版 edge-tts（≤7.2.8，仓库停留在 2026-03）在多区域会被服务端拒绝（WebSocket 握手 403）。

本项目已通过 `edge_tts_compat.py` 在内置修复：合成端点自动切换到 v2，v2 不可用时回退 v1，并对失败给出明确错误提示与「切换到本地 SAPI5 语音」的恢复入口。若在线语音仍失败，请检查网络；无需在线语音时可仅使用 SAPI5 本地语音。

**Q: 打包后 SAPI5 语音不工作？**

A: 确保 `pythoncom` 和 `pywintypes` 已包含在 hidden imports 中（`build_exe.py` 已默认配置）。如仍有问题，尝试在 spec 文件中添加 `--collect-all pywin32`。

## 安装更多语音

应用使用 Windows 系统 SAPI5 语音引擎。如需更多语音（如日语、韩语等）：

1. 打开 **设置 → 时间和语言 → 语言和区域**
2. 添加目标语言
3. 在该语言下点击 **语音** 选项，下载语音包
4. 重启应用即可在下拉列表中看到新语音

## 常见问题

**Q: 打开 PDF 后文本区域为空？**

A: 该 PDF 可能是扫描件或纯图片，不包含可选文本。需要先用 OCR 工具处理。

**Q: 没有检测到中文语音？**

A: 请在 Windows 设置中安装中文语言包和语音包。

**Q: 朗读时有卡顿？**

A: 尝试降低语速，或关闭其他占用 CPU 的程序。在线语音（Edge-TTS）如遇网络延迟也会卡顿，可切换到本地语音。已对 Edge-TTS 实现下一句音频预生成，可显著压缩句间网络等待导致的停顿。

**Q: 在线语音句间停顿比本地语音长？**

A: 在线语音（Edge-TTS）每句都需要联网生成音频，默认模式下上一句播完后才开始生成下一句，会产生额外空档。当前版本已在播放当前句时后台预生成下一句音频，使句间延迟尽量接近本地 SAPI5 语音。由于不同语音包本身的句末尾音长度不同，仍可能存在细微差异，这属于语音合成引擎特性，代码层无法完全统一。

**Q: 高亮和语音不同步？**

A: 已通过 SAPI5 `started-word` 事件（音频真正开始播放的时机）同步高亮，并在跳句/停止后排空引擎残留事件，确保导航与高亮、语音三者一致。如仍有问题，请确认使用的是最新版本。
