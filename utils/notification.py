"""
消息通知模块
负责发送pushplus消息通知
"""

import logging

import requests

logger = logging.getLogger(__name__)


def send_to_pushplus(token: str, title: str, content: str, channel: str = "wechat") -> bool:
    """
    发送 pushplus 通知。

    Returns:
        bool: 是否发送成功；未配置 token 时返回 False（由调用方决定是否提示）。
    """
    if not token or not token.strip():
        logger.debug("未配置 pushplus token，跳过通知")
        return False

    # 注意：pushplus 的 token 直接拼在 URL 里，日志中只记录脱敏值
    from utils.log_config import mask_secret

    masked_token = mask_secret(token)

    def _redact(message) -> str:
        # requests 的异常信息里会带完整 URL（含 token），落盘前替换掉
        return str(message).replace(token, masked_token)

    try:
        url = f"http://www.pushplus.plus/send/{token}"
        data = {"title": title, "content": content, "channel": channel}
        response = requests.post(url, data=data, timeout=30)
        if response.status_code == 200:
            logger.info(
                "pushplus 通知发送成功 channel=%s token=%s title=%s",
                channel,
                masked_token,
                title,
            )
            return True

        logger.warning(
            "pushplus 通知发送失败 channel=%s token=%s HTTP=%s 响应=%s",
            channel,
            masked_token,
            response.status_code,
            _redact((response.text or "")[:200]),
        )
        return False
    except requests.RequestException as exc:
        # 注意不要用 logger.exception：异常消息里含带 token 的完整 URL
        logger.warning(
            "pushplus 通知请求异常 channel=%s token=%s: %s",
            channel,
            masked_token,
            _redact(exc),
        )
        return False
    except Exception as exc:
        logger.error(
            "pushplus 通知发生未知异常 channel=%s token=%s: %s",
            channel,
            masked_token,
            _redact(exc),
        )
        return False