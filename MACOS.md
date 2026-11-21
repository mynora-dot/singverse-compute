# Mac 原生计算节点

GitHub 正式 Release 同时提供 `singverse-compute-macos-arm64-<版本>.dmg` 和同名 `.sha256`，适用于 Apple Silicon（M1 及后续芯片）的 macOS 原生节点。版本与提交由同一 Release 注入，不需要新增 GitHub Variables 或 Secrets。Mac 包独立于四种 Linux Docker 镜像，使用 MPS、MLX 与 VideoToolbox；CPU 只作为 AI 加速不可用或执行失败后的自动降级。

这是带安装入口的原生 Python 分发包，首次安装需联网下载锁定的依赖和 FFmpeg；不内置 Python、模型权重或宿主驱动。先安装原生 ARM64 Python 3.12（命令 `python3.12`），不要使用 Rosetta 的 x86_64 Python。安装目录中会生成项目自己的三个虚拟环境与 `.tools`，不会安装全局 Python 包。

下载 DMG 及校验文件到同一目录，先校验，再打开磁盘映像。把其中的 `singverse-compute-macos` 文件夹复制到自己的可写目录（例如 `~/Applications`），弹出磁盘映像后安装；不要在只读映像中运行安装入口。也可使用命令：

```sh
# 将文件名中的 <版本> 替换成实际 Release 数字版本
shasum -a 256 -c singverse-compute-macos-arm64-<版本>.dmg.sha256
hdiutil attach singverse-compute-macos-arm64-<版本>.dmg
mkdir -p ~/Applications
cp -R "/Volumes/SingVerse Compute <版本>/singverse-compute-macos" ~/Applications/
hdiutil detach "/Volumes/SingVerse Compute <版本>"
cd ~/Applications/singverse-compute-macos
sh Install.command
sh Start.command
```

`Install.command` 安装固定 Torch/MPS、独立 CPU 降级环境、MLX 和校验和固定的 FFmpeg，不下载模型，不启动节点。安装后可双击 `Start.command`，或在目录中执行 `python3.12 scripts/macos-install.py start|restart|stop|status`。启动器使用当前用户的 launchd，保留登录启动；停止使用 `Stop.command`。原生节点默认仅监听 `127.0.0.1:18090`。

在 Go 管理端配对原生节点，进入性能节点配置下载或导入对应模型并试跑加速设备与 CPU 降级。Docker 中的 Go 连接 Mac 节点时，可使用 `http://host.docker.internal:18090`；回调地址必须能被原生节点访问。媒体仍通过授权回调读取，计算节点不直接访问业务数据库。模型、缓存、凭据与日志保存在本目录，发布包不携带这些数据。

升级前停止原节点，在原固定路径更新包内程序文件，保留 `data/`、`.tools/` 和三个 `.venv*` 目录，再运行安装与启动命令。不要并行启动不同目录下的节点；现有启动器会拒绝接管另一目录的 launchd 服务。首次安装与升级后的 GPU/NPU 资格需在实际设备试跑，Actions 打包成功不代表 GPU 验收。
