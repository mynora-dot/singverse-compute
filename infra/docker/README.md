# 计算节点 Docker

本仓库独立维护计算节点镜像及 Mac 原生安装包，不构建管理平台。Docker 只提供 CUDA、ROCm、OpenVINO（amd64）和 RKNN（arm64）四类加速镜像，每类内置独立 CPU 降级环境。Mac 使用 [原生安装包](../../MACOS.md)，不增加 Docker 标签。

执行 make docker-context-check docker-test 验证；make docker-build COMPUTE_RUNTIME=cuda 构建；make docker-up 启动预构建镜像；make docker-down 停止并保留卷。`COMPUTE_RUNTIME` 默认 `cuda`，部署前改为主机对应的 `cuda`、`rocm`、`openvino` 或 `rknn`，并追加[硬件覆盖配置](GPU.md)。

源码构建使用 compose.compute.yaml 加 compose.compute-build.yaml；预构建镜像只用前者。Compose 项目名保持 singverse-compute-node，命名卷保留。默认只监听本机；跨主机部署显式设置受限网络地址、凭据、TLS及媒体访问。Go 发起配对和任务，计算节点不直接访问业务数据库。

单独选择计算镜像版本：COMPUTE_VERSION；COMPUTE_RUNTIME 选择运行库。正式版本由 GitHub Release 标签通过 `SINGVERSE_VERSION` 注入，源码不维护 VERSION 文件；本地源码构建默认 `0.0.0`，可显式设置 `SINGVERSE_VERSION`。

## 双仓库发布与项目空间

仅发布正式 GitHub Release 时触发 `singverse-compute-images` 工作流。普通 push/PR、保存草稿及 Pre-release 不发布，也不提供 Run workflow 入口。流程直接构建各运行库的 OCI 产物，将同一内容推送到 GHCR 和 Docker Hub；不运行单元测试、lint 或容器运行验收。

Release 标签使用 `vMAJOR.MINOR.PATCH`，例如 `v0.0.2`；Actions 自动注入版本 `0.0.2`，所有架构锁定该标签的提交。不需要手动配置版本 Variable。先把工作流推送到 GitHub，再创建指向新提交的标签和 Release。

| Actions Variable | 说明 |
| --- | --- |
| `GHCR_NAMESPACE`（可选） | GHCR 用户/组织，默认仓库 owner 的小写名称 |
| `DOCKERHUB_NAMESPACE` | Docker Hub 用户/组织 |

两个平台空间可不同，填写小写名称；缺失配置不会跳过某个平台。

在本仓库配置 Secrets：`DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`。GHCR 默认使用工作流 `GITHUB_TOKEN` 和 `github.actor`；也可配置 `GHCR_TOKEN`、`GHCR_USERNAME`。提前创建/授权 Docker Hub 仓库；跨 GitHub owner/org 推送须配置相应权限。凭据只保存到 Secrets。

固定标签不同内容会拒绝覆盖，所有平台的固定版本成功后更新 `latest-<runtime>`。每个镜像仓库针对一个版本只新增以下四个版本标签，并维护四个 `latest` 标签：

| 硬件 | 固定版本（以 0.0.2 为例） | 跟随版本 | 架构 |
| --- | --- | --- | --- |
| NVIDIA | `0.0.2-cuda` | `latest-cuda` | linux/amd64 |
| AMD | `0.0.2-rocm` | `latest-rocm` | linux/amd64 |
| Intel | `0.0.2-openvino` | `latest-openvino` | linux/amd64 |
| 瑞芯微 | `0.0.2-rknn` | `latest-rknn` | linux/arm64 |

不再发布 `base`、`cpu`、`uvr`、`rocm-native` 或带 `-amd64` / `-arm64` 的额外标签。架构信息保存在镜像清单中，子清单通过 digest 引用。历史版本标签继续保留，因此长期总数会随版本累积；本次修改不会删除仓库中已有的旧标签。

重试使用同一 Release 的已有归档；修复代码后需使用指向新提交的新版本，重跑旧 Actions 不会加载新代码。运行库镜像不内置模型权重。同一工作流另将 Mac ARM64 安装归档和 SHA-256 文件上传到该 Release，首次安装在 Mac 下载锁定依赖，无需新增凭据。

## 从指定仓库部署

复制 `.env.example` 为 `.env`（已有配置不覆盖），选择下列一个 `IMAGE_PREFIX`：

| 来源 | 前缀 |
| --- | --- |
| GHCR | `ghcr.io/<ghcr_namespace>` |
| Docker Hub | `docker.io/<dockerhub_namespace>` |

固定 `COMPUTE_VERSION` 为已发布的数字版本（例如 `0.1.0`，不带 `v`），按硬件设置 `COMPUTE_RUNTIME=cuda|rocm|openvino|rknn`。最终镜像为 `${IMAGE_PREFIX}/singverse-compute:${COMPUTE_VERSION}-${COMPUTE_RUNTIME}`，Docker 按镜像清单检查架构。CPU 仅在已启动的节点内按 `cpuFallback` 策略自动降级，不作为单独的镜像或用户可选 AI 设备。示例版本不保证远端已有产物。

