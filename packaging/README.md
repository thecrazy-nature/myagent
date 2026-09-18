# Windows 分发方案

本目录提供当前用户范围的 Windows 安装与发布脚本。安装过程不写 Windows Registry，不设置永久环境变量，也不会复制 Hermes 认证文件、API Key、`docs/` 或 `runs/`。

## 构建发布包

在仓库根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\packaging\windows\build-release.ps1"
```

产物写入本地 `dist/`（已被 Git 忽略），其中包含一个 ZIP 和 SHA-256 校验文件。发布包只包含运行应用所需的源码、配置说明和安装脚本，不包含测试、benchmark、研究文档或运行记录。

## 安装

用户解压 ZIP 后双击 `setup.cmd`，或运行：

```powershell
.\install.ps1 -InstallHermes
```

默认安装目录为 `%LOCALAPPDATA%\HermesEMAgent`。安装器会：

1. 在缺少 Hermes 时调用 Hermes 官方 Windows 安装器；
2. 使用 Hermes 自带的 Python/uv 安装 Streamlit 等 UI 依赖；
3. 检测 MATLAB，或接受 `-MatlabExecutable` 指定路径；
4. 写入仅位于应用目录的 `app-settings.local.json`；
5. 创建当前用户的开始菜单与桌面快捷方式。

首次使用仍需由用户自行完成 Hermes 模型认证。认证信息保存在 Hermes 自己的用户目录中，不进入本项目或发布包。

## 外部软件边界

- **MATLAB**：当前数值核心仍通过 `matlab -batch` 执行，因此目标电脑需要兼容 MATLAB。若要做到目标电脑无需 MATLAB 许可证，需要购买/使用 MATLAB Compiler 构建独立数值程序，并让用户安装匹配版本的 MATLAB Runtime。
- **CST Studio Suite**：仅超表面 CST 自动建模功能需要。普通聚焦、阵列设计和 MATLAB 超表面优化不依赖 CST。CST 的安装与许可证不能随本项目再分发。
- **网络**：Hermes 安装、模型认证和在线模型调用需要网络。代理完全可选，并只通过当前 PowerShell 会话配置。

## 卸载

从安装目录运行：

```powershell
.\uninstall.ps1
```

默认保留 `runs/` 和本地设置。若明确需要连同实验数据一起删除：

```powershell
.\uninstall.ps1 -RemoveUserData
```
