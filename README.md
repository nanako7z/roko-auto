# roko-auto — 多任务输入自动化调度平台

基于 **Interception 内核驱动**的 Windows 键盘/鼠标自动化平台。支持多任务并发调度、Web API 远程控制和屏幕监控。无驱动时自动降级为 Win32 `SendInput`。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Web 服务（加载 tasks/ 目录下的任务）
python -m roko serve

# 访问 API 文档
# http://localhost:8642/docs
```

## 前置条件

1. **Windows** + 安装 Interception 驱动（管理员权限）
2. `interception.dll` 与脚本同目录或加入 `PATH`
3. Python 3.9+

## 运行方式

### 1. Web 服务模式（推荐）

启动 Web 服务 + 任务调度器，通过 API 管理所有任务：

```bash
python -m roko serve                        # 使用默认配置
python -m roko serve --config server.yaml   # 指定服务配置
```

启动后：
- 自动加载 `tasks/` 目录下的所有任务配置，根据 `enabled` 字段决定是否自动运行
- 打开浏览器访问 `http://localhost:8642` 进入 **Web 管理界面**
- 访问 `http://localhost:8642/docs` 查看 Swagger API 文档

### Web 管理界面

内置可视化管理界面，支持：
- **Tasks** — 查看所有任务状态，一键启动/停止/暂停/恢复/触发；新建和编辑任务（表单模式 + YAML 模式）
- **Monitor** — 实时屏幕监控，可选刷新间隔（1s/2s/5s/10s），支持指定区域截图
- **System** — 查看驱动类型、运行中任务数、系统运行时间等状态信息

### 2. 单任务模式

不启动 Web 服务，直接运行单个任务：

```bash
python -m roko run --task tasks/keep_alive.yaml         # 新格式任务
python -m roko run --config config.yaml                  # 旧格式兼容
python -m roko run --config config.yaml --once           # 单次执行
```

### 3. 录制模式

录制键盘和鼠标操作为 `.bin` 文件（需要 Interception 驱动）：

```bash
python -m roko record output.bin
python -m roko record macros/login.bin --config config.yaml
```

- 录制开始时光标自动归零到 `(0, 0)`
- 按 **F12** 停止录制（Ctrl+C 备用）
- 输出的 `.bin` 文件可通过 `type: file` 命令回放

### 4. 旧入口（向后兼容）

```bash
python interception_runner.py --config config.yaml
python interception_runner.py --config config.yaml --once
python interception_runner.py --record output.bin
```

## 项目结构

```
roko-auto/
  server.yaml                 # 服务端配置
  config.yaml                 # 旧格式配置（自动兼容）
  requirements.txt            # Python 依赖
  interception.dll            # Interception 驱动 DLL
  interception_runner.py      # 旧入口（薄包装）

  roko/                       # 核心包
    cli.py                    # CLI 子命令（serve/run/record）
    server.py                 # Web 服务启动

    input/                    # 输入驱动层
      context.py              # 线程安全的共享驱动上下文
      keyboard.py             # 键盘实现（Interception + SendInput）
      mouse.py                # 鼠标实现（Interception + SendInput）
      recorder.py             # 输入录制器
      replay.py               # 录制回放
      helpers.py              # 按键映射、贝塞尔曲线移动
      constants.py            # 常量和数据结构

    commands/                 # 命令系统
      executor.py             # 命令执行引擎
      loader.py               # 配置加载、DLL 路径解析

    config/                   # 配置管理
      models.py               # Pydantic 配置模型
      loader.py               # 配置加载与旧格式迁移

    scheduler/                # 任务调度
      task_runner.py          # 单任务线程
      task_manager.py         # 多任务生命周期管理
      schedule_types.py       # interval/cron/oneshot 调度
      models.py               # 任务状态模型

    api/                      # Web API
      app.py                  # FastAPI 应用
      routes_tasks.py         # 任务管理接口
      routes_screen.py        # 屏幕监控接口
      routes_system.py        # 系统状态接口
      deps.py                 # 依赖注入

    screen/                   # 屏幕监控
      capture.py              # 基于 mss 的截图

  tasks/                      # 任务配置目录
  commands/                   # 可复用命令序列目录
```

## 配置说明

### 服务配置（server.yaml）

```yaml
server:
  host: 0.0.0.0
  port: 8642

driver:
  dll_path: interception.dll

screen:
  capture_method: mss
  max_fps: 2

tasks_dir: ./tasks
commands_dir: ./commands
```

### 任务配置（tasks/*.yaml）

每个任务是一个独立的 YAML 文件，包含调度规则和命令序列：

