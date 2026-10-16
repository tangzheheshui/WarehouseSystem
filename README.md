# 仓库管理系统

Python 标准库 + SQLite + 原生 Web 前端实现，无第三方运行依赖。

## 功能

- 管理员：商品信息、仓库信息、项目管理、项目管理员创建和授权。
- 项目管理员：入库、出库、移库、库存查询导出、拣货单、模板管理、操作日志。
- 移库：同项目内批次在货位间一步移库，支持 Excel 导入与手动录入，独立移库模板与移库单查询。
- 多项目库存、单据、模板、日志隔离；商品和仓库档案公司共享。
- 姓名全拼登录，手机号仅作联系方式。
- 入库/出库支持 Excel 导入与手动录入；入库通知单号、配货单号用户自定义且唯一。
- 到期日期自动计算（生产日期+保质天数，或直接填写到期日期）。
- 出库按可用库存校验、先进先出扣减，支持指定货位、生产批号、生产日期约束。
- 拣货单以配货单号分组，A4 分页打印，支持按货位/按货品编号排序。
- 库存查询区分汇总（按货品编号）与明细（按货位）双视图，支持自定义显示/隐藏列。
- 项目创建时绑定唯一仓库与库区；商品归属唯一项目，在「项目信息」的项目详情内管理（V7 前为公司共享档案 + 项目授权）。
- 项目历史库存 CSV 一次性初始化导入。
- 多公司同服务器部署，每家公司独立子域名：`<公司标识>.wms.tangzheheshui.cn`。
- 万顺控制台（V5）：平台管理员在 `console.wms.tangzheheshui.cn` 创建公司（一次性初始密码）、停用/启用公司、应急重置公司管理员密码；单进程按 Host 分发，每家公司仍是独立 SQLite 库。
- 品牌（V5）：登录页与浏览器标题显示「万顺WMS仓库管理系统」，第二行显示本公司名称；公司管理员可在系统设置里修改公司名称。

需求见 [requirements.md](docs/requirements.md)（V2/V3/V4/V5 合并，含事项列表与待确认清单）。

## 本地运行

```bash
./scripts/run.sh
```

访问：<http://127.0.0.1:8000/>

默认管理员：

- 用户名：`admin`
- 初始密码：`Admin123456`

首次登录会强制修改密码。生产初始化可设置：

```bash
WMS_ADMIN_NAME=张三 WMS_ADMIN_USERNAME=zhangsan WMS_ADMIN_PASSWORD='初始密码' ./scripts/run.sh
```

### 平台模式（多公司 + 万顺控制台）

```bash
WMS_PLATFORM_ADMIN_USERNAME=wanshun WMS_PLATFORM_ADMIN_PASSWORD='初始密码' \
  python3 server/main.py --platform --domain wms.test
```

- 控制台：<http://console.wms.test/>（本机联调可改 hosts 指向 127.0.0.1）
- 公司子域名：`<公司前缀>.wms.test`，在控制台创建公司后立即可访问
- 未设置平台管理员密码时首启生成随机密码并仅打印一次

生产部署（泛域名证书、nginx、systemd）见 [deployment.md](docs/deployment.md) 的「V5 平台模式」章节。

### 本地演示数据（可复原）

```bash
python3 seed_demo.py
./scripts/run.sh --platform --domain localhost --port 8000
```

一键得到一家「ACME物流」演示公司（控制台 `http://console.localhost:8000`，账号 `wanshun / Local123456`；公司端 `http://acme.localhost:8000`），内含仓库、5 个货位、2 个项目、5 个商品、2 个项目管理员账号、6 张入库单、1 张出库单（待分拣）和对应库存，效期按保质天数自动计算。账号密码见 [seed_demo.py](seed_demo.py) 头部常量。

脚本幂等可重复执行；数据写在 `data/`（不入 git）。换电脑后 clone 仓库 → 重跑本脚本 → 启动服务，即得同一套演示数据。

### 固定假数据预览

```bash
scripts/dev-preview.sh
```

- 地址：<http://127.0.0.1:8000/>
- 项目管理员：`preview / Preview123456`，查看项目切换、出入库、库存等业务界面，登录后不强制改密。
- 系统管理员：`admin / PreviewAdmin123`，查看商品、仓库、项目、用户管理。
- 预览数据库固定存放在 `.preview/warehouse.db`（已忽略版本控制）；首次启动自动初始化 1 个默认项目 + 4 个示例项目，`preview` 授权全部项目，可直接验证项目切换器与多项目数据隔离；再次启动直接复用，秒级完成。
- 重置假数据：`rm -rf .preview && scripts/dev-preview.sh`；更换端口：`scripts/dev-preview.sh 8001`。

## 测试

```bash
./scripts/run-tests.sh
```

覆盖 Excel 读写、登录锁定、权限、项目隔离、导入校验、出入库、到期日计算、历史 CSV、V1 迁移、访问前缀，以及 V5 多公司 Host 分发、控制台鉴权与公司创建/停启用/管理员重置全流程。测试使用临时数据库，自动创建测试账号，不依赖生产环境或 `acme`。

测试说明和详细用例见 [tests/README.md](tests/README.md)、[test-cases.md](tests/cases/test-cases.md)。

## 部署

公司放子域名前缀（SaaS 多租户惯例）：`acme.wms.tangzheheshui.cn`。DNSPod 添加泛解析：`*.wms` A 记录指向服务器 IP（再加一条 `wms` 做落地页）。

第一家公司：

```bash
./scripts/deploy.sh acme 193.112.26.217 张三 zhangsan
```

访问：

```text
https://acme.wms.tangzheheshui.cn/
```

第二家公司自动选择内部端口，证书按子域名自动签发，无需再动 DNS：

```bash
./scripts/deploy.sh beta 193.112.26.217 李四 lisi
```

访问：

```text
https://beta.wms.tangzheheshui.cn/
```

更多说明见 [deployment.md](docs/deployment.md) 和 [operation-manual.md](docs/operation-manual.md)。
