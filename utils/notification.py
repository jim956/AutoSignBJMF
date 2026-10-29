"""
消息通知模块
负责发送pushplus消息通知
"""

import requests


def send_to_pushplus(token: str, title: str, content: str, channel: str = "wechat"):
    if not token or not token.strip():
        return

    try:
        url = f"http://www.pushplus.plus/send/{token}"
        data = {"title": title, "content": content, "channel": channel}
        response = requests.post(url, data=data, timeout=30)
        if response.status_code == 200:
            print("send_to_pushplus成功")
        else:
            print(f"send_to_pushplus失败，状态码: {response.status_code}")
    except Exception as e:
        print(f"send_to_pushplus出错: {e}")
