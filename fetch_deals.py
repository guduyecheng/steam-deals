#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Steam 史低 · 新史低 榜单
==========================================================
数据来源: IsThereAnyDeal /deals/v2  (国区 / Steam)
输出:     docs/index.html  (自包含, 手机可直接打开)

判定规则:
  史低    : 当前价 <= 历史最低价(historyLow)
  新史低  : 满足史低, 且折扣开始时间(timestamp)在最近 NEW_LOW_DAYS 天内
             —— 用户反馈「史低状态可持续多天」, 故用时间区分「新出现」与「延续中」

抓取策略:
  ITA 的 deals 列表按时间大致升序(老的在前, 新的在后), 且单页上限 100,
  故先二分定位总量, 再从列表末尾往前扫描最新的一批折扣。

用法:
  python3 fetch_deals.py --key 你的ITAkey
"""

import os
import sys
import json
import time
import html
import argparse
import datetime
import pathlib

import requests
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
BASE = "https://api.isthereanydeal.com"
STEAM_SHOP_ID = 61
TIMEOUT = 25
STEAM_TIMEOUT = 12
SESSION = requests.Session()
OUT_DIR = pathlib.Path(__file__).resolve().parent / "docs"

COUNTRY = "CN"            # 榜单区域: 中国区 (注意: deals/v2 要求大写)
RATE_URL = "https://open.er-api.com/v6/latest/CNY"   # 免费汇率(1 CNY = ? 外币)
NEW_LOW_DAYS = 3          # 折扣开始 ≤ N 天 视为「新史低」
TOP_N = 15                # 榜单条数
SCAN_PAGES = 15           # 从列表末尾往前扫描的页数(每页 100 条)

NOW = datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def api_get(path, params, key, tries=3):
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(f"{BASE}{path}", params={**params, "key": key},
                             headers={"Accept": "application/json"}, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:                 # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(1.2)
    raise RuntimeError(f"GET {path} 失败: {last}")


def api_post(path, params, body, key, tries=3):
    last = None
    for i in range(tries):
        try:
            r = SESSION.post(f"{BASE}{path}", params={**params, "key": key},
                              headers={"Accept": "application/json",
                                       "Content-Type": "application/json"},
                              json=body, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:                 # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(1.2)
    raise RuntimeError(f"POST {path} 失败: {last}")


def deals_page(offset, limit, key):
    d = api_get("/deals/v2", {"country": COUNTRY, "shops": STEAM_SHOP_ID,
                              "limit": limit, "offset": offset}, key)
    return d.get("list", []), d.get("hasMore"), d.get("nextOffset")


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(s) if s else None
    except Exception:                          # noqa: BLE001
        return None


def remain_str(expiry):
    dt = parse_ts(expiry)
    if not dt:
        return ""
    sec = (dt - NOW).total_seconds()
    if sec <= 0:
        return "即将结束"
    if sec < 86400:
        return f"{int(sec // 3600)} 小时后结束"
    return f"{int(sec // 86400)} 天后结束"


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
def find_total(key):
    """二分定位折扣列表总量(列表按时间升序, 末尾最新)。"""
    lo, hi = 0, 60000
    while lo < hi:
        mid = (lo + hi) // 2
        lst, _, _ = deals_page(mid, 1, key)
        if lst:
            lo = mid + 1
        else:
            hi = mid
    return lo


def steam_ua(appid):
    """Steam 官方接口取乌克兰区价格(UAH)。返回 dict 或 None。"""
    for _ in range(2):
        try:
            r = SESSION.get("https://store.steampowered.com/api/appdetails",
                            params={"appids": appid, "cc": "ua", "l": "english",
                                    "filters": "price_overview"}, timeout=STEAM_TIMEOUT)
            r.raise_for_status()
            node = r.json().get(str(appid), {})
            po = (node.get("data") or {}).get("price_overview") if node.get("success") else None
            if not po:
                return None
            return {"final": po["final"] / 100, "currency": po.get("currency"),
                    "formatted": po.get("final_formatted"),
                    "discount": po.get("discount_percent")}
        except Exception:                      # noqa: BLE001
            time.sleep(1.0)
    return None


def get_rates():
    """汇率表 {货币: 1 CNY = ? 该货币}。"""
    try:
        r = SESSION.get(RATE_URL, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()["rates"]
    except Exception:                          # noqa: BLE001
        return {}


def steam_reviews(appid):
    """Steam 评论总数, 作为「热度」代理(热门游戏评论多)。"""
    try:
        r = SESSION.get(f"https://store.steampowered.com/appreviews/{appid}",
                        params={"json": 1, "num_per_page": 0, "language": "all",
                                "purchase_type": "all"}, timeout=STEAM_TIMEOUT)
        r.raise_for_status()
        return int((r.json().get("query_summary") or {}).get("total_reviews") or 0)
    except Exception:                          # noqa: BLE001
        return 0


def collect(key):
    rates = get_rates()
    print("① 定位折扣列表总量 ...")
    total = find_total(key)
    print(f"   共 {total} 条折扣")

    print(f"② 扫描列表末尾最近 {SCAN_PAGES} 页 ...")
    items, off = [], max(0, total - SCAN_PAGES * 100)
    while off < total:
        lst, _, nxt = deals_page(off, 100, key)
        if not lst:
            break
        items += lst
        off = nxt if nxt else off + len(lst)
    print(f"   扫描 {len(items)} 条")

    print("③ 筛选史低 ...")
    lows = []
    for it in items:
        dl = it.get("deal") or {}
        p = (dl.get("price") or {}).get("amount")
        hl = (dl.get("historyLow") or {}).get("amount")
        if p is None or hl is None or it.get("type") != "game":
            continue
        if p <= hl + 1e-9:
            ts = parse_ts(dl.get("timestamp"))
            it["_is_new"] = bool(ts and (NOW - ts).total_seconds() <= NEW_LOW_DAYS * 86400)
            lows.append(it)
    print(f"   史低 {len(lows)} 条 (其中新史低 "
          f"{sum(1 for x in lows if x['_is_new'])} 条)")

    print("④ 取 Steam appid ...")
    appids = {}
    if lows:
        try:
            data = api_post(f"/lookup/shop/{STEAM_SHOP_ID}/id/v1", {},
                            [x["id"] for x in lows], key)
            for gid, arr in (data or {}).items():
                if arr and isinstance(arr[0], str) and arr[0].startswith("app/"):
                    appids[gid] = arr[0].split("/", 1)[1]
        except Exception as e:                 # noqa: BLE001
            print(f"   [warn] appid 解析失败: {e}")

    print(f"⑤ 按热度(Steam 评论数)排序, 取前 {TOP_N} ...")
    def _rev(item):
        aid = appids.get(item["id"])
        return item["id"], (steam_reviews(aid) if aid else 0)

    heat = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for gid, n in ex.map(_rev, lows):
            heat[gid] = n
    for it in lows:
        it["_heat"] = heat.get(it["id"], 0)
    print(f"   {sum(1 for v in heat.values() if v)}/{len(heat)} 款取到评论数, "
          f"最高 {max(heat.values()) if heat else 0} 条")

    lows.sort(key=lambda x: (-x["_heat"], -(x["deal"].get("cut") or 0)))
    lows = lows[:TOP_N]

    print("⑥ 补乌克兰区价格 ...")
    def _ua(gid):
        aid = appids.get(gid)
        return gid, (steam_ua(aid) if aid else None)

    ua_map = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        for gid, ua in ex.map(_ua, [x["id"] for x in lows]):
            ua_map[gid] = ua
    print(f"   {sum(1 for v in ua_map.values() if v)}/{len(ua_map)} 款取到乌区价")

    rows = []
    for it in lows:
        dl = it["deal"]
        ua = ua_map.get(it["id"])
        ua_cny = None
        if ua and ua.get("currency"):
            _r = rates.get(ua["currency"])
            if _r:
                ua_cny = round(ua["final"] / _r, 1)
        rows.append({
            "name": it.get("title"),
            "cover": (it.get("assets") or {}).get("boxart"),
            "price": dl["price"]["amount"],
            "regular": (dl.get("regular") or {}).get("amount"),
            "cut": dl.get("cut") or 0,
            "low": dl["historyLow"]["amount"],
            "is_new": it["_is_new"],
            "remain": remain_str(dl.get("expiry")),
            "started": (dl.get("timestamp") or "")[:10],
            "url": dl.get("url"),
            "ua_uah": ua["final"] if ua else None,
            "ua_fmt": ua["formatted"] if ua else None,
            "ua_cny": ua_cny,
        })
        tag = "新史低" if it["_is_new"] else "史低"
        print(f"   [{tag}] {it.get('title')[:32]:<34} ￥{dl['price']['amount']} "
              f"(史低 ￥{dl['historyLow']['amount']}) {rows[-1]['remain']}")

    return {"updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "country": COUNTRY, "total": total, "rows": rows}


# ---------------------------------------------------------------------------
# 页面(参考小黑盒榜单样式)
# ---------------------------------------------------------------------------
def render(p):
    cards = []
    for r in p["rows"]:
        badge = ('<span class="tag new">新史低</span>' if r["is_new"]
                 else '<span class="tag low">史低</span>')
        cover = (f'<img src="{html.escape(r["cover"])}" loading="lazy" alt="">'
                 if r.get("cover") else "")
        regular = (f'<s>￥{r["regular"]:g}</s>' if r.get("regular") else "")
        if r.get("ua_uah") is not None:
            cny = f' ≈ ￥{r["ua_cny"]:g}' if r.get("ua_cny") is not None else ""
            ua = f'<div class="ua">乌克兰区 {r["ua_fmt"] or ""}{cny}</div>'
        else:
            ua = ""
        cards.append(f"""
      <a class="row" href="{html.escape(r['url'] or '#')}" target="_blank" rel="noreferrer">
        <div class="cover">{cover}<span class="off">-{r['cut']}%</span></div>
        <div class="info">
          <div class="title">{html.escape(r['name'])}</div>
          <div class="price"><b>￥{r['price']:g}</b>{regular}{badge}</div>
          <div class="sub">史低 ￥{r['low']:g} · {r['remain']}{ua}</div>
        </div>
      </a>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Steam 史低榜</title>