```yaml
name: keep_alive
enabled: true

schedule:
  type: interval           # interval / cron / oneshot
  interval_sec: 20         # 执行周期（秒）
  jitter_sec: 5            # 随机浮动（±秒）
  start_delay_sec: 3       # 启动延迟

options:
  default_hold_sec: 0.03
  pause_between_cycles_sec: 0
  mouse_move_default_duration_sec: 0
  mouse_move_default_wobble: 0.2

# 内联命令
commands:
  - type: key
    key: tab
  - type: wait
    sec: 2
  - type: key
    key: "2"

# 或引用外部命令文件
# command_file: commands/my_sequence.yaml
```

### 调度类型

| 类型 | 说明 | 关键字段 |
|------|------|----------|
| `interval` | 固定周期执行（支持抖动） | `interval_sec`, `jitter_sec` |
| `cron` | Cron 表达式调度 | `cron_expression: "*/5 * * * *"` |
| `oneshot` | 执行一次后停止 | — |

### 命令类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `key` | 单键按下/释放 | `key: tab` |
| `hotkey` | 组合键 | `keys: [ctrl, shift, esc]` |
| `mouse_click` | 鼠标点击 | `button: left` |
| `mouse_move` | 鼠标移动（相对/绝对/贝塞尔） | `x: 960, y: 540, absolute: true` |
| `mouse_scroll` | 滚轮 | `amount: 3` |
| `wait` | 等待 | `sec: 2` |
| `file` | 加载外部命令文件 | `path: macros/login.yaml` |

支持的按键：`a-z`, `0-9`, `tab`, `enter`, `esc`, `space`, `backspace`, `up`, `down`, `left`, `right`, `ctrl`, `shift`, `alt`, `f12`

人工模拟鼠标移动（贝塞尔曲线 + 缓入缓出）：

```yaml
- type: mouse_move
  x: 960
  y: 540
  absolute: true
  duration: 0.8     # 移动耗时（秒）
  wobble: 0.2       # 曲线弯曲程度
```

### 外部文件引用

```yaml
commands:
  - type: file
    path: macros/login_sequence.yaml    # YAML 命令列表
  - type: file
    path: recordings/mouse_action.bin   # 二进制录制回放
```

- 路径相对于当前配置文件所在目录
- 支持嵌套引用（最大 10 层），自动检测循环引用

## Web API

启动服务后访问 `http://localhost:8642/docs` 查看完整 Swagger 文档。

### 任务管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tasks` | 列出所有任务及状态 |
| `POST` | `/api/tasks` | 创建任务 |
| `GET` | `/api/tasks/{name}` | 获取任务详情 |
| `PUT` | `/api/tasks/{name}` | 更新任务配置 |
| `DELETE` | `/api/tasks/{name}` | 删除任务 |
| `POST` | `/api/tasks/{name}/start` | 启动任务 |
| `POST` | `/api/tasks/{name}/stop` | 停止任务 |
| `POST` | `/api/tasks/{name}/pause` | 暂停任务 |
| `POST` | `/api/tasks/{name}/resume` | 恢复任务 |
| `POST` | `/api/tasks/{name}/trigger` | 立即触发一次 |

#### 创建任务示例

```bash
curl -X POST http://localhost:8642/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "keep_alive",
    "enabled": true,
    "schedule": {
      "type": "interval",
      "interval_sec": 30,
      "jitter_sec": 5
    },
    "commands": [
      {"type": "key", "key": "tab"},
      {"type": "wait", "sec": 1}
    ]
  }'
```

#### 控制任务

```bash
# 启动
curl -X POST http://localhost:8642/api/tasks/keep_alive/start

# 停止
curl -X POST http://localhost:8642/api/tasks/keep_alive/stop

# 立即触发一次
curl -X POST http://localhost:8642/api/tasks/keep_alive/trigger
```

### 屏幕监控

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/screen` | 截图（返回 PNG） |
| `GET` | `/api/screen?format=jpeg` | 截图（返回 JPEG） |
| `GET` | `/api/screen?format=base64` | 截图（返回 Base64 JSON） |
| `GET` | `/api/screen?region=0,0,800,600` | 指定区域截图 |

```bash
# 保存截图
curl http://localhost:8642/api/screen -o screenshot.png

# 获取 base64 编码
curl "http://localhost:8642/api/screen?format=base64"
```

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/system/health` | 健康检查 |
| `GET` | `/api/system/status` | 系统状态（驱动、任务数、运行时间） |
| `POST` | `/api/system/shutdown` | 优雅关闭 |

## 录制文件格式

二进制格式（紧凑高效，适合高频鼠标事件）：

- Header：`RCRD` magic + version + event count（10 字节）
- 键盘事件：7 字节（delta_ms + scan code + state）
- 鼠标事件：17 字节（delta_ms + state + flags + rolling + x + y）

## 打包发布

```bash
pip install pyarmor pyinstaller

# 混淆源码
pyarmor gen -O obf_dist interception_runner.py

# 打包为单文件 exe（内置 DLL）
pyinstaller --onefile --name interception_runner \
  --add-binary "interception.dll;." obf_dist\interception_runner.py
```

产物：`dist\interception_runner.exe`，运行时只需 `config.yaml`。
