---
name: music-search
description: "搜索和播放音乐 / Search & play music. 当用户想要：(1) 随机点一首歌/随机推荐一首歌/帮我放首歌 (2) 搜索某首歌曲/点播指定歌曲/播放某个歌手的歌 (3) 获取歌曲播放链接或封面 (4) 在网易云/酷狗/酷我/汽水/QQ音乐等平台查找音乐时使用。Use when user wants to: randomly recommend or play a song, search for a song by name/artist, play specific music, get play URL or cover image from NetEase/KuGou/KuWo/QiShui/QQ Music."
metadata:
  requires:
    bins: ["python3"]
    env: []
  emoji: "🎵"
---

# Music Search Skill

搜索并在微信播放音乐的技能。

## 支持平台

| 平台 | 标识符 | 说明 |
|------|--------|------|
| 网易云音乐 | `netease` | 曲库丰富，最稳定（**首选**） |
| 酷狗音乐 | `kugou` | 流行/主流歌曲丰富 |
| 酷我音乐 | `kuwo` | 冷门歌曲备选 |
| 汽水音乐 | `qishui` | 字节跳动旗下 |
| QQ音乐 | `qq` | 腾讯曲库 |

---

## ⚠️ 核心规则（必须严格遵守）

> **你的职责只有一件事：调用脚本获取音乐数据，然后原样透传 JSON 字段。**
> 
> - ❌ **绝对禁止**：自己生成或拼接任何 XML 代码
> - ❌ **绝对禁止**：输出 `MUSIC_CARD:` 前缀的内容
> - ❌ **绝对禁止**：从记忆中"回忆" XML 结构
> - ✅ **只允许**：输出 `MUSIC_PLAY:{json}` 格式
> 
> XML 由系统底层代码负责构建，你不需要也不应该处理 XML。

---

## 执行流程（严格遵循）

### 第一步：调用脚本获取音乐数据

**随机点歌**（用户说"随机点一首/帮我放首歌/随机推荐"）：
```bash
python3 "/root/dow-ipad-859/workspace/skills/music-search/scripts/search_music.py" random netease
```

**搜索歌曲列表**（用户想先看搜索结果再选）：
```bash
python3 "/root/dow-ipad-859/workspace/skills/music-search/scripts/search_music.py" search netease "歌曲名"
```

**获取指定歌曲详情**（用户已知歌名，或从搜索列表中选择）：
```bash
python3 "/root/dow-ipad-859/workspace/skills/music-search/scripts/search_music.py" detail netease "歌曲名" 1
```

> 若网易云失败，依次尝试：`kugou` → `kuwo` → `qishui` → `qq`

---

### 第二步：输出 MUSIC_PLAY 格式（仅此格式，不得更改）

脚本会返回如下 JSON：
```json
{"ok": true, "title": "歌曲名", "singer": "歌手", "music_url": "http://...", "thumb_url": "https://...", "platform": "netease", "source": "网易云音乐"}
```

**你的任务**：将脚本返回的 JSON 原样填入下面格式并输出：

```
MUSIC_PLAY:{"title":"<title的值>","singer":"<singer的值>","music_url":"<music_url的值>","thumb_url":"<thumb_url的值>","platform":"<platform的值>","source":"<source的值>"}
```

**规则**：
- 直接从脚本输出取值填入，不得修改任何字段内容
- 不要添加任何说明文字（如"正在为你播放..."）
- 不要用 markdown 代码块包裹
- `MUSIC_PLAY:` 后直接跟 JSON，不允许换行

---

### 第三步（仅搜索列表时）：用户选择后播放

如果用户使用了 `search` 命令获得歌曲列表，展示结果后让用户选择序号，再执行：

```bash
python3 "/root/dow-ipad-859/workspace/skills/music-search/scripts/search_music.py" detail netease "歌曲名" <用户选择的序号>
```

然后同样输出 `MUSIC_PLAY:{json}` 格式。

---

## 完整示例

### 随机点歌

用户输入：`随机点一首歌`

1. 执行脚本：
```bash
python3 "/root/dow-ipad-859/workspace/skills/music-search/scripts/search_music.py" random netease
```

2. 脚本返回：
```json
{"ok": true, "title": "晴天", "singer": "周杰伦", "music_url": "http://music.163.com/song/media/outer/url?id=186001", "thumb_url": "https://p2.music.126.net/xxx.jpg", "platform": "netease", "source": "网易云音乐"}
```

3. **你只需输出**（不要有其他文字）：
```
MUSIC_PLAY:{"title":"晴天","singer":"周杰伦","music_url":"http://music.163.com/song/media/outer/url?id=186001","thumb_url":"https://p2.music.126.net/xxx.jpg","platform":"netease","source":"网易云音乐"}
```

---

### 搜索指定歌曲

用户输入：`帮我搜索周杰伦的七里香`

1. 执行搜索：
```bash
python3 "/root/dow-ipad-859/workspace/skills/music-search/scripts/search_music.py" search netease "七里香"
```

2. 展示搜索结果列表给用户，等待用户选择编号

3. 用户选择后执行详情：
```bash
python3 "/root/dow-ipad-859/workspace/skills/music-search/scripts/search_music.py" detail netease "七里香" 1
```

4. 输出 `MUSIC_PLAY:{json}` 格式

---

## 错误处理

| 情况 | 处理方式 |
|------|----------|
| 脚本返回 `{"error": ...}` | 切换平台重试（netease → kugou → kuwo） |
| 脚本返回的 JSON 缺少 `ok:true` | 视为失败，切换平台 |
| 所有平台均失败 | 告知用户"暂时无法获取音乐，请稍后再试" |

---

## 技术说明

**为什么使用 `MUSIC_PLAY:{json}` 而不是直接输出 XML？**

- LLM 不适合生成精确的 XML（标签容易拼错、遗漏或变形）
- XML 由系统底层 `_build_music_xml()` 函数用代码模板生成，确保 100% 格式正确
- LLM 只需提取 5 个简单字段，出错概率极低
- 即使从记忆中生成（未调用脚本），简单 JSON 也远比 XML 准确
