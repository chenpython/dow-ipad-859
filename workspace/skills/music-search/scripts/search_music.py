#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Music Search Script - aligned with SearchMusic plugin APIs
Supports multiple Chinese music platforms.

API endpoints mirror SearchMusic/config.json exactly so that results are
consistent with the plugin.  The `random` command uses the dedicated
api.52vmy.cn/api/music/wy/rand endpoint (same as the plugin's
random_music.api_endpoint) instead of a fragile search-then-pick approach.

Output modes:
  - random / detail / play : JSON with a 'wechat_xml' field (appmsg XML)
  - search                 : JSON list for display to the user

Usage:
  search_music.py random  <platform>
  search_music.py search  <platform> <song_name>
  search_music.py detail  <platform> <song_name> <song_number>
  search_music.py play    <platform> <song_name> <song_number>
"""

import sys
import json
import urllib.parse
import re

# Use requests for robust HTTP (auto redirect, gzip, retries)
try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    import urllib.request as _urllib_req
    _HAS_REQUESTS = False

# ---------------------------------------------------------------------------
# Platform table (mirrors SearchMusic/config.json)
# ---------------------------------------------------------------------------
PLATFORM_CFG = {
    "netease": {
        "search": "https://api.317ak.cn/api/yljk/wyyundg/wyyundg?msg={song_name}&ckey=4XBSGOG073HNHUQ53T1Q&g=10&br=1&type=json",
        "detail": "https://api.317ak.cn/api/yljk/wyyundg/wyyundg?msg={song_name}&ckey=4XBSGOG073HNHUQ53T1Q&g=10&n={song_number}&br=1&type=json",
        "appid":  "wx8dd6ecd81906fd84",
        "source": "网易云音乐",
    },
    "kugou": {
        "search": "https://api.317ak.cn/api/yljk/jhdg?ckey=4XBSGOG073HNHUQ53T1Q&msg={song_name}&pt=kg&y=1&s=20&type=json",
        "detail": "https://api.317ak.cn/api/yljk/jhdg?ckey=4XBSGOG073HNHUQ53T1Q&msg={song_name}&pt=kg&n={song_number}&y=1&s=20&type=json",
        "appid":  "wx79f2c4418704b4f8",
        "source": "酷狗音乐",
    },
    "kuwo": {
        "search": "https://api.52vmy.cn/api/music/kw?word={song_name}&n=&type=json",
        "detail": "https://api.52vmy.cn/api/music/kw?word={song_name}&n={song_number}&type=json",
        "appid":  "wxc305711a2a7ad71c",
        "source": "酷我音乐",
    },
    "qishui": {
        "search": "https://api.dragonlongzhu.cn/api/dg_qishuimusic.php?msg={song_name}&type=json",
        "detail": "https://api.dragonlongzhu.cn/api/dg_qishuimusic.php?msg={song_name}&n={song_number}&type=json",
        "appid":  "wx904fb3ecf62c7dea",
        "source": "汽水音乐",
    },
    "qq": {
        "search": "https://api.317ak.cn/api/yljk/jhdg?ckey=4XBSGOG073HNHUQ53T1Q&msg={song_name}&pt=qq&y=1&s=20&type=json",
        "detail": "https://api.317ak.cn/api/yljk/jhdg?ckey=4XBSGOG073HNHUQ53T1Q&msg={song_name}&pt=qq&n={song_number}&y=1&s=20&type=json",
        "appid":  "wx5aa333606550dfd5",
        "source": "QQ音乐",
    },
}

# Dedicated random-song API – same as SearchMusic plugin's random_music.api_endpoint
# Returns: {"code":200,"data":{"song":"...","singer":"...","Music":"...","cover":"...","id":...}}
RANDOM_MUSIC_API = {
    "netease": "https://api.52vmy.cn/api/music/wy/rand",
    # Fallbacks for other platforms use netease random API (most reliable)
    "kugou":   "https://api.52vmy.cn/api/music/wy/rand",
    "kuwo":    "https://api.52vmy.cn/api/music/wy/rand",
    "qishui":  "https://api.52vmy.cn/api/music/wy/rand",
    "qq":      "https://api.52vmy.cn/api/music/wy/rand",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
}

# ---------------------------------------------------------------------------
# HTTP helper (requests preferred; urllib fallback)
# ---------------------------------------------------------------------------
def _http_get(url, timeout=15):
    if _HAS_REQUESTS:
        for attempt in range(3):
            try:
                resp = _requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
                resp.raise_for_status()
                return resp.text
            except Exception as e:
                if attempt == 2:
                    return None
                import time as _time; _time.sleep(2 ** attempt)
    else:
        req = _urllib_req.Request(url, headers=_HEADERS)
        try:
            with _urllib_req.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception:
            return None

# ---------------------------------------------------------------------------
# WeChat music card XML (mirrors SearchMusic.construct_music_appmsg)
# ---------------------------------------------------------------------------
def build_music_xml(title, singer, music_url, thumb_url="", platform="netease"):
    def _appid_by_url(url, plat):
        if "kuwo.cn"       in url: return PLATFORM_CFG.get("kuwo",    {}).get("appid", "")
        if "kugou.com"     in url: return PLATFORM_CFG.get("kugou",   {}).get("appid", "")
        if "music.163.com" in url: return PLATFORM_CFG.get("netease", {}).get("appid", "")
        if "qishui" in url or "douyinpic.com" in url:
                                   return PLATFORM_CFG.get("qishui",  {}).get("appid", "")
        return PLATFORM_CFG.get(plat, {}).get("appid", PLATFORM_CFG["netease"]["appid"])

    appid = _appid_by_url(music_url, platform)
    source = PLATFORM_CFG.get(platform, {}).get("source", "音乐分享")

    def _safe(u):
        if not u: return ""
        if not u.startswith(("http://", "https://")):
            u = "https://" + u.lstrip("/")
        if u.startswith("http://"):
            u = "https://" + u[7:]
        return u.replace("&", "&amp;")

    thumb_url = _safe(thumb_url)
    music_url = _safe(music_url)

    # Build appattach XML separately to avoid any f-string issues
    appattach_xml = (
        '<appattach>'
        '<totallen>0</totallen>'
        '<attachid></attachid>'
        '<emoticonmd5></emoticonmd5>'
        '<fileext></fileext>'
        f'<cdnthumburl>{thumb_url}</cdnthumburl>'
        '<cdnthumbaeskey></cdnthumbaeskey>'
        '<aeskey></aeskey>'
        '</appattach>'
    )

    return (
        f'<appmsg appid="{appid}" sdkver="0">'
        f'<title>{title}</title>'
        f'<des>{singer}</des>'
        f'<action>view</action>'
        f'<type>76</type>'
        f'<showtype>0</showtype>'
        f'<soundtype>0</soundtype>'
        f'<mediatagname>音乐</mediatagname>'
        f'<messageaction></messageaction>'
        f'<content></content>'
        f'<contentattr>0</contentattr>'
        f'<url>https://y.qq.com/m/index.html</url>'
        f'<lowurl></lowurl>'
        f'<dataurl>{music_url}</dataurl>'
        f'<lowdataurl></lowdataurl>'
        f'{appattach_xml}'
        f'<extinfo></extinfo>'
        f'<sourceusername></sourceusername>'
        f'<sourcedisplayname>{source}</sourcedisplayname>'
        f'<thumburl>{thumb_url}</thumburl>'
        f'<songalbumurl>{thumb_url}</songalbumurl>'
        f'<songlyric></songlyric>'
        f'</appmsg>'
    )

# ---------------------------------------------------------------------------
# Random music – uses dedicated API (same as plugin)
# ---------------------------------------------------------------------------
def random_music(platform):
    """
    Fetch a random song via the dedicated random API.
    Mirrors SearchMusic.handle_random_music() exactly.
    Returns detail dict with wechat_xml on success, {"error": ...} on failure.
    """
    url = RANDOM_MUSIC_API.get(platform, RANDOM_MUSIC_API["netease"])
    content = _http_get(url)
    if not content:
        return {"error": f"random API request failed (platform={platform})"}

    try:
        data = json.loads(content)
    except Exception:
        return {"error": f"random API returned non-JSON: {content[:80]}"}

    if data.get("code") != 200 or not data.get("data"):
        return {"error": f"random API bad response: code={data.get('code')}"}

    info      = data["data"]
    title     = info.get("song", "未知歌曲")
    singer    = info.get("singer", "未知歌手")
    music_url = info.get("Music", "") or info.get("url", "")
    thumb_url = info.get("cover", "")

    if not music_url:
        return {"error": "random API: no music_url in response"}

    # Always treat as netease (music.163.com URLs → wx8dd...)
    actual_platform = "netease"
    return {
        "title":      title,
        "singer":     singer,
        "music_url":  music_url,
        "thumb_url":  thumb_url,
        "platform":   actual_platform,
        "source":     PLATFORM_CFG[actual_platform]["source"],
        "wechat_xml": build_music_xml(title, singer, music_url, thumb_url, actual_platform),
    }

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search_music(platform, song_name):
    cfg = PLATFORM_CFG.get(platform)
    if not cfg:
        return {"error": f"Unknown platform: {platform}"}

    url = cfg["search"].format(song_name=urllib.parse.quote(song_name))
    content = _http_get(url)
    if not content:
        return {"error": "search request failed"}

    results = []
    try:
        data = json.loads(content)
        # 317ak returns {"code":200,"data":[...]}
        arr = data.get("data") or []
        if isinstance(arr, list):
            for item in arr[:10]:
                results.append({
                    "n":      item.get("n", item.get("num", "")),
                    "title":  (item.get("name") or item.get("title") or "").strip(),
                    "singer": (item.get("artist") or item.get("singer") or "").strip(),
                })
    except Exception:
        # text fallback
        for num, title, singer in re.findall(r"(\d+)[\.、]\s*(.+?)(?:--| - )(.+)", content)[:10]:
            results.append({"n": num, "title": title.strip(), "singer": singer.strip()})

    return {
        "platform": platform,
        "source":   cfg["source"],
        "query":    song_name,
        "results":  results,
    }

# ---------------------------------------------------------------------------
# Detail / play (returns full info + wechat_xml)
# ---------------------------------------------------------------------------
def _parse_response(content, platform):
    title = singer = music_url = thumb_url = detail_url = ""
    try:
        data = json.loads(content)
        d = data.get("data", data)
        if isinstance(d, dict):
            title      = (d.get("name")    or d.get("title")   or d.get("song",   "")).strip()
            singer     = (d.get("artist")  or d.get("singer")  or "").strip()
            music_url  = (d.get("url")     or d.get("Music")   or d.get("music",  "")).strip()
            thumb_url  = (d.get("pic")     or d.get("cover")   or d.get("picurl", "")).strip()
            detail_url = (d.get("page")    or d.get("detail")  or "").strip()
    except Exception:
        pass

    if not (title and singer and music_url):
        for line in content.splitlines():
            line = line.strip()
            if line.startswith(("歌曲名称：", "歌名：")):
                title = line.split("：", 1)[1].strip()
            elif line.startswith(("歌手名称：", "歌手：")):
                singer = line.split("：", 1)[1].strip()
            elif line.startswith("歌曲详情页："):
                detail_url = line.split("：", 1)[1].strip()
            elif line.startswith("播放链接："):
                part = line.split("：", 1)[1].strip()
                m = re.search(r'href="([^"]+)"', part)
                music_url = m.group(1) if m else part
            elif line.startswith("±img="):
                thumb_url = line.replace("±img=", "").replace("±", "").strip()
        if not thumb_url:
            m = re.search(r"±img=([^±\s]+)±", content)
            if m:
                thumb_url = m.group(1)

    if title and singer and music_url:
        cfg = PLATFORM_CFG.get(platform, {})
        return {
            "title":      title,
            "singer":     singer,
            "music_url":  music_url,
            "thumb_url":  thumb_url,
            "detail_url": detail_url,
            "platform":   platform,
            "source":     cfg.get("source", ""),
        }
    return None


def get_music_detail(platform, song_name, song_number):
    cfg = PLATFORM_CFG.get(platform)
    if not cfg:
        return {"error": f"Unknown platform: {platform}"}

    url = cfg["detail"].format(
        song_name=urllib.parse.quote(song_name),
        song_number=song_number
    )
    content = _http_get(url)
    if not content:
        return {"error": "detail request failed"}

    detail = _parse_response(content, platform)
    if not detail:
        return {"error": "Failed to parse song details"}

    detail["wechat_xml"] = build_music_xml(
        detail["title"], detail["singer"],
        detail["music_url"], detail.get("thumb_url", ""),
        platform
    )
    return detail


def play_music(platform, song_name, song_number):
    return get_music_detail(platform, song_name, song_number)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: search_music.py <command> [args...]"}, ensure_ascii=False))
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "random":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "Usage: search_music.py random <platform>"}, ensure_ascii=False))
            sys.exit(1)
        result = random_music(sys.argv[2])
        # 输出不含 wechat_xml 的精简 JSON，由下游代码构建 XML
        if isinstance(result, dict) and "wechat_xml" in result:
            print(json.dumps({
                "ok": True,
                "title":     result["title"],
                "singer":    result["singer"],
                "music_url": result["music_url"],
                "thumb_url": result.get("thumb_url", ""),
                "platform":  result.get("platform", "netease"),
                "source":    result.get("source", ""),
            }, ensure_ascii=False))
            sys.exit(0)

    elif cmd == "search":
        if len(sys.argv) < 4:
            print(json.dumps({"error": "Usage: search_music.py search <platform> <song_name>"}, ensure_ascii=False))
            sys.exit(1)
        result = search_music(sys.argv[2], sys.argv[3])

    elif cmd == "detail":
        if len(sys.argv) < 5:
            print(json.dumps({"error": "Usage: search_music.py detail <platform> <song_name> <song_number>"}, ensure_ascii=False))
            sys.exit(1)
        result = get_music_detail(sys.argv[2], sys.argv[3], sys.argv[4])
        # 输出不含 wechat_xml 的精简 JSON，由下游代码构建 XML
        if isinstance(result, dict) and "wechat_xml" in result:
            print(json.dumps({
                "ok": True,
                "title":     result["title"],
                "singer":    result["singer"],
                "music_url": result["music_url"],
                "thumb_url": result.get("thumb_url", ""),
                "platform":  result.get("platform", "netease"),
                "source":    result.get("source", ""),
            }, ensure_ascii=False))
            sys.exit(0)

    elif cmd == "play":
        if len(sys.argv) < 5:
            print(json.dumps({"error": "Usage: search_music.py play <platform> <song_name> <song_number>"}, ensure_ascii=False))
            sys.exit(1)
        result = play_music(sys.argv[2], sys.argv[3], sys.argv[4])
        # 输出不含 wechat_xml 的精简 JSON，由下游代码构建 XML
        if isinstance(result, dict) and "wechat_xml" in result:
            print(json.dumps({
                "ok": True,
                "title":     result["title"],
                "singer":    result["singer"],
                "music_url": result["music_url"],
                "thumb_url": result.get("thumb_url", ""),
                "platform":  result.get("platform", "netease"),
                "source":    result.get("source", ""),
            }, ensure_ascii=False))
            sys.exit(0)

    elif cmd == "card":
        # 直接输出 MUSIC_CARD: 格式，不经过LLM拼接，避免XML错误
        # Usage: search_music.py card random <platform>
        #        search_music.py card detail <platform> <song_name> <song_number>
        if len(sys.argv) < 3:
            print(json.dumps({"error": "Usage: search_music.py card <random|detail> [args...]"}, ensure_ascii=False))
            sys.exit(1)

        subcmd = sys.argv[2]
        if subcmd == "random":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "Usage: search_music.py card random <platform>"}, ensure_ascii=False))
                sys.exit(1)
            result = random_music(sys.argv[3])
        elif subcmd == "detail":
            if len(sys.argv) < 6:
                print(json.dumps({"error": "Usage: search_music.py card detail <platform> <song_name> <song_number>"}, ensure_ascii=False))
                sys.exit(1)
            result = get_music_detail(sys.argv[3], sys.argv[4], sys.argv[5])
        else:
            print(json.dumps({"error": f"Unknown card subcommand: {subcmd}"}, ensure_ascii=False))
            sys.exit(1)

        # 直接输出 MUSIC_CARD: 格式，不走JSON
        if isinstance(result, dict) and "wechat_xml" in result:
            print(f"MUSIC_CARD:{result['wechat_xml']}")
        elif isinstance(result, dict) and "error" in result:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(json.dumps({"error": "No wechat_xml in result"}, ensure_ascii=False))
        sys.exit(0)

    else:
        result = {"error": f"Unknown command: {cmd}"}
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
