"""
用户和班级信息获取模块
负责从网页获取用户和班级信息

注意：一个账号可能加入了多个班级。班级魔方的 /student 页面会把这些班级
逐个渲染成一张卡片（<div class="card course" course_id="...">），所以这里
统一按「班级列表」来解析，取第一个作为兼容字段，但完整列表要交给调用方。
"""

import re
import datetime
import requests
from bs4 import BeautifulSoup

REMEMBER_COOKIE_NAME = "remember_student_59ba36addc2b2f9401580f014c7f58ea4e30989d"

BASE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; X64; Linux; Android 9;) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Firefox/92.0  WeChat/x86_64 Weixin NetType/4G Language/zh_CN ABI/x86_64',
}

# 班级卡片里的班级码：「班级码 BBMP59」
CLASS_CODE_PATTERN = re.compile(r'班级码\s*([A-Za-z0-9]{4,10})')
COURSE_LINK_PATTERN = re.compile(r'/student/course/(\d+)')

REQUEST_TIMEOUT = 15


def get_current_time():
    """
    获取当前时间

    Returns:
        str: 格式化的当前时间字符串
    """
    now = datetime.datetime.now()
    return now.strftime("%H:%M:%S")


def _extract_remember_cookie(cookie_value):
    """从完整 cookie 字符串中取出 remember_student 字段（服务端只认它）。"""
    match = re.search(re.escape(REMEMBER_COOKIE_NAME) + r'=[^;]+', cookie_value or "")
    if not match:
        raise ValueError(f"cookie 中缺少 {REMEMBER_COOKIE_NAME} 字段")
    return match.group(0)


def _parse_user_name(html):
    """从 /student/my 页面的 gconfig 里取姓名。"""
    gconfig_match = re.search(r'var gconfig=\{([^}]+)\}', html)
    if not gconfig_match:
        return None
    uname_match = re.search(r"uname:'([^']+)'", gconfig_match.group(1))
    return uname_match.group(1) if uname_match else None


def parse_classes(html):
    """
    解析 /student 页面里的全部班级。

    Returns:
        list[dict]: [{"class_id": "127372", "class_name": "计算机学院25级...", "class_code": "BBMP59"}, ...]
                    按页面顺序返回，重复的班级 ID 只保留一次。
    """
    soup = BeautifulSoup(html, 'html.parser')
    classes = []
    seen = set()

    def add(class_id, class_name=None, class_code=None):
        class_id = str(class_id or "").strip()
        if not class_id or class_id in seen:
            return
        seen.add(class_id)
        classes.append({
            "class_id": class_id,
            "class_name": (class_name or "").strip() or None,
            "class_code": class_code,
        })

    # 主要方式：班级卡片。卡片结构示例：
    # <div class="card mb-3 course" course_id="127372">
    #   <a href="/student/course/127372"><h5 class="course_name">计算机学院...</h5>
    #     <p><span>班级码 BBMP59</span>计科251</p></a>
    # </div>
    for card in soup.find_all('div', class_='course'):
        class_id = card.get('course_id')
        if not class_id:
            link = card.find('a', href=True)
            match = COURSE_LINK_PATTERN.search(link['href']) if link else None
            class_id = match.group(1) if match else None
        if not class_id:
            continue

        name_tag = card.find(class_='course_name')
        class_name = name_tag.get_text(strip=True) if name_tag else None
        code_match = CLASS_CODE_PATTERN.search(card.get_text(" ", strip=True))
        add(class_id, class_name, code_match.group(1) if code_match else None)

    # 备选方式：页面里任意 /student/course/<id> 链接（老版页面可能没有卡片结构）
    for link in soup.find_all('a', href=True):
        match = COURSE_LINK_PATTERN.search(link['href'])
        if not match:
            continue
        text = link.get_text(" ", strip=True)
        code_match = CLASS_CODE_PATTERN.search(text)
        # 链接文本形如「计算机学院25级计科和软件 班级码 BBMP59 计科251」
        name = text.split("班级码")[0].strip() if "班级码" in text else text
        add(match.group(1), name or None, code_match.group(1) if code_match else None)

    # 最后兜底：gconfig 里如果只有一个班级信息，也把它当成唯一班级
    if not classes:
        gconfig_match = re.search(r'var gconfig=\{([^}]+)\}', html)
        if gconfig_match:
            gconfig_content = gconfig_match.group(1)
            id_match = re.search(r"id:'([^']+)'", gconfig_content)
            if id_match:
                cname_match = re.search(r"cname:'([^']+)'", gconfig_content)
                add(
                    id_match.group(1),
                    cname_match.group(1) if cname_match else None,
                )

    return classes


