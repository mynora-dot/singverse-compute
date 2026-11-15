# 计算节点 Docker

本仓库独立维护计算节点镜像及运行库，不构建管理平台。默认 base 支持两种 CPU 架构；cpu、uvr 同样支持 amd64/arm64，CUDA、ROCm、OpenVINO 为 amd64，RKNN 为 arm64。

执行 make docker-context-check docker-test 验证；make docker-build COMPUTE_RUNTIME=base 构建；make docker-up 启动预构建镜像；make docker-down 停止并保留卷。

源码构建使用 compose.compute.yaml 加 compose.compute-build.yaml；预构建镜像只用前者。Compose 项目名保持 singverse-compute-node，命名卷保留。默认只监听本机；跨主机部署显式设置受限网络地址、凭据、TLS及媒体访问。Go 发起配对和任务，计算节点不直接访问业务数据库。

单独选择计算镜像版本：COMPUTE_VERSION；COMPUTE_RUNTIME 选择运行库。正式版本由 GitHub Release 标签通过 `SINGVERSE_VERSION` 注入，源码不维护 VERSION 文件；本地源码构建默认 `0.0.0`，可显式设置 `SINGVERSE_VERSION`。

## 三平台发布与项目空间

仅发布正式 GitHub Release 时触发 `singverse-compute-images` 工作流。普通 push/PR、保存草稿及 Pre-release 不发布，也不提供 Run workflow 入口。流程直接构建各运行库的 OCI 产物，将同一内容推送到 GHCR、Docker Hub 和阿里云 ACR；不运行单元测试、lint 或容器运行验收。

Release 标签使用 `vMAJOR.MINOR.PATCH`，例如 `v0.0.2`；Actions 自动注入版本 `0.0.2`，所有架构锁定该标签的提交。不需要手动配置版本 Variable。先把工作流推送到 GitHub，再创建指向新提交的标签和 Release。

| Actions Variable | 说明 |
| --- | --- |
| `GHCR_NAMESPACE`（可选） | GHCR 用户/组织，默认仓库 owner 的小写名称 |
| `DOCKERHUB_NAMESPACE` | Docker Hub 用户/组织 |
| `ACR_REGISTRY` | ACR 控制台提供的实际域名，不含协议、路径或末尾斜杠 |
| `ACR_NAMESPACE` | ACR 实例内的命名空间 |

三个平台空间可不同，填写小写名称。ACR 使用 GitHub runner 可访问的地址；缺失配置不会跳过某个平台。

在本仓库配置 Secrets：`DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`、`ACR_USERNAME`、`ACR_PASSWORD`。GHCR 默认使用工作流 `GITHUB_TOKEN` 和 `github.actor`；也可配置 `GHCR_TOKEN`、`GHCR_USERNAME`。提前创建/授权 Docker Hub、ACR 仓库；跨 GitHub owner/org 推送须配置相应权限。凭据只保存到 Secrets。

固定标签不同内容会拒绝覆盖，所有平台的固定版本成功后更新 `latest-<runtime>`。重试使用同一 Release 的已有归档；修复代码后需使用指向新提交的新版本，重跑旧 Actions 不会加载新代码。运行库镜像不内置模型权重。

## 从指定仓库部署

复制 `.env.example` 为 `.env`（已有配置不覆盖），选择下列一个 `IMAGE_PREFIX`：

| 来源 | 前缀 |
| --- | --- |
| GHCR | `ghcr.io/<ghcr_namespace>` |
| Docker Hub | `docker.io/<dockerhub_namespace>` |
| 阿里云 ACR | `<acr_registry>/<acr_namespace>` |

固定 `COMPUTE_VERSION` 为已发布的数字版本（例如 `0.1.0`，不带 `v`），`COMPUTE_RUNTIME=base` 或所需运行库。最终镜像为 `${IMAGE_PREFIX}/singverse-compute:${COMPUTE_VERSION}-${COMPUTE_RUNTIME}`；架构标签为 `<版本>-<运行库>-<架构>`，多架构标签自动匹配 amd64/arm64。示例版本不保证远端已有产物。

私有仓库在部署主机先执行 `docker login <仓库域名>`。然后从本仓库根执行：

```sh
docker compose --project-directory . -f infra/docker/compose/compose.compute.yaml pull
make docker-up
```

切换仓库前确认同一固定版本的 digest 一致，只改 `IMAGE_PREFIX`，保留 `singverse-compute-node` 项目名和现有卷。Go 仓库的同机组合部署共用一个前缀，发布主服务和 compute 时应选择对应平台的同一空间；独立节点可以使用自己的空间。计算认证、硬件/模型设置和媒体目录按原配置保留。

## 可恢复的发布归档

正式发布在 GitHub Release 保存每个运行库和架构的 OCI 归档。逻辑名为 `${runtime}-${arch}.oci.tar`；实际使用 1 GiB 内容寻址分片，最后发布 `.oci.tar.json` 清单，包含整包及每片的长度和 SHA-256，适配 GitHub 单资产大小限制。重试先校验归档长度与摘要并恢复同一内容，再发布到镜像仓库。中断上传的分片可复用，固定内容从不覆盖；查询失败不会被当成缓存缺失。脚本入口：`python3 infra/docker/release-artifact.py restore|save --name base-amd64.oci.tar --archive /tmp/image.tar`，需要 GH_TOKEN、GITHUB_REPOSITORY 与 RELEASE_TAG，版本由该 Release 标签决定。
