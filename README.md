# Steam 史低榜

每天自动抓取 **Steam 国区正在史低**的游戏（含「新史低」标记），并附带**乌克兰区价格对比**。

**页面地址** 👉 https://guduyecheng.github.io/steam-deals/

## 数据来源

| 数据 | 来源 |
|---|---|
| 国区当前价 / 历史最低价 | IsThereAnyDeal API |
| 乌克兰区当前价（格里夫纳） | Steam 官方商店接口 |

## 判定规则

- **史低**：当前价 = 该游戏的历史最低价
- **新史低**：满足史低，且折扣是最近 **3 天**内出现的

榜单按**热度**（Steam 评论数）排序，取前 15 款。

## 自动更新

每天 UTC 01:00（北京时间 09:00）自动运行一次。

手动更新：进入 **Actions** 标签页 → 左侧选「更新 Steam 史低榜」→ 右侧 **Run workflow**。

## 常用调整

编辑 `fetch_deals.py` 顶部的常量：

| 常量 | 作用 | 默认 |
|---|---|---|
| `TOP_N` | 榜单条数 | 15 |
| `COUNTRY` | 榜单区域 | `CN` |
| `NEW_LOW_DAYS` | 几天内的折扣算「新史低」 | 3 |
| `SCAN_PAGES` | 从折扣列表末尾扫描的页数（越大越全、越慢） | 15 |

## 本地运行

```bash
pip install requests
python fetch_deals.py --key 你的ITA_key
# 只重新渲染页面（不重新抓数据）:
python fetch_deals.py --render-only
```
