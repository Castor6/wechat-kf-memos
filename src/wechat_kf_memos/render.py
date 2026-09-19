import html
import json
import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .tags import extract_tags, strip_tags


def literal(value):
    """Render untrusted chat text without allowing injected HTML/Markdown structures."""
    text = html.escape(str(value), quote=False)
    for char in "\\`*_{}[]()#+-.!|~":
        text = text.replace(char, "\\" + char)
    return text


def raw(value):
    """Lossless fallback with a fence longer than any run in the payload."""
    text = json.dumps(value, ensure_ascii=False, indent=2)
    fence = "`" * max(3, max((len(s) for s in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence}json\n{text}\n{fence}"


def stamp(value):
    try:
        return datetime.fromtimestamp(int(value), ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OverflowError, OSError):
        return "时间未知"


def url_reference(value):
    url = str(value)
    try:
        parsed = urlsplit(url)
        safe = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
    except ValueError:
        safe = False
    if safe:
        return (
            "<" + url.replace("<", "%3C").replace(">", "%3E").replace("\n", "%0A").replace("\r", "%0D") + ">"
        )
    return literal(url)


CARDS = {
    "miniprogram": ("小程序", {"title": "标题", "appid": "AppID", "pagepath": "页面路径"}),
    "channels_shop_product": (
        "视频号商品",
        {
            "product_id": "商品 ID",
            "title": "商品",
            "sales_price": "售价（分）",
            "shop_nickname": "店铺",
            "head_image": "商品图片地址",
            "shop_head_image": "店铺头像地址",
        },
    ),
    "channels_shop_order": (
        "视频号订单",
        {
            "order_id": "订单 ID",
            "product_titles": "商品",
            "price_wording": "价格",
            "state": "状态",
            "shop_nickname": "店铺",
            "image_url": "商品图片地址",
        },
    ),
    "channels": (
        "视频号",
        {"sub_type": "类型（1 动态 / 2 直播 / 3 名片）", "nickname": "视频号", "title": "标题"},
    ),
}


def render(message: dict, media, depth=0):
    """media(media_id, kind) returns a Markdown attachment reference; never fetch arbitrary URLs."""
    kind = message.get("msgtype", "unknown")
    if not isinstance(kind, str):
        return "[消息类型异常，保留原始内容]\n\n" + raw(message)
    data = message.get(kind, {})
    if not isinstance(data, dict):
        return "[消息结构异常，保留原始内容]\n\n" + raw(message)
    if kind == "text":
        result = literal(data.get("content", ""))
    elif kind == "merged_msg":
        if depth >= 8:
            return "[嵌套聊天记录超过 8 层，保留原始内容]\n\n" + raw(message)
        parts = [f"**{literal(data.get('title', '聊天记录'))}**"]
        items = data.get("item", [])
        if not isinstance(items, list):
            return "[聊天记录结构异常，保留原始内容]\n\n" + raw(message)
        for item in items:
            if not isinstance(item, dict):
                parts.append("[消息结构异常]\n\n" + raw(item))
                continue
            parts.append(
                f"**{literal(item.get('sender_name', '未知发送者'))} · {stamp(item.get('send_time'))}**"
            )
            nested = item.get("msg_content", "")
            try:
                nested = json.loads(nested) if isinstance(nested, str) else nested
                if not isinstance(nested, dict):
                    raise TypeError()
                nested = {**nested, "msgtype": nested.get("msgtype", item.get("msgtype", "unknown"))}
            except (ValueError, TypeError):
                parts.append("[内容无法解析，以下为原始文本]\n\n" + raw(item.get("msg_content", "")))
            else:
                parts.append(render(nested, media, depth + 1))
        result = "\n\n".join(parts)
    elif kind in {"image", "voice", "video", "file"}:
        result = media(data["media_id"], kind) if data.get("media_id") else f"[{kind}：接口未返回附件 ID]"
    elif kind == "link":
        result = f"{literal(data.get('title', '链接'))}\n\n{literal(data.get('desc', ''))}\n\n{url_reference(data.get('url', ''))}"
    elif kind == "location":
        result = literal(
            f"位置：{data.get('name', '')} {data.get('address', '')} ({data.get('latitude')}, {data.get('longitude')})"
        )
    elif kind in CARDS:
        title, fields = CARDS[kind]
        parts = [f"**{title}**"]
        for key, label in fields.items():
            if key in data:
                value = data[key]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                parts.append(f"{label}：{literal(value)}")
        if data.get("thumb_media_id"):
            parts.append(media(data["thumb_media_id"], "image"))
        result = "\n\n".join(parts)
    elif kind == "note":
        result = "[微信笔记：官方接口不提供笔记正文；请改为发送文字、图片或文件]"
    else:
        return f"[{literal(kind)}：尚无专用解析器，保留接口返回内容]\n\n" + raw(message)
    return result


def note_tags(message: dict, settings=None):
    tags = list(settings.default_tags if settings else ("微信剪藏",))
    if message.get("msgtype") == "merged_msg":
        tags.append(settings.chat_record_tag if settings else "微信聊天记录")
    elif message.get("msgtype") == "text" and isinstance(message.get("text"), dict):
        tags.extend(extract_tags(str(message["text"].get("content", ""))))
    return list(dict.fromkeys(tags))


def note(message: dict, media, settings=None):
    note_tags_original = note_tags(message, settings)
    # Explicit classification belongs in the API tags field, not the note body.
    if message.get("msgtype") == "text" and isinstance(message.get("text"), dict):
        message = {
            **message,
            "text": {**message["text"], "content": strip_tags(str(message["text"].get("content", "")))},
        }
    content = render(message, media)
    if not settings or settings.memos_tag_mode == "markdown":
        tags = " ".join("#" + tag for tag in note_tags_original)
        return (content + "\n\n" + tags).strip()
    return content
