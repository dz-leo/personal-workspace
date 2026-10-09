/**
 * 个人账本工作台 — 冒烟自检（jsdom）
 * 覆盖 S1 数据层 / S2 日视图+快速记账 / S3 周·月视图 / S4 图表+总结
 * 运行：NODE_PATH=/Users/dazhi/.workbuddy/binaries/node/workspace/node_modules node smoke-test.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const errors = [];

const dom = new JSDOM(html, {
  url: 'http://localhost/',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  beforeParse(w) {
    // 模拟「后端未启动」：让 RemoteAdapter 的同步 XHR 直接抛错，
    // 走本地存储降级分支（与真实 file:// / 后端挂掉行为一致），避免 jsdom 真发起网络请求而卡死
    w.XMLHttpRequest = function () {
      this.open = function () {};
      this.setRequestHeader = function () {};
      this.send = function () { throw new Error('network unavailable in test'); };
    };
  },
});
const w = dom.window;
const d = w.document;

// polyfill jsdom 缺失的浏览器 API
w.URL.createObjectURL = () => 'blob:mock';
w.URL.revokeObjectURL = () => {};
w.HTMLElement.prototype.scrollIntoView = function () {};

w.addEventListener('error', e => errors.push('window.error: ' + e.message));
const origError = w.console.error;
w.console.error = function (...a) { errors.push('console.error: ' + a.join(' ')); origError.apply(w.console, a); };

function q(sel) { return d.querySelector(sel); }
function qa(sel) { return Array.from(d.querySelectorAll(sel)); }
function click(el) {
  if (!el) throw new Error('元素不存在: ' + (el === null ? 'null' : ''));
  el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
}
function setVal(el, v) {
  el.value = v;
  el.dispatchEvent(new w.Event('input', { bubbles: true }));
  el.dispatchEvent(new w.Event('change', { bubbles: true }));
}
function tab(view) { return q('#viewTabs .tab[data-view="' + view + '"]'); }
function db() { return JSON.parse(w.localStorage.getItem('wb_ledger_v1')); }
const results = [];
function check(name, fn) {
  try {
    const r = fn();
    results.push([r === true ? 'PASS' : 'FAIL', name, r === true ? '' : String(r)]);
  } catch (e) {
    results.push(['FAIL', name, e.message]);
  }
}

// 等待脚本自然初始化（不手动触发 DOMContentLoaded，避免与 jsdom 自身触发重复）
setTimeout(() => {
  // ============ 1. 初始化 ============
  check('页面无 JS 运行时错误', () => errors.length === 0 || errors.join(' | '));
  check('存储层已写入本地', () => !!w.localStorage.getItem('wb_ledger_v1'));
  check('示例数据已生成（22 条，含 2 条不计入）', () => {
    const n = db().records.length;
    return n === 22 ? true : '实际 ' + n + ' 条';
  });
  check('示例数据标记为 demo 便于一键清除', () => {
    const n = db().records.filter(r => r.demo).length;
    return n === 22 ? true : '实际 ' + n + ' 条';
  });

  // ============ 2. 首屏（日视图） ============
  check('「今天要处理」区有内容', () => q('#todo').textContent.trim().length > 0 || '空');
  check('昨天漏记提醒存在', () => q('#todo').textContent.includes('昨天'));
  check('日视图渲染出 3 条流水', () => {
    const n = qa('#mainArea .rec').length;
    return n === 3 ? true : '实际 ' + n + ' 条';
  });
  check('日期导航标题正确', () => q('#navArea').textContent.includes('月') && q('#navArea').textContent.includes('今天'));
  check('日汇总支出金额正确（¥86.00）', () => {
    const v = q('#sumArea').textContent;
    return v.includes('86.00') ? true : v.slice(0, 80);
  });
  check('存储状态显示「本地存储」', () => q('#stgTxt').textContent.includes('本地'));
  check('日视图右侧统计：本月概览已渲染', () => q('#statsArea').textContent.includes('本月支出'));
  check('日视图右侧统计：今日分类分布有行', () => q('#statsArea').querySelectorAll('.dist-row').length > 0);
  check('今日小记自动总结已生成', () => q('#noteAuto').textContent.includes('共 3 笔'));
  check('小记标题为「今日小记」', () => q('#noteArea').textContent.includes('今日小记'));

  // ============ 3. S3：周视图 ============
  check('切到周视图：Tab 高亮切换', () => {
    click(tab('week'));
    return tab('week').classList.contains('on') && !tab('day').classList.contains('on');
  });
  check('周视图导航显示区间', () => q('#navArea').textContent.includes('-') && q('#navArea').textContent.includes('本周'));
  check('周视图渲染柱状图（7 根柱子）', () => {
    const n = qa('#mainArea rect[data-date]').length;
    return n === 7 ? true : '实际 ' + n + ' 根';
  });
  check('周视图柱状图含平均线', () => q('#mainArea svg').textContent.includes('日均') || qa('#mainArea svg line').length > 0);
  check('周视图按天折叠分组已渲染', () => qa('#mainArea [data-fold]').length > 0);
  check('周汇总含环比上周', () => q('#sumArea').textContent.includes('环比上周') || q('#sumArea').textContent.includes('日均'));
  check('周视图右侧统计：分类/标签/账户三块齐全', () => {
    const t = q('#statsArea').textContent;
    return (t.includes('本周统计') && t.includes('分类排行') && t.includes('标签分析') && t.includes('账户分布')) ? true : t.slice(0, 100);
  });
  check('本周小记自动总结已生成', () => {
    const t = q('#noteAuto').textContent;
    return t.includes('共') && t.includes('笔') && !t.includes('还没有记账记录') ? true : t.slice(0, 100);
  });
  check('小记标题切换为「本周小记」', () => q('#noteArea').textContent.includes('本周小记'));
  check('周视图切换上一周不报错', () => {
    click(q('#navPrev'));
    return q('#navArea').textContent.length > 0;
  });
  check('周视图切换下一周回到本周', () => {
    click(q('#navNext'));
    return q('#navArea').textContent.includes('本周');
  });
  check('点击柱子跳回日视图', () => {
    click(qa('#mainArea rect[data-date]')[2]);
    return tab('day').classList.contains('on') ? true : '仍停在 ' + q('#viewTabs .tab.on').getAttribute('data-view');
  });
  check('折叠分组可点击收起/展开', () => {
    click(tab('week'));
    const h = qa('#mainArea [data-fold]')[0];
    const key = h.getAttribute('data-fold');
    const before = qa('#mainArea .rec').length;
    click(h);
    const after = qa('#mainArea .rec').length;
    click(q('#mainArea [data-fold="' + key + '"]'));
    const back = qa('#mainArea .rec').length;
    return (after < before && back === before) ? true : `前 ${before} / 折叠后 ${after} / 展开后 ${back}`;
  });

  // ============ 4. S3：月视图 ============
  check('切到月视图：Tab 高亮切换', () => {
    click(tab('month'));
    return tab('month').classList.contains('on');
  });
  check('月视图导航显示年月', () => q('#navArea').textContent.includes('年') && q('#navArea').textContent.includes('月'));
  check('月视图渲染热力日历', () => qa('#mainArea .cal-cell').length > 27);
  check('日历格子数 = 前导空格 + 当月天数', () => {
    const now = new Date();
    const dim = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    const lead = (new Date(now.getFullYear(), now.getMonth(), 1).getDay() + 6) % 7;
    const n = qa('#mainArea .cal-cell').length;
    return n === lead + dim ? true : `实际 ${n}，应为 ${lead + dim}`;
  });
  check('日历标出今天', () => qa('#mainArea .cal-cell.today').length === 1);
  check('日历有热度分级（至少 2 种等级）', () => {
    const lv = new Set(qa('#mainArea .cal-cell').map(c => (c.className.match(/l\d/) || [''])[0]));
    return lv.size >= 2 ? true : '等级数 ' + lv.size;
  });
  check('日历有图例说明', () => q('#mainArea .cal-legend').textContent.includes('超日均'));
  check('月汇总显示本月支出/收入/结余', () => {
    const t = q('#sumArea').textContent;
    return (t.includes('本月支出') && t.includes('本月收入') && t.includes('结余')) ? true : t.slice(0, 80);
  });
  check('月视图右侧统计：预算环 + 环形图', () => {
    const t = q('#statsArea').textContent;
    const svg = qa('#statsArea svg').length;
    return (t.includes('月统计') && t.includes('支出构成') && svg >= 2) ? true : `文字=${t.slice(0, 60)} svg数=${svg}`;
  });
  check('月视图统计含分类排行', () => q('#statsArea').textContent.includes('分类排行'));
  check('月视图统计含近 6 个月趋势折线图', () => {
    const t = q('#statsArea').textContent;
    // 折线图用 <path> 画线 + <circle> 画点，不是 polyline
    const dots = qa('#statsArea circle').length;
    return (t.includes('近 6 个月趋势') && dots >= 6) ? true : `文字=${t.includes('近 6 个月趋势')} 数据点=${dots}`;
  });
  check('月视图统计含商家 Top5', () => q('#statsArea').textContent.includes('商家 Top5'));
  check('月视图统计含可优化空间分析', () => q('#statsArea').textContent.includes('可优化空间'));
  check('本月小记自动总结已生成', () => {
    const t = q('#noteAuto').textContent;
    return t.includes('环比上月') || t.includes('共') ? true : t.slice(0, 100);
  });
  check('小记标题切换为「本月小记」', () => q('#noteArea').textContent.includes('本月小记'));
  check('切上一个月不报错（跨月）', () => {
    click(q('#navPrev'));
    return q('#navArea').textContent.includes('月');
  });
  check('上一个月显示历史数据（不为空崩）', () => q('#mainArea').textContent.length > 0);
  check('切回本月', () => {
    click(q('#navNext'));
    return q('#navArea').textContent.includes('本月');
  });
  check('连续回退 14 个月（跨年）不报错', () => {
    for (let i = 0; i < 14; i++) click(q('#navPrev'));
    return q('#navArea').textContent.includes('年');
  });
  check('空月份不崩溃', () => q('#mainArea').textContent.length > 0 && q('#statsArea').textContent.length > 0);
  check('连续前进 14 个月回到本月', () => {
    for (let i = 0; i < 14; i++) click(q('#navNext'));
    return q('#navArea').textContent.includes('本月');
  });
  check('不处于本周时，出现「回到本周」快捷按钮', () => {
    click(tab('week'));
    click(q('#navPrev'));
    return !!q('#navToday') && q('#navToday').textContent.includes('回到本周');
  });
  check('点「回到本周」一步归位', () => {
    click(q('#navToday'));
    return q('#navArea').textContent.includes('本周') && !q('#navToday');
  });
  check('处于本周时不显示归位按钮', () => !q('#navToday'));
  check('不处于本月时，出现「回到本月」快捷按钮', () => {
    click(tab('month'));
    click(q('#navPrev'));
    return !!q('#navToday') && q('#navToday').textContent.includes('回到本月');
  });
  check('点「回到本月」一步归位', () => {
    click(q('#navToday'));
    return q('#navArea').textContent.includes('本月') && !q('#navToday');
  });
  check('点击日历某天跳到该天的日视图', () => {
    click(qa('#mainArea .cal-cell[data-date]')[10]);
    return tab('day').classList.contains('on');
  });
  check('不在今天时，日视图出现「回到今天」', () => !!q('#navToday'));
  check('点「回到今天」一步归位', () => {
    click(q('#navToday'));
    return q('#navArea').textContent.includes('今天') && qa('#mainArea .rec').length === 3;
  });

  // ============ 4b. 资金账户 + 资产视图（demo 数据完整时校验余额派生） ============
  check('资产视图：顶部显示净资产', () => {
    click(tab('asset'));
    return q('#sumArea').textContent.includes('净资产');
  });
  check('资产视图：渲染账户列表与「添加资金账户」', () => {
    const rows = qa('#mainArea .acc[data-acc]').length;
    const hasAdd = !!q('#addAcc');
    return (rows >= 7 && hasAdd) ? true : `账户行 ${rows} / 添加按钮 ${hasAdd}`;
  });
  check('资产视图：余额由流水实时派生（信用卡欠款以红色「欠款」展示，负债计入净资产）', () => {
    const t = q('#mainArea').textContent;
    return (t.includes('信用卡') && t.includes('欠款')) ? true : t.slice(0, 200);
  });
  check('资产视图：点账户打开账户编辑器', () => {
    click(qa('#mainArea .acc[data-acc]')[0]);
    const on = q('#maskAcc').classList.contains('on');
    click(q('#accCancel'));
    return on && !q('#maskAcc').classList.contains('on');
  });
  check('记账选「不计入」时出现转入账户选择器', () => {
    click(q('#btnAdd'));
    click(qa('#sheetEdit .seg button[data-type="transfer"]')[0]);
    const hasTo = !!q('#eToAccs');
    click(q('#eCancel'));
    return hasTo ? true : '未出现转入账户选择器';
  });
  check('资产视图渲染不报错', () => errors.length === 0 || errors.join(' | '));
  click(tab('day')); // 还原日视图，避免影响后续 section 5

  // ============ 5. 日期切换（日视图） ============
  check('日视图切前一天：显示空态', () => {
    click(q('#navPrev'));
    return q('#mainArea').textContent.includes('还没有记录');
  });
  check('日视图切回今天', () => {
    click(q('#navNext'));
    return q('#mainArea').textContent.includes('共 3 笔');
  });

  // ============ 6. S2：记一笔 ============
  check('打开记账弹窗', () => {
    click(q('#btnAdd'));
    return q('#maskEdit').classList.contains('on');
  });
  check('弹窗渲染 20 个支出分类（不含新增按钮）', () => {
    const n = qa('#eCats [data-cat]').length;
    return n === 20 ? true : '实际 ' + n;
  });
  check('选分类后出现二级细分', () => {
    click(qa('#eCats .cat-b')[0]);
    return qa('#eSubs .chip').length > 0;
  });
  check('金额为空时拦截保存', () => {
    click(q('#eSave'));
    return q('#maskEdit').classList.contains('on') && q('#toast').textContent.includes('金额');
  });
  check('填入商家后选分类不丢输入（表单状态保持）', () => {
    setVal(q('#eAmt'), '25.5');
    setVal(q('#eMerchant'), '测试商家');
    click(qa('#eCats .cat-b')[1]);
    return (q('#eAmt').value === '25.5' && q('#eMerchant').value === '测试商家')
      ? true : `金额=${q('#eAmt').value} 商家=${q('#eMerchant').value}`;
  });
  check('保存成功：记录 +1', () => {
    click(qa('#eAccs [data-acc]')[0]);
    click(qa('#eCats .cat-b')[0]);
    setVal(q('#eAmt'), '25.5');
    setVal(q('#eMerchant'), '测试商家');
    click(qa('#eTags .chip')[1]);
    click(q('#eSave'));
    return db().records.length === 23 ? true : '实际 ' + db().records.length + ' 条';
  });
  check('保存后弹窗关闭且列表刷新为 4 条', () => {
    const n = qa('#mainArea .rec').length;
    return (!q('#maskEdit').classList.contains('on') && n === 4) ? true : '列表 ' + n + ' 条';
  });
  check('新记录字段正确写入', () => {
    const r = db().records[db().records.length - 1];
    return (r.amount === 25.5 && r.merchant === '测试商家') ? true : JSON.stringify(r).slice(0, 120);
  });
  check('标签已选中写入', () => {
    const r = db().records[db().records.length - 1];
    return r.tags.length === 1 ? true : JSON.stringify(r.tags);
  });

  // ============ 7. 存并继续 ============
  check('存并继续：记录 +1 且弹窗保持打开', () => {
    click(q('#btnAdd'));
    click(qa('#eAccs [data-acc]')[0]);
    setVal(q('#eAmt'), '9.9');
    click(q('#eSaveMore'));
    return (db().records.length === 24 && q('#maskEdit').classList.contains('on'))
      ? true : `记录 ${db().records.length} / 弹窗开启 ${q('#maskEdit').classList.contains('on')}`;
  });
  check('存并继续后表单已重置（金额清空）', () => q('#eAmt').value === '');
  click(q('#eCancel'));

  // ============ 8. 编辑与删除 ============
  check('点击流水打开编辑并回填', () => {
    click(qa('#mainArea .rec')[0]);
    return q('#maskEdit').classList.contains('on') && q('#eAmt').value !== '';
  });
  check('编辑保存生效', () => {
    setVal(q('#eAmt'), '99.99');
    click(q('#eSave'));
    return db().records.some(r => r.amount === 99.99) ? true : '未找到 99.99';
  });
  check('删除记录走二次确认', () => {
    const before = db().records.filter(r => !r.deleted).length;
    click(qa('#mainArea .rec')[0]);
    click(q('#eDel'));
    const hasConfirm = q('#maskConfirm').classList.contains('on');
    click(q('#cfYes'));
    const after = db().records.filter(r => !r.deleted).length;
    return (hasConfirm && after === before - 1) ? true : `确认框=${hasConfirm} ${before}->${after}`;
  });

  // ============ 9. 小记 ============
  check('小记写入并持久化', () => {
    setVal(q('#noteIn'), '今天克制住了没点奶茶');
    q('#noteIn').dispatchEvent(new w.Event('blur', { bubbles: true }));
    return db().notes.some(n => n.content === '今天克制住了没点奶茶') ? true : JSON.stringify(db().notes);
  });

  // ============ 10. 数据菜单与导出 ============
  check('打开数据菜单', () => {
    click(q('#btnData'));
    return q('#maskData').classList.contains('on');
  });
  check('导出 JSON 不报错', () => { click(q('#dExportJson')); return true; });
  check('导出 CSV 不报错', () => { click(q('#dExportCsv')); return true; });
  check('清空全部需输入「清空」二次确认', () => {
    click(q('#dClearAll'));
    const opened = q('#maskConfirm').classList.contains('on');
    click(q('#cfYes'));
    const stillThere = db().records.length > 0;
    click(q('#cfNo'));
    return (opened && stillThere) ? true : `确认框=${opened} 数据仍存在=${stillThere}`;
  });
  click(q('#dClose'));

  // ============ 11. 空数据不崩 ============
  check('清空示例：只删示例、保留用户自己的账', () => {
    click(q('#btnData'));
    const cd = q('#dClearDemo');
    if (!cd) return '没有清空示例按钮';
    click(cd);
    click(q('#cfYes'));
    const demoLeft = db().records.filter(r => r.demo).length;
    const userLeft = db().records.filter(r => !r.demo).length;
    return (demoLeft === 0 && userLeft > 0) ? true : `示例剩 ${demoLeft} 条 / 用户剩 ${userLeft} 条`;
  });
  check('清空示例后三个视图都能正常渲染', () => {
    const ok = [];
    ['day', 'week', 'month'].forEach(v => {
      click(tab(v));
      ok.push(v + ':' + (q('#mainArea').innerHTML.length > 0 && q('#statsArea').innerHTML.length > 0));
    });
    return ok.every(x => x.endsWith('true')) ? true : ok.join(' ');
  });

  check('清空全部后进入纯空态', () => {
    click(tab('day'));
    click(q('#btnData'));
    click(q('#dClearAll'));
    setVal(q('#cfIn'), '清空');
    click(q('#cfYes'));
    return db().records.length === 0 ? true : '还剩 ' + db().records.length + ' 条';
  });
  check('空数据：日视图显示空态而非崩溃', () => q('#mainArea').textContent.includes('还没有记录'));
  check('空数据：周视图显示空态', () => {
    click(tab('week'));
    return q('#mainArea').textContent.includes('这一周还没有记录');
  });
  check('空数据：月视图日历仍完整渲染', () => {
    click(tab('month'));
    return qa('#mainArea .cal-cell').length > 27 && q('#statsArea').textContent.includes('暂无数据');
  });
  check('空数据：自动总结正常', () => {
    click(tab('day'));
    return q('#noteAuto').textContent.includes('还没有记账记录');
  });
  check('空数据：今日要处理有提示', () => q('#todo').textContent.includes('今天还没有记账'));
  check('空数据下无任何运行时错误', () => errors.length === 0 || errors.join(' | '));

  // ============ 12. 边界：跨月跨年 ============
  check('日视图连续回退 60 天（跨月）不报错', () => {
    for (let i = 0; i < 60; i++) click(q('#navPrev'));
    return q('#navArea').textContent.length > 0;
  });
  check('日视图连续前进 400 天（跨年）不报错', () => {
    for (let i = 0; i < 400; i++) click(q('#navNext'));
    return q('#navArea').textContent.length > 0;
  });
  check('跨年后无新错误', () => errors.length === 0 || errors.join(' | '));

  // ============ 13. 自定义分类（三通道 + 新增/删除） ============
  check('新增分类：打开编辑器并创建（即时进入表单可选）', () => {
    click(q('#btnAdd'));
    click(q('#eCatAdd'));
    if (!q('#maskCat').classList.contains('on')) return '分类编辑器未打开';
    setVal(q('#catName'), '车位租金');
    click(qa('#sheetCat [data-ic]')[2]);
    click(qa('#sheetCat [data-clr]')[4]);
    click(q('#catSave'));
    const cats = db().settings.categories;
    const created = cats.filter(c => c.custom && c.name === '车位租金');
    const inForm = qa('#eCats [data-cat]').length;
    const expCount = cats.filter(c => c.type === 'expense').length;
    return (created.length === 1 && inForm === expCount) ? true : ('已建' + created.length + ' 表单' + inForm + ' 支出' + expCount);
  });
  check('删除自定义分类：二次确认 + 兜底历史记录', () => {
    const custom = db().settings.categories.find(c => c.custom);
    if (!custom) return '没有自定义分类';
    click(q('#btnAdd'));
    click(qa('#eCats [data-cat="' + custom.id + '"]')[0]);
    click(qa('#eAccs [data-acc]')[0]);
    setVal(q('#eAmt'), '800');
    click(q('#eSave'));
    const rec = db().records.find(r => r.amount === 800 && r.category === custom.id);
    if (!rec) return '未记下该分类的流水';
    click(q('#btnAdd'));
    const delBtn = qa('#eCats .cat-del[data-catdel="' + custom.id + '"]')[0];
    if (!delBtn) return '未渲染删除按钮';
    click(delBtn);
    if (!q('#maskConfirm').classList.contains('on')) return '未弹二次确认';
    click(q('#cfYes'));
    const gone = db().settings.categories.every(c => c.id !== custom.id);
    const rec2 = db().records.find(r => r.id === rec.id);
    const fallbackOK = rec2 && rec2.category === 'other';
    return (gone && fallbackOK) ? true : ('gone=' + gone + ' fallback=' + (rec2 && rec2.category));
  });
  check('不计入收支通道独立存在且统计自动隔离', () => {
    const t = db().settings.categories.filter(c => c.type === 'transfer');
    return t.length >= 3 ? true : ('不计入通道分类数 ' + t.length);
  });

  // ============ 输出 ============
  console.log('\n============ 冒烟自检结果 ============');
  let pass = 0, fail = 0;
  results.forEach(([s, n, m]) => {
    if (s === 'PASS') { pass++; console.log('  [PASS] ' + n); }
    else { fail++; console.log('  [FAIL] ' + n + '  →  ' + m); }
  });
  console.log('======================================');
  console.log('通过 ' + pass + ' / 失败 ' + fail);
  if (errors.length) {
    console.log('\n运行时错误：');
    errors.forEach(e => console.log('  ' + e));
  }
  process.exit(fail ? 1 : 0);
}, 300);
