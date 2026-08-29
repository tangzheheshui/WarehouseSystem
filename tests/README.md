# 测试目录说明

## 目录结构

```text
tests/
├── README.md                  # 测试入口说明
├── cases/                     # 测试用例
│   ├── test-cases.md          # 中文详细测试用例
│   └── test_*.py              # 自动化测试用例
├── data/                      # 测试数据
│   ├── warehouse_data.py      # 临时库基础数据和 Excel 数据构造
│   └── excel/                 # 本地可查看的 xlsx 样例
└── scripts/                   # 测试脚本
    ├── run.sh                 # 本地全量自动化测试入口
    ├── build_excel_fixtures.py # 生成本地 Excel 样例
    └── acme_real_test.py      # acme 真实环境端到端验收
```

## 依赖边界

| 入口 | 数据库 | 账号 | 是否连 `acme` |
| --- | --- | --- | --- |
| `scripts/run.sh` | 每个用例临时创建并自动删除 | 自动创建 `zhangsan`、`liuhaifeng` 测试夹具账号 | 否 |
| `scripts/build_excel_fixtures.py` | 临时创建并自动删除 | 不登录业务系统 | 否 |
| `scripts/acme_real_test.py` | 使用 `acme` 真实库 | 需要真实管理员和项目管理员密码 | 是，可选 |

`zhangsan`、`liuhaifeng` 在本地自动化里只是临时库夹具名称，每次由 `WarehouseDataFactory` 创建，测试后随临时目录删除；它们不是环境依赖，也不要求与 `acme` 里的同名账号使用相同密码。

## 分类说明

- `cases/` 存放测试用例：`test-cases.md` 是人工评审和验收用的中文用例；`test_*.py` 是可执行用例。
- `data/` 存放测试数据构造逻辑和本地样例：`warehouse_data.py` 每次创建临时 SQLite 数据库，自动生成账号、项目、商品、仓库、货位，并按模板生成入库、出库 Excel。
- `data/excel/` 存放 6 个本地 xlsx：入库/出库模板、有效数据、错误数据各一套，供人工打开检查和手工测试使用。
- `scripts/` 存放执行脚本：统一封装测试命令，不在 `tests/` 根目录放 Python 文件。
- 自动化测试不依赖 `data/excel/` 的静态文件，而是在运行时按当前项目模板动态构造，避免模板改列后旧表头导致误判。

## 执行测试

```bash
./tests/scripts/run.sh
```

生成本地 Excel 样例：

```bash
python3 tests/scripts/build_excel_fixtures.py
```

执行 `acme` 真实环境验收（可选，仅用于部署后检查；不在 `run.sh` 中执行）：

```bash
python3 tests/scripts/acme_real_test.py \
  --admin-password '<zhangsan 当前测试密码>' \
  --project-password '<liuhaifeng 当前测试密码>'
```

本地自动化测试使用临时数据库，不需要 `acme`，也不需要任何真实环境账号。`acme` 是独立的真实测试环境：先做只读冒烟，再使用带 `AUTO-` 前缀和唯一时间戳的数据做端到端验收；测试单据保留用于追溯，不清理或覆盖已有业务数据。

详细用例见 [cases/test-cases.md](cases/test-cases.md)。
