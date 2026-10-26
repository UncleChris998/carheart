# -*- coding: utf-8 -*-
"""免费联网：直接抓搜索引擎结果页，不花一分钱。

为什么不用 DeepSeek 的 /v1/tools/search：那个按次收费（5 元/1000 次）。
聊天模型自己不会上网 —— 它只能说"我要搜"，真正去搜的还是程序。
所以搜索这一步可以用免费的：抓 Bing / 搜狗的 HTML 结果页。

实测（本机网络）：
  cn.bing.com   可用
  www.sogou.com 可用
  baidu.com     返回拦截短页，解析不出结果
  duckduckgo / brave  连不上

解析用 lxml，不用正则 —— 正则被嵌套标签坑过：
早期版本 `<li class="b_algo">.*?</li>` 会在第一个嵌套 </li> 处断掉，
结果把"最"字的字典页当成搜索结果返回了。
"""
import gzip
import html as htmllib
import re
import urllib.parse
import urllib.request

try:
    import lxml.html as LH
except Exception:                                         # noqa: BLE001
    LH = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TIMEOUT = 15


def _get(url, referer=None):
    hdrs = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip",
    }
    if referer:
        hdrs["Referer"] = referer
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
        ctype = (r.headers.get("Content-Type") or "").lower()
    if "gzip" in enc:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    # 先按响应头里的 charset
    m = re.search(r"charset=([\w-]+)", ctype)
    cands = []
    if m:
        cands.append(m.group(1))
    cands += ["utf-8", "gb18030", "gbk"]
    for cs in cands:
        try:
            return raw.decode(cs)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def _txt(el):
    """取一个节点的纯文本。"""
    if el is None:
        return ""
    try:
        s = el.text_content()
    except Exception:
        s = ""
    s = htmllib.unescape(s or "")
    return re.sub(r"\s+", " ", s).strip()


def _tree(page):
    if LH is None:
        return None
    try:
        return LH.fromstring(page)
    except Exception:
        return None


def _bing(q, count):
    """解析 Bing 结果。每个自然结果是一个 li.b_algo。"""
    url = "https://cn.bing.com/search?" + urllib.parse.urlencode(
        {"q": q, "setlang": "zh-CN", "count": max(10, count)})
    page = _get(url)
    doc = _tree(page)
    if doc is None:
        return []
    out = []
    for li in doc.cssselect("li.b_algo"):
        a = li.cssselect("h2 a")
        if not a:
            continue
        link = (a[0].get("href") or "").strip()
        title = _txt(a[0])
        if not title or not link.startswith("http"):
            continue
        # 摘要：优先 p，其次 .b_caption
        snippet = ""
        for sel in ("p", ".b_caption", ".b_snippet"):
            got = li.cssselect(sel)
            if got:
                snippet = _txt(got[0])
                if snippet:
                    break
        out.append({
            "title": title, "url": link,
            "summary": snippet, "snippet": snippet,
            "date": "", "authority": 0,
            "site_name": urllib.parse.urlparse(link).netloc,
        })
        if len(out) >= count:
            break
    return out


def _sogou(q, count):
    """解析搜狗结果。"""
    url = "https://www.sogou.com/web?" + urllib.parse.urlencode({"query": q})
    page = _get(url)
    doc = _tree(page)
    if doc is None:
        return []
    out = []
    for div in doc.cssselect("div.vrwrap, div.rb"):
        a = div.cssselect("h3 a")
        if not a:
            continue
        link = (a[0].get("href") or "").strip()
        title = _txt(a[0])
        if not title or not link:
            continue
        # 搜狗的链接是 /link?url=... 要补全域名
        if link.startswith("/link?"):
            link = "https://www.sogou.com" + link
        elif not link.startswith("http"):
            continue
        snippet = ""
        for sel in ("p.str_info", "div.str_info", "p.space-txt", "div.text-layout"):
            got = div.cssselect(sel)
            if got:
                snippet = _txt(got[0])
                if snippet:
                    break
        out.append({
            "title": title, "url": link,
            "summary": snippet, "snippet": snippet,
            "date": "", "authority": 0,
            "site_name": urllib.parse.urlparse(link).netloc,
        })
        if len(out) >= count:
            break
    return out


def web(query, count=6):
    """免费搜。返回结果列表，全失败返回空列表。

    搜狗优先：实测它对中文查询的解析质量明显好于 Bing。
    Bing 会「降级」—— 比如搜「最新款 iPhone 型号」，它返回的却是
    「最」字的字典页（页面标题是对的，结果全是别的东西），
    这是它的反爬策略，不能信。所以只当兜底。
    """
    q = (query or "").strip()
    if not q:
        return []
    if LH is None:
        print("  [联网] 没有 lxml，搜不了", flush=True)
        return []
    for name, fn in (("搜狗", _sogou), ("Bing", _bing)):
        try:
            res = fn(q, count)
            if res:
                print(f"  [联网] {name} 搜到 {len(res)} 条", flush=True)
                return res
            print(f"  [联网] {name} 没解析出结果", flush=True)
        except Exception as e:                            # noqa: BLE001
            print(f"  [联网] {name} 失败：{type(e).__name__}: {e}", flush=True)
    return []
