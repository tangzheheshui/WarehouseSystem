# 仓库管理系统

Python 标准库 + SQLite + 原生 Web 前端实现，无第三方运行依赖。

## 功能

- 管理员：商品信息、仓库信息、项目管理、项目管理员创建和授权。
- 项目管理员：入库、出库、库存查询导出、拣货单、模板管理、操作日志。
- 多项目库存、单据、模板、日志隔离；商品和仓库档案公司共享。
- 姓名全拼登录，手机号仅作联系方式。
- 入库/出库支持 Excel 导入与手动录入；入库通知单号、配货单号用户自定义且唯一。
- 到期日期自动计算（生产日期+保质天数，或直接填写到期日期）。
- 出库按可用库存校验、先进先出扣减，支持指定货位、生产批号、生产日期约束。
- 拣货单以配货单号分组，A4 分页打印，支持按货位/按货品编号排序。
- 库存查询区分汇总（按货品编号）与明细（按货位）双视图，支持自定义显示/隐藏列。
- 项目创建时绑定唯一仓库与库区；商品维护与项目授权独立，商品须添加到项目后方可作业。
- 项目历史库存 CSV 一次性初始化导入。
- 多公司同服务器 80 端口部署，访问地址为 `/公司标识/`。

需求见 [requirements.md](docs/requirements.md)（V2/V3/V4 合并，含事项列表与待确认清单）。

## 本地运行

```bash
./run.sh
```

访问：<http://127.0.0.1:8000/>

默认管理员：

- 用户名：`admin`
- 初始密码：`Admin123456`

首次登录会强制修改密码。生产初始化可设置：

```bash
WMS_ADMIN_NAME=张三 WMS_ADMIN_USERNAME=zhangsan WMS_ADMIN_PASSWORD='初始密码' ./run.sh
```

### 固定假数据预览

```bash
scripts/dev-preview.sh
```

使用固定账号 `preview / Preview123456`（项目管理员）和 `admin / PreviewAdmin123`（系统管理员），假数据持久化在 `.preview/`，重复启动秒级完成。详见 [local-preview.md](docs/local-preview.md)。

## 测试

```bash
./tests/scripts/run.sh
```

覆盖 Excel 读写、登录锁定、权限、项目隔离、导入校验、出入库、到期日计算、历史 CSV、V1 迁移和访问前缀。测试使用临时数据库，自动创建测试账号，不依赖生产环境或 `acme`。

测试说明和详细用例见 [tests/README.md](tests/README.md)、[test-cases.md](tests/cases/test-cases.md)。

## 部署

第一家公司：

```bash
./deploy.sh acme 193.112.26.217 张三 zhangsan
```

访问：

```text
http://193.112.26.217/acme/
```

第二家公司自动选择内部端口，公网仍使用 80：

```bash
./deploy.sh beta 193.112.26.217 李四 lisi
```

访问：

```text
http://193.112.26.217/beta/
```

更多说明见 [deployment.md](docs/deployment.md) 和 [operation-manual.md](docs/operation-manual.md)。
