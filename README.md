# personal-workspace

个人自用的工作台，第一个板块是**个人账本**。前端零外部依赖、断网可用，数据存在本机 MySQL。

```
ledger/
  index.html        单文件前端（HTML + CSS + JS 全内联）
  server.py         本地后端（Python 标准库 + PyMySQL，零框架）
  ledger-init.sql   建库脚本（含 DROP TABLE，慎用）
  smoke-test.js     jsdom 回归测试
  start.command     双击启动服务
  backup.command    双击备份数据库
  使用手册.md        日常使用看这份
  overview.md       架构与开发记录
```

## 快速开始

```bash
cd ledger
python3 server.py --init-conf            # 生成 ~/.ledger.conf（600 权限），填入 MySQL 口令
mysql -uroot -p ledger < ledger-init.sql # 建库（会清空同名库，慎用）
bash start.command                       # 起服务并打开浏览器
```

浏览器打开 `http://127.0.0.1:8765/`。手机同一 WiFi 下用 `http://<电脑内网IP>:8765/`。

## 数据库口令不入库

代码和文档里**不写数据库口令**。口令存放在本机 `~/.ledger.conf`（权限 600，已在 `.gitignore` 中）：

```
db_host=127.0.0.1
db_port=3306
db_user=root
db_password=...
db_name=ledger
```

读取优先级：环境变量 `LEDGER_DB_*` > `~/.ledger.conf` > 内置默认值。
改密码只改这个文件，不用动代码。

## 访问控制

这是个自用工具，服务也只在自己家的局域网里跑，所以没做登录鉴权。实际依赖三道边界：

- **服务默认只监听 `127.0.0.1`**：只有这台电脑能连。要手机访问才用 `--host 0.0.0.0`，且只在自己的家里这么做。
- **不开放 CORS**：任何网站都无法用 JS 读写你 `127.0.0.1` 上的账本（浏览器同源策略）。
- **静态资源走白名单**：只发 `index.html`，`server.py` / 建库脚本 / `backups/*.sql.gz` 一律 404。

公共 WiFi 下别用 `--host 0.0.0.0`——那等于把你的账本开放给同网段任何人。

## 不会丢数据的两道保险

**写库前自动备份。** `POST /api/db` 是「先清表再全量写回」，一次坏写入就足以清空账本。所以每次写库前服务端先把当前状态存成 `ledger/backups/auto-*.json.gz`，格式与应用内「导出 JSON」一致，可直接导入回来。默认 300 秒最多一次、保留 30 份，启动时若超过 24 小时没备份会先补一份。参数在 `~/.ledger.conf`：`auto_backup` / `auto_backup_interval` / `auto_backup_keep` / `backup_dir`。

**`--read-only`。** 以此启动时拒绝一切写入（POST 返回 403），排查和验证脚本时用它确保不会碰到真库。

## 已知限制

- **全量快照同步**：手机与电脑同时使用会互相覆盖，暂未做冲突合并。这是目前最大的遗留风险。
- **数据库口令仍是弱口令**：本机 MySQL 用的是简单口令，仓库里虽然不再明文暴露，但建议换成强口令。

## 回归测试

```bash
npm install && npm test    # 98 项检查；jsdom 不入库，需先 npm install
```