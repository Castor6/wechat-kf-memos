"""User-facing outcomes; no remote body, URL or credential is interpolated."""

import json
import re

import httpx

from .clients import RemoteError

LABELS = {
    "text": "文字",
    "image": "图片",
    "voice": "语音",
    "video": "视频",
    "file": "文件",
    "link": "链接",
    "location": "位置",
    "miniprogram": "小程序",
    "channels": "视频号",
    "channels_shop_product": "视频号商品",
    "channels_shop_order": "视频号订单",
    "merged_msg": "聊天记录",
    "note": "微信笔记",
}


def label(msg):
    return LABELS.get(msg.get("msgtype"), "未知类型消息")


def reason(exc):
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    if isinstance(exc, httpx.ConnectError):
        return "连接失败（网络、DNS 或 TLS）"
    if isinstance(exc, RemoteError):
        code = str(exc)
        match = re.fullmatch(r"(memos|wechat|wechat_media)_http_(\d{3})", code)
        if match:
            status = match[2]
            detail = {
                "401": "认证失败",
                "403": "权限不足",
                "413": "内容超过服务大小限制",
                "429": "请求频率受限",
            }.get(status, "接口请求失败")
            return f"{detail}，HTTP {status}"
        match = re.fullmatch(r"wechat(?:_media)?_api_(\d+)", code)
        if match:
            return "微信错误码 " + match[1]
        if code == "media_too_large":
            return "附件超过桥接服务大小限制"
        if code in {"existing_note_not_private", "attachment_owner_mismatch"}:
            return "已有资源可见性或附件归属不符"
    return "内部处理错误（" + type(exc).__name__ + "）"


class ClipFailure(Exception):
    def __init__(self, stage, exc):
        self.detail = stage + "：" + reason(exc)
        super().__init__(self.detail)


def operation(stage, fn):
    try:
        return fn()
    except ClipFailure:
        raise
    except Exception as exc:  # noqa: BLE001 -- sanitize remote errors at operation boundary
        raise ClipFailure(stage, exc) from None


def limitations(msg, depth=0):
    kind = msg.get("msgtype")
    if kind == "note":
        return ["微信接口未提供微信笔记内容"]
    if kind not in LABELS:
        return ["消息类型尚无专用解析器"]
    if kind != "merged_msg":
        data = msg.get(kind, {})
        if not isinstance(data, dict):
            return ["消息结构异常"]
        if kind in {"image", "voice", "video", "file"} and not data.get("media_id"):
            return ["微信接口未提供附件 ID"]
        return []
    if depth >= 8:
        return ["聊天记录嵌套超过 8 层，深层内容仅保留原始数据"]
    data = msg.get(kind, {})
    if not isinstance(data, dict) or not isinstance(data.get("item", []), list):
        return ["聊天记录结构异常"]
    issues = []
    for item in data.get("item", []):
        try:
            nested = item["msg_content"]
            nested = json.loads(nested) if isinstance(nested, str) else nested
            nested = {**nested, "msgtype": nested.get("msgtype", item.get("msgtype"))}
            issues.extend(limitations(nested, depth + 1))
        except (KeyError, TypeError, ValueError, AttributeError):
            issues.append("聊天记录条目无法解析")
    return list(dict.fromkeys(issues))
