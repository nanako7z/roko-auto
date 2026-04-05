# Windows 自动执行脚本（接近设备版）

本目录当前保留的是 Interception 驱动方案：
- `interception_runner.py`
- `config_interception.yaml`

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
python interception_runner.py --config config_interception.yaml
```

单次验证：
```bash
python interception_runner.py --config config_interception.yaml --once
```

## 配置
`config_interception.yaml` 支持：
- `schedule.interval_sec`：周期
- `schedule.jitter_sec`：周期随机浮动（±秒）
- `commands`：按顺序执行的命令

当前示例：
1. 按下 `tab`
2. 等待 2 秒
3. 按下 `1`

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

3. 打包为单文件 `exe`：
```bash
pyinstaller --onefile --name interception_runner obf_dist\interception_runner.py
```

4. 产物位置：
- 可执行文件：`dist\interception_runner.exe`
- 运行时请确保 `config_interception.yaml` 与 `interception.dll` 在可访问路径（同目录最简单）。