私有仓库在部署主机先执行 `docker login <仓库域名>`。然后从本仓库根执行：

```sh
docker compose --project-directory . -f infra/docker/compose/compose.compute.yaml pull
make docker-up
```

切换仓库前确认同一固定版本的 digest 一致，只改 `IMAGE_PREFIX`，保留 `singverse-compute-node` 项目名和现有卷。Go 仓库的同机组合部署共用一个前缀，发布主服务和 compute 时应选择对应平台的同一空间；独立节点可以使用自己的空间。计算认证、硬件/模型设置和媒体目录按原配置保留。

## 可恢复的发布归档

正式发布在 GitHub Release 保存每个运行库和架构的 OCI 归档。逻辑名为 `${runtime}-${arch}.oci.tar`；实际使用 1 GiB 内容寻址分片，最后发布 `.oci.tar.json` 清单，包含整包及每片的长度和 SHA-256，适配 GitHub 单资产大小限制。重试先校验归档长度与摘要并恢复同一内容，再发布到镜像仓库。中断上传的分片可复用，固定内容从不覆盖；查询失败不会被当成缓存缺失。脚本入口：`python3 infra/docker/release-artifact.py restore|save --name cuda-amd64.oci.tar --archive /tmp/image.tar`，需要 GH_TOKEN、GITHUB_REPOSITORY 与 RELEASE_TAG，版本由该 Release 标签决定。

发布任务执行 `python3 infra/docker/release.py publish --from-release`，每次只恢复一个运行库所需的架构归档，完成两个镜像仓库的固定版本索引及其引用的镜像层后清理本地文件，再处理下一个运行库。全部固定版本成功后统一更新 `latest-<runtime>`，避免一次下载全部 CUDA、ROCm 等大归档耗尽 runner 磁盘。不再重复上传 Actions artifacts；Release 分片归档作为重试来源，缺失或损坏即停止发布，不在发布阶段重新构建。

构建与发布 job 在恢复大归档之前运行 `infra/docker/prepare-runner.sh`，仅在临时 GitHub-hosted Linux runner 上清理不用的 SDK、浏览器和云 CLI，并输出实际释放空间；保留 Python、Node、Git、gh、Docker、Skopeo 与 swap。轻量预检和 Mac 安装包打包任务不做清理，不改分区或合并磁盘。

普通依赖使用 BuildKit pip 缓存挂载，CUDA、ROCm 等加速运行库安装禁用大 wheel 下载缓存。CUDA 的 PyTorch 与 ONNX GPU worker 共用一份锁定版本的 NVIDIA 动态库，在 pip 安装前建立包及元数据链接，避免重复的大库进入任何镜像层；ONNX 仍使用隔离的虚拟环境，安装后执行 `pip check`。CPU 自动降级使用独立环境，不共享 GPU 库。

ROCm 移除旧的 PyTorch 2.5.1 / ROCm 6.2 栈及额外 gfx1103 GPU 虚拟环境，统一使用原生配置已有的 PyTorch / torchaudio `2.11.0+rocm10.0.0` 和一套 SDK 公共库，再安装固定的设备包。具体架构范围见 [GPU.md](GPU.md)；各新增 wheel 同样通过完整文件 SHA-256 固定内容。既不使用浮动 nightly，也不把所有新架构盲目打入镜像。实际压缩体积需新版本构建后确认，设备包本身仍可能较大。

源码、版本元数据在四个最终阶段才加入，因此仅改代码或版本不使 GPU 依赖层失效。上述精简不新增 Actions Variables / Secrets，也不改变四类镜像、8 个标签和双仓库发布规则。

跨版本的构建层缓存存放于 `ghcr.io/<GHCR_NAMESPACE>/singverse-compute-buildcache:<runtime>-<arch>`，使用 registry cache 的 `mode=max`，按运行库和架构隔离。缓存包由现有 GHCR 凭据创建，不需要新增 Actions 参数；已有包需允许本仓库 Actions 读写，跨 owner 的自定义凭据也需覆盖缓存包。缓存不向 Docker Hub 复制，正式 `singverse-compute` 仍每个版本只有 8 个标签。缺失缓存正常构建，缓存导出失败不阻断正式产物，正式产物或归档保存失败仍停止发布。首次构建需要填充缓存；后续依赖层不变时才能复用。

pip 缓存挂载只在当前 BuildKit 中复用，不作为 registry cache 上传；跨 runner 复用的是已安装依赖的构建层。导出 OCI 和远程缓存后清理本地 BuildKit 缓存，再保存 Release 分片。

修改 Dockerfile 或工作流后，需要提交、推送并发布指向新提交的新版本 Release；重跑旧运行仍使用旧版本锁定的源码与工作流，无法应用这些修改。

发布日志逐个显示正在查询或上传的完整镜像标签，直接输出 Skopeo 的镜像层进度，并每 30 秒报告仍在上传的目标与耗时。远端标签查询限时 2 分钟；每次复制限时 30 分钟，期间由 Skopeo 最多重试 2 次。超时或失败会停止发布，固定标签全部成功前不会更新 `latest`。发布 job 总限时 120 分钟。重跑同一 Release 会复用已有的相同摘要标签与归档。
