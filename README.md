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

## 已知限制

- **API 无鉴权**：局域网内任何人可读写账本。公共 WiFi 下别开着，详见 `ledger/使用手册.md`。
- **全量快照同步**：手机与电脑同时使用会互相覆盖，暂未做冲突合并。
- **备份靠自觉**：`backup.command` 需手动或定时任务触发。

## 回归测试

```bash
npm i -D jsdom && node smoke-test.js   # 依赖 jsdom，仓库不包含 node_modules
```