<style>
  :root {{ color-scheme: light dark; --red:#e5342b; --green:#00b06b; --orange:#ff7a00; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:14px 12px 40px; background:#f4f4f6; color:#1a1a1a;
         font:15px/1.45 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }}
  h1 {{ font-size:19px; margin:4px 2px 2px; }}
  .meta {{ color:#8a8a8e; font-size:12px; margin:0 2px 14px; }}
  .row {{ display:flex; gap:11px; align-items:center; background:#fff; border-radius:12px;
          padding:10px; margin-bottom:9px; text-decoration:none; color:inherit;
          box-shadow:0 1px 2px rgba(0,0,0,.05); }}
  .cover {{ position:relative; width:78px; height:104px; flex:0 0 78px; border-radius:7px;
            overflow:hidden; background:#e8e8ec; }}
  .cover img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .off {{ position:absolute; left:0; top:0; background:var(--green); color:#fff;
          font-size:11px; font-weight:700; padding:1px 5px; border-bottom-right-radius:6px; }}
  .info {{ flex:1; min-width:0; }}
  .title {{ font-weight:600; font-size:15px; margin-bottom:5px;
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .price {{ color:var(--red); font-weight:700; font-size:16px; }}
  .price s {{ color:#9a9a9e; font-weight:400; font-size:12px; margin-left:6px; }}
  .sub {{ color:#8a8a8e; font-size:12px; margin-top:3px; }}
  .sub .ua {{ margin-top:2px; }}
  .tag {{ display:inline-block; font-size:11px; font-weight:600; padding:1px 6px;
          border-radius:4px; margin-left:6px; vertical-align:1px; }}
  .tag.low {{ background:#e3f8ef; color:var(--green); }}
  .tag.new {{ background:#fff0e0; color:var(--orange); }}
  .remain {{ flex:0 0 auto; color:#9a9a9e; font-size:11px; text-align:right; max-width:64px; }}
  footer {{ margin-top:16px; color:#9a9a9e; font-size:11px; line-height:1.7; }}
  @media (prefers-color-scheme:dark) {{
    body {{ background:#000; color:#f2f2f2; }}
    .row {{ background:#1c1c1e; box-shadow:none; }}
    .cover {{ background:#2c2c2e; }}
    .tag.low {{ background:#0d3325; }}
    .tag.new {{ background:#3a2508; }}
  }}
</style></head>
<body>
  <h1>Steam 史低榜</h1>
  <div class="meta">更新于 {p['updated_at']} · 共 {len(p['rows'])} 款 · 国区价格</div>
  {''.join(cards)}
  <footer>
    史低=当前价即为历史最低价;新史低=折扣在最近 {NEW_LOW_DAYS} 天内出现。<br>
    价格/史低数据来自 IsThereAnyDeal,乌克兰区价格来自 Steam 官方接口。
  </footer>
</body></html>"""


# ---------------------------------------------------------------------------
def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)      # 实时输出, 便于观察进度
    except Exception:                                    # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="抓取 Steam 史低榜单并生成页面")
    ap.add_argument("--key", help="ITA API key (优先于环境变量 ITAD_API_KEY)")
    ap.add_argument("--render-only", action="store_true",
                    help="跳过抓取, 直接用 docs/data.json 重新渲染页面(调样式用)")
    args = ap.parse_args()

    if args.render_only:
        payload = json.loads((OUT_DIR / "data.json").read_text(encoding="utf-8"))
    else:
        key = (args.key or os.environ.get("ITAD_API_KEY", "")).strip()
        if not key:
            print("[!] 缺少 ITA API key: --key 传入或设置 ITAD_API_KEY")
            sys.exit(1)
        payload = collect(key)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    (OUT_DIR / "index.html").write_text(render(payload), encoding="utf-8")
    print(f"\n完成 -> {OUT_DIR/'index.html'}")


if __name__ == "__main__":
    main()
