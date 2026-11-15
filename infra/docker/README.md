# 计算节点 Docker

本仓库独立维护计算节点镜像及运行库，不构建管理平台。默认 base 支持两种 CPU 架构；cpu、uvr 同样支持 amd64/arm64，CUDA、ROCm、OpenVINO 为 amd64，RKNN 为 arm64。

执行 make docker-context-check docker-test 验证；make docker-build COMPUTE_RUNTIME=base 构建；make docker-up 启动预构建镜像；make docker-down 停止并保留卷。

源码构建使用 compose.compute.yaml 加 compose.compute-build.yaml；预构建镜像只用前者。Compose 项目名保持 singverse-compute-node，命名卷保留。默认只监听本机；跨主机部署显式设置受限网络地址、凭据、TLS及媒体访问。Go 发起配对和任务，计算节点不直接访问业务数据库。

单独选择计算镜像版本：COMPUTE_VERSION；COMPUTE_RUNTIME 选择运行库。源构建产品版本只读取根 VERSION。

## 三平台发布与项目空间

正式 GitHub Release 触发 `singverse-compute-images` 工作流，按实际架构构建并验证一次 OCI 产物，再将同一内容推送到 GitHub Container Registry（GHCR）、Docker Hub 和阿里云 ACR。GitHub Release 附件保存验收归档，GHCR 保存可拉取镜像。

也可在 Actions → singverse-compute-images → Run workflow 选择已有正式 Release 及本次目标空间：

| 参数 | 留空时使用 | 说明 |
| --- | --- | --- |
| `release_tag` | 不发布，仅构建检查 | 已发布、非草稿、非 prerelease 的 Release tag，例如 `v0.1.0` |
| `ghcr_namespace` | Variable `GHCR_NAMESPACE`，再回退仓库 owner 的小写名称 | GHCR 用户/组织 |
| `dockerhub_namespace` | Variable `DOCKERHUB_NAMESPACE` | Docker Hub 用户/组织 |
| `acr_registry` | Variable `ACR_REGISTRY` | ACR 实例的实际域名，例如 `registry.cn-hangzhou.aliyuncs.com` |
| `acr_namespace` | Variable `ACR_NAMESPACE` | ACR 实例内的命名空间 |

手动参数优先，只影响这次运行；自动正式 Release 使用 Variables。三个平台空间可不同，填写小写名称，不含域名或路径；`acr_registry` 不含协议和末尾斜杠，应填写 GitHub runner 可访问的实例地址。缺失配置不会跳过某个平台。`main` 的 push、pull request 和未填写 `release_tag` 的手动运行均不推送镜像。

在本仓库配置 Secrets：`DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`、`ACR_USERNAME`、`ACR_PASSWORD`。GHCR 默认使用工作流 `GITHUB_TOKEN` 和 `github.actor`；也可配置 `GHCR_TOKEN`、`GHCR_USERNAME` 使用有目标空间权限的账号。命名空间参数不授予访问权限，提前创建/授权 Docker Hub、ACR 仓库；跨 GitHub owner/org 推送须配置相应权限。凭据只保存到 Secrets。

手动选择的 tag 必须等于该 tag 源码根 `VERSION` 前加 `v`，所有架构锁定相同提交，验收 revision 也使用该提交。先将新工作流合入默认分支，再选择包含新工作流的分支触发，例如（替换为实际仓库、版本和空间）：

```sh
gh workflow run singverse-compute-images.yml --repo your-org/singverse-compute \
  -f release_tag=v0.1.0 \
  -f ghcr_namespace=your-org \
  -f dockerhub_namespace=your-team \
  -f acr_registry=registry.cn-hangzhou.aliyuncs.com \
  -f acr_namespace=your-project
```

固定标签不同内容会拒绝覆盖，所有平台的固定版本成功后更新 `latest-<runtime>`。手动选择历史 Release 也会将目标空间的浮动标签指向该历史版本。重试或向新空间补发使用同一 Release 的已验收归档。运行库镜像不内置模型权重。

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

正式发布在 GitHub Release 保存每个运行库和架构的已验收 OCI 归档。逻辑名为 `${runtime}-${arch}.oci.tar`；实际使用 1 GiB 内容寻址分片，最后发布 `.oci.tar.json` 清单，包含整包及每片的长度和 SHA-256，适配 GitHub 单资产大小限制。重试先恢复同一归档并重新执行版本、revision、运行库和 OCI 校验，再发布到镜像仓库。中断上传的分片可复用，固定内容从不覆盖；查询失败不会被当成缓存缺失。脚本入口：`python3 infra/docker/release-artifact.py restore|save --name base-amd64.oci.tar --archive /tmp/image.tar`，需要 GH_TOKEN、GITHUB_REPOSITORY 与 RELEASE_TAG，标签必须匹配根 VERSION。
