# Windows 自动执行脚本（接近设备版）

本目录当前保留的是 Interception 驱动方案：
- `interception_runner.py`
- `config.yaml`

该方案通过驱动层发送键盘扫描码，比普通 `SendInput/pyautogui` 更接近真实输入设备行为。

## 前置条件
1. 在 Windows 安装 Interception 驱动（管理员权限）
2. 确保 `interception.dll` 可被加载（与脚本同目录或加入 `PATH`）
3. 安装依赖：
```bash
pip install pyyaml
```

## 运行
```bash
python interception_runner.py --config config.yaml
```

单次验证：
```bash
python interception_runner.py --config config.yaml --once
```

## 操作录制

录制键盘和鼠标操作，保存为二进制文件（`.bin`），可直接回放。

```bash
python interception_runner.py --record macros/login.bin
```

- 录制开始时光标自动归零到屏幕原点 `(0, 0)`，确保录制和回放起点一致
- 按 **F12** 停止录制（Ctrl+C 作为备用）
- 录制需要 Interception 驱动，不支持 SendInput 降级
- 输出的 `.bin` 文件可通过 `type: file` 命令直接回放

二进制格式（紧凑高效，适合高频鼠标事件）：
- Header：`RCRD` magic + version + event count（10 字节）
- 键盘事件：7 字节（delta_ms + scan code + state）
- 鼠标事件：17 字节（delta_ms + state + flags + rolling + x + y）

## 配置
`config.yaml` 支持：
- `schedule.interval_sec`：周期
- `schedule.jitter_sec`：周期随机浮动（±秒）
- `commands`：按顺序执行的命令
- `options.mouse_move_default_duration_sec`：`mouse_move` 默认耗时（默认 `0.8`）
- `options.mouse_move_default_wobble`：`mouse_move` 默认曲率（默认 `0.2`）

常用命令类型：
1. `key` / `hotkey`
2. `mouse_click`
3. `mouse_move`（支持 `absolute: true` + `duration` + `wobble` 的人工轨迹）
4. `mouse_scroll`
5. `wait`
6. `file` — 从外部文件加载并执行命令

### `type: file` 命令

从外部文件加载命令，支持 `.yaml`/`.yml`（命令列表）和 `.bin`（录制文件）：

```yaml
commands:
  - type: file
    path: macros/login_sequence.yaml    # YAML 命令文件
  - type: wait
    sec: 2
  - type: file
    path: recordings/mouse_action.bin   # 二进制录制文件回放
```

- 路径相对于当前配置文件所在目录解析
- 支持嵌套引用（最大深度 10 层），自动检测循环引用
- YAML 外部文件只需包含 `commands:` 列表

当前示例：
1. 按下 `tab`
2. 等待 2 秒
3. 按下 `2`

## 停止
- 控制台 `Ctrl + C`

## Windows 打包与代码混淆
推荐流程：先混淆，再打包。

1. 安装工具（PowerShell / CMD）：
```bash
pip install pyarmor pyinstaller
```

2. 混淆源码（输出到 `obf_dist`）：
```bash
pyarmor gen -O obf_dist interception_runner.py
```

3. 打包为单文件 `exe`（内置 `interception.dll`）：
```bash
pyinstaller --onefile --name interception_runner --add-binary "interception.dll;." obf_dist\interception_runner.py
```

4. 产物位置：
- 可执行文件：`dist\interception_runner.exe`
- 运行时只需确保 `config.yaml` 可访问；`interception.dll` 已内置到 exe。