def get_user_and_classes(student):
    """
    获取用户信息与账号下的全部班级。

    Args:
        student (dict): 学生配置信息（只需 cookie）

    Returns:
        tuple: ({"name": ...}, [{"class_id": ..., "class_name": ..., "class_code": ...}, ...])
    """
    user_info = {}
    classes = []
    try:
        cookie = _extract_remember_cookie(student.get('cookie'))
        headers = dict(BASE_HEADERS)
        headers['Cookie'] = cookie

        # 用户个人信息
        try:
            response_my = requests.get(
                "https://bjmf.k8n.cn/student/my", headers=headers, timeout=REQUEST_TIMEOUT
            )
            if response_my.status_code == 200:
                user_info['name'] = _parse_user_name(response_my.text) or "未找到"
            else:
                print(f"获取用户信息页面失败，状态码: {response_my.status_code}")
        except requests.RequestException as exc:
            print(f"获取用户信息页面异常: {exc}")

        # 班级列表（可能不止一个班级）
        try:
            response_class = requests.get(
                "https://bjmf.k8n.cn/student", headers=headers, timeout=REQUEST_TIMEOUT
            )
            if response_class.status_code == 200:
                classes = parse_classes(response_class.text)
            else:
                print(f"获取班级信息页面失败，状态码: {response_class.status_code}")
        except requests.RequestException as exc:
            print(f"获取班级信息页面异常: {exc}")

        user_info.setdefault('name', "未找到")
        return user_info, classes

    except Exception as e:
        print(f"获取用户信息时发生错误: {e}")
        import traceback
        traceback.print_exc()
        return user_info or {"name": "未找到"}, classes


def get_user_and_class_info(student):
    """
    获取用户和班级信息（兼容旧接口：只返回第一个班级）。

    签到任务按「姓名 + 班级」区分，多班级账号需要自己遍历班级，
    推荐直接使用 get_user_and_classes()。

    Args:
        student (dict): 学生配置信息

    Returns:
        tuple: (用户信息字典, 班级信息字典)
    """
    user_info, classes = get_user_and_classes(student)

    if classes:
        first = classes[0]
        class_info = {
            'class_id': first['class_id'],
            'class_name': first['class_name'] or "未找到",
            'class_code': first['class_code'] or "未找到",
            'course_id': first['class_id'],
        }
    else:
        class_info = {
            'class_id': "未找到",
            'class_name': "未找到",
            'class_code': "未找到",
        }

    # 输出信息
    current_time = get_current_time()
    print(f"==============={current_time}===================")
    print("=========== 用户和班级信息 ===============")
    print(f"用户姓名: {user_info.get('name', '未找到')}")
    print(f"班级名称: {class_info.get('class_name', '未找到')}")
    print(f"班级代号: {class_info.get('class_id', '未找到')}")
    print(f"班级码: {class_info.get('class_code', '未找到')}")
    if len(classes) > 1:
        print(f"检测到 {len(classes)} 个班级: " + "、".join(
            f"{c['class_id']}({c['class_name'] or '未命名'})" for c in classes
        ))
    return user_info, class_info