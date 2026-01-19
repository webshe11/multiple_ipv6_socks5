# 多 IPv6 SOCKS5 代理生成器

## 项目概述

本项目提供了一个基于 Python 的实用工具，用于生成并运行多个 SOCKS5 代理服务器。每个代理服务器都会绑定到一个从特定前缀派生的唯一 IPv6 地址。这允许用户通过将不同的 IPv6 地址分配给不同的本地端口，来利用一段 IPv6 地址块（例如 `/64` 子网）。

**主要特性：**
*   **SOCKS5 实现：** 轻量级的 `asyncio` SOCKS5 服务器实现（支持 `CONNECT` 方法，无身份验证）。
*   **IP 生成：** 自动从提供或自动检测的前缀生成一系列 IPv6 地址。
*   **接口配置：** 自动将生成的 IPv6 地址添加到指定的网络接口（支持 macOS, Linux 和 Windows）。
*   **多端口绑定：** 将每个生成的 IPv6 地址映射到一个唯一的本地端口（例如：端口 10801 -> IP 1, 端口 10802 -> IP 2）。
*   **清理功能：** 包含一个实用程序，用于从接口中移除已添加的 IP 地址。

## 使用说明

### 前置要求

*   **Python 3.7+**：需要支持 `asyncio` 特性。
*   **Root/管理员权限**：需要权限以在网络接口上添加或移除 IP 地址。

### 运行代理

要启动代理，请使用管理员权限运行脚本。默认情况下，它会尝试检测您的 IPv6 前缀和接口。

**基本示例 (macOS/Linux):**
```bash
sudo python3 multiple_ipv6_socks5.py
```

**基本示例 (Windows):**
以管理员身份运行命令提示符 (CMD):
```cmd
python multiple_ipv6_socks5.py -i "Wi-Fi"
```

### 配置选项

您可以使用命令行参数自定义行为：

| 参数 | 描述 | 默认值 |
| :--- | :--- | :--- |
| `-i`, `--interface` | 网络接口名称 (例如 `en0`, `eth0`, `Wi-Fi`)。 | `en0` |
| `-p`, `--prefix` | IPv6 前缀 (例如 `2001:db8::`)。如果省略则自动检测。 | 自动检测 |
| `-P`, `--start-port` | 起始本地 SOCKS5 端口。 | `10801` |
| `-c`, `--count` | 要生成的 IP/端口数量。 | `5` |
| `-s`, `--start-index` | IP 后缀生成的起始索引。 | `1` |
| `--clean` | 从接口移除生成的 IP 并退出。 | `False` |

**示例：在接口 eth0 上生成 10 个代理，起始端口为 20000：**
```bash
sudo python3 multiple_ipv6_socks5.py -i eth0 -P 20000 -c 10
```

### 清理资源

要移除脚本添加的 IP 地址，请使用 `--clean` 标志运行脚本，并使用与创建时相同的参数（数量、起始索引）：

```bash
sudo python3 multiple_ipv6_socks5.py -i en0 -c 10 --clean
```

## 架构

该脚本 (`multiple_ipv6_socks5.py`) 是一个独立的模块，包含：
1.  **`Socks5Server` 类：** 处理 SOCKS5 握手并在客户端与远程目标之间传输数据。它明确地将出站连接绑定到分配给该实例的特定 IPv6 地址。
2.  **网络工具：** 使用系统命令 (`ifconfig`, `netsh`) 与底层操作系统网络栈交互的函数（`get_ipv6_prefix`, `configure_interface`, `cleanup_interface`, `is_admin`）。
3.  **主循环：** 解析参数，设置接口，并为每个代理服务器生成 `asyncio` 任务。

## 开发

*   **代码风格：** 标准 Python `asyncio` 模式。
*   **依赖：** 仅使用标准库 (`asyncio`, `socket`, `struct`, `subprocess`, `ipaddress`, `sys`, `argparse`, `re`, `os`, `platform`, `ctypes`)。无需安装额外的 `pip` 包。
