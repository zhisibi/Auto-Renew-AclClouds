#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from datetime import datetime
from seleniumbase import SB
from selenium.common.exceptions import ElementClickInterceptedException, WebDriverException
from selenium.webdriver.common.by import By
from zoneinfo import ZoneInfo

# ---------- 配置（从环境变量读取） ----------
EMAIL = os.getenv('EMAIL') or ""
PASSWORD = os.getenv('PASSWORD') or ""
COOKIE_VALUE = os.getenv('COOKIE_VALUE') or ""
TG_CHAT_ID = os.getenv('TG_CHAT_ID') or ""
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN') or ""

LOGIN_PATH = '/auth/login'
BASE_URL = 'https://dash.aclclouds.com'
PROJECTS_URL = f'{BASE_URL}/projects'

def beijing_time_str():
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')

def send_telegram(message):
    if TG_BOT_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {'chat_id': TG_CHAT_ID, 'text': message}
        try:
            requests.post(url, data=data, timeout=10)
            print(f"Telegram sent: {message[:50]}...")
        except Exception as e:
            print(f"Failed to send Telegram: {e}")
    else:
        print(f"[Telegram disabled] {message}")

def wait_for_url_change(sb, original_url, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        current_url = sb.get_current_url()
        if current_url != original_url:
            return True
        sb.sleep(0.5)
    raise Exception(f"等待 URL 变化超时 ({timeout}秒)，当前仍为: {original_url}")

def extract_remember_cookie_value(raw_cookie):
    """支持纯 value、name=value、或从浏览器复制的完整 Cookie 字符串。"""
    if not raw_cookie:
        return ''

    raw_cookie = raw_cookie.strip().strip('"').strip("'")
    if not raw_cookie:
        return ''

    parts = [part.strip() for part in raw_cookie.split(';') if part.strip()]
    for part in parts:
        if part.startswith(f'remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d='):
            return part.split('=', 1)[1].strip()

    if len(parts) == 1 and '=' in parts[0]:
        return parts[0].split('=', 1)[1].strip()

    return raw_cookie

def is_login_page(sb):
    return LOGIN_PATH in sb.get_current_url()

def is_logged_in(sb):
    current_url = sb.get_current_url()
    return BASE_URL in current_url and LOGIN_PATH not in current_url

def scroll_to_selector(sb, selector):
    sb.scroll_to(selector)
    sb.sleep(0.2)

def safe_click_element(sb, element, label):
    sb.driver.execute_script(
        'arguments[0].scrollIntoView({block: "center", inline: "center"});',
        element,
    )
    sb.sleep(0.5)

    try:
        element.click()
        return True
    except (ElementClickInterceptedException, WebDriverException) as e:
        print(f"{label} 普通点击失败，改用 JavaScript 点击: {e}")

    sb.driver.execute_script('arguments[0].click();', element)
    sb.sleep(0.5)
    return True

def element_text(element):
    try:
        return element.text.strip()
    except Exception:
        return ''

def unique_elements(elements):
    unique = []
    seen = set()
    for element in elements:
        element_id = getattr(element, 'id', None)
        if element_id and element_id in seen:
            continue
        if element_id:
            seen.add(element_id)
        unique.append(element)
    return unique

def element_contains(parent, child):
    if parent == child:
        return True
    try:
        return parent.find_elements(By.XPATH, './/*').count(child) > 0
    except Exception:
        return False

def dedupe_project_cards(cards):
    cards = unique_elements(cards)
    if not cards:
        return []

    keep = []
    for card in cards:
        card_text = element_text(card)
        if len(card_text) < 3:
            continue

        duplicate = False
        for kept in list(keep):
            kept_text = element_text(kept)
            if element_contains(kept, card):
                duplicate = True
                break
            if element_contains(card, kept):
                if len(card_text) > len(kept_text):
                    keep.remove(kept)
                else:
                    duplicate = True
                break

        if not duplicate:
            keep.append(card)

    deduped = []
    seen_signatures = set()
    for card in keep:
        text = element_text(card)
        name = ''
        for line in text.splitlines():
            line = line.strip()
            if line and not re.search(r'expires|renewal|renew|reactivate|suspended|expiry|expire|valid|续期|重新激活|恢复|暂停|过期|到期', line, re.I):
                name = line
                break
        signature = (name.lower(), get_project_expiry(card).lower())
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped.append(card)

    return deduped

def find_elements(root, selector):
    by = By.XPATH if selector.startswith(('/', './/')) else By.CSS_SELECTOR
    return root.find_elements(by, selector)

def find_renew_buttons(root):
    selectors = [
        '.projects-renew-btn',
        './/button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "renew")]',
        './/button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "reactivate")]',
        './/*[(@role="button" or self::a) and contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "renew")]',
        './/*[(@role="button" or self::a) and contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "reactivate")]',
    ]
    buttons = []
    for selector in selectors:
        try:
            buttons.extend(find_elements(root, selector))
        except Exception:
            continue
    return unique_elements([button for button in buttons if element_text(button) or button.is_displayed()])

def find_card_container_from_child(sb, child):
    return sb.driver.execute_script(
        '''
        const start = arguments[0];
        let node = start;
        for (let i = 0; node && i < 10; i += 1, node = node.parentElement) {
          const text = (node.innerText || '').trim();
          const cls = (node.className || '').toString().toLowerCase();
          const looksLikeProject = /renew|reactivate|suspended|expiry|expire|expires|valid|续期|重新激活|恢复|暂停|过期|到期/i.test(text);
          const looksLikeCard = /card|project|service|server|item|row/.test(cls);
          if (node !== start && text.length > 20 && (looksLikeProject || looksLikeCard)) {
            return node;
          }
        }
        return start.parentElement || start;
        ''',
        child,
    )

def find_project_cards(sb):
    candidate_selectors = [
        '.projects-card',
        '[class*="projects-card"]',
        '[class*="project"][class*="card"]',
        '[class*="Project"][class*="Card"]',
        '[class*="service"][class*="card"]',
        '[class*="server"][class*="card"]',
        'article',
    ]
    cards = []
    for selector in candidate_selectors:
        try:
            for card in sb.driver.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(card).lower()
                if any(keyword in text for keyword in ['renew', 'reactivate', 'suspended', 'expiry', 'expire', 'valid', '续期', '重新激活', '恢复', '暂停', '过期', '到期']):
                    cards.append(card)
        except Exception:
            continue

    if cards:
        return dedupe_project_cards(cards)

    for button in find_renew_buttons(sb.driver):
        try:
            cards.append(find_card_container_from_child(sb, button))
        except Exception:
            continue

    if cards:
        return dedupe_project_cards(cards)

    expiry_xpath = (
        '//*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expiry") '
        'or contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expire") '
        'or contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "valid") '
        'or contains(normalize-space(.), "过期") or contains(normalize-space(.), "到期")]'
    )
    for elem in sb.driver.find_elements(By.XPATH, expiry_xpath):
        try:
            cards.append(find_card_container_from_child(sb, elem))
        except Exception:
            continue

    return dedupe_project_cards(cards)

def extract_date_like(text):
    if not text:
        return ''
    patterns = [
        r'\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
        r'\d{1,2}[-/]\d{1,2}[-/]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ''

def extract_duration_like(text):
    if not text:
        return ''

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if re.search(r'expires\s+in|剩余|还有', line, re.I) and idx + 1 < len(lines):
            return f"{line} {lines[idx + 1]}"

    match = re.search(
        r'(?:expires\s+in\s*)?\d+\s*(?:d|day|days|j|天|日)\s*\d*\s*(?:h|hour|hours|小时)?',
        text,
        re.I,
    )
    if match:
        return match.group(0).strip()

    match = re.search(r'\d+\s*(?:h|hour|hours|小时)', text, re.I)
    if match:
        return match.group(0).strip()

    return ''

def get_project_name(card, idx):
    selectors = [
        '.projects-card-title',
        'h1',
        'h2',
        'h3',
        'h4',
        '[class*="title"]',
        '[class*="name"]',
    ]
    for selector in selectors:
        try:
            for elem in card.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(elem)
                if text and len(text) <= 80 and 'renew' not in text.lower() and 'expiry' not in text.lower() and not extract_duration_like(text):
                    return text
        except Exception:
            continue

    for line in element_text(card).splitlines():
        line = line.strip()
        if line and len(line) <= 80 and not extract_duration_like(line) and not re.search(r'renew|reactivate|suspended|expiry|expire|valid|续期|重新激活|恢复|暂停|过期|到期', line, re.I):
            return line
    return f"项目 #{idx}"

def get_project_expiry(card):
    selectors = [
        '.projects-expiry-value',
        '[class*="expiry"]',
        '[class*="expire"]',
        '[class*="Expires"]',
        './/*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expiry")]',
        './/*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expire")]',
        './/*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "valid")]',
        './/*[contains(normalize-space(.), "过期") or contains(normalize-space(.), "到期")]',
    ]
    for selector in selectors:
        try:
            for elem in find_elements(card, selector):
                text = element_text(elem)
                date_text = extract_date_like(text)
                if date_text:
                    return date_text
                duration_text = extract_duration_like(text)
                if duration_text:
                    return duration_text
                if text and len(text) <= 120:
                    return text
        except Exception:
            continue

    card_text = element_text(card)
    return extract_date_like(card_text) or extract_duration_like(card_text) or '未知'

def get_renewal_available_note(card):
    text = element_text(card)
    patterns = [
        r'Renewal\s+will\s+be\s+available[^\n]*',
        r'可续期[^\n]*',
        r'续期[^\n]*前[^\n]*',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(0).strip()
    return ''

def get_card_by_index(sb, idx):
    cards = find_project_cards(sb)
    if idx <= len(cards):
        return cards[idx - 1]
    return None

def wait_for_renew_result(sb, idx, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            success_modals = sb.driver.find_elements(
                By.XPATH,
                '//div[contains(@class, "modal") and contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "successfully")]',
            )
            if any(modal.is_displayed() for modal in success_modals):
                card = get_card_by_index(sb, idx)
                return True, get_project_expiry(card) if card else '未知', 'success modal'

            card = get_card_by_index(sb, idx)
            if card:
                renewal_note = get_renewal_available_note(card)
                renew_buttons = find_renew_buttons(card)
                if renewal_note and not renew_buttons:
                    return True, get_project_expiry(card), renewal_note
        except Exception as e:
            print(f"检查续期结果时暂时失败: {e}")

        sb.sleep(1)

    card = get_card_by_index(sb, idx)
    note = get_renewal_available_note(card) if card else ''
    expiry = get_project_expiry(card) if card else '未知'
    return False, expiry, note

def get_renew_note(card):
    selectors = [
        '.projects-renew-note',
        '[class*="renew-note"]',
        '[class*="note"]',
        '[class*="tip"]',
    ]
    for selector in selectors:
        try:
            for elem in card.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(elem)
                if text:
                    return text
        except Exception:
            continue
    return '未到续期时间'

def get_action_button_label(button):
    text = element_text(button)
    lowered = text.lower()
    if 'reactivate' in lowered or '重新激活' in text or '恢复' in text:
        return 'Reactivate'
    return 'Renew'

def log_projects_page_diagnostics(sb):
    current_url = sb.get_current_url()
    title = sb.get_title()
    body_text = ''
    try:
        body_text = sb.driver.find_element(By.TAG_NAME, 'body').text.strip()
    except Exception:
        pass
    print(f"项目页诊断 URL: {current_url}")
    print(f"项目页诊断标题: {title}")
    print(f"项目页可见文本摘要: {body_text[:1200]}")

def has_renew_antibot_modal(sb):
    selectors = [
        '//div[contains(., "Anti-bot confirmation")]',
        '//div[contains(., "Confirm you are human")]',
        '//div[contains(., "I am not a robot")]',
    ]
    for selector in selectors:
        try:
            if any(elem.is_displayed() for elem in sb.driver.find_elements(By.XPATH, selector)):
                return True
        except Exception:
            continue
    return False

def click_captcha_checkbox(sb, label='验证码', timeout=10):
    """点击 ACLClouds 页面上的人机验证复选框。"""
    selectors = [
        'div.auth-captcha-inner[role="checkbox"]',
        '//div[contains(., "Anti-bot confirmation")]//*[@role="checkbox"]',
        '//div[contains(., "I am not a robot")]//*[@role="checkbox"]',
        '//div[contains(@class, "modal") and contains(., "Secured by ACLClouds")]//*[@role="checkbox"]',
    ]

    last_error = None
    for selector in selectors:
        try:
            sb.wait_for_element_visible(selector, timeout=timeout)
            scroll_to_selector(sb, selector)
            sb.uc_click(selector)
            sb.sleep(1)

            checked = sb.get_attribute(selector, 'aria-checked')
            if checked is None and label.startswith('续期') and not has_renew_antibot_modal(sb):
                print(f"{label}点击后窗口已关闭，继续检查续期结果")
                return True
            print(f"{label}点击完成，勾选状态: {checked}")
            if checked != 'true':
                print(f"{label}未确认勾选，尝试再次点击")
                try:
                    sb.click(selector)
                except Exception as e:
                    print(f"{label}二次点击被拦截，尝试 JavaScript 点击: {e}")
                    if selector.startswith('//'):
                        checkbox = sb.driver.find_element(By.XPATH, selector)
                    else:
                        checkbox = sb.driver.find_element(By.CSS_SELECTOR, selector)
                    safe_click_element(sb, checkbox, label)
                sb.sleep(1)
            return True
        except Exception as e:
            last_error = e

    print(f"{label}操作异常: {last_error}")
    return False

def handle_renew_antibot(sb, project_name):
    """Renew 后如果弹出 Anti-bot confirmation，则点击确认。"""
    modal_selectors = [
        '//div[contains(., "Anti-bot confirmation")]',
        '//div[contains(., "Confirm you are human")]',
        '//div[contains(., "I am not a robot")]',
    ]

    for selector in modal_selectors:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            print(f"[{project_name}] 检测到续期人机验证窗口")
            return click_captcha_checkbox(sb, '续期人机验证', timeout=5)
        except Exception:
            continue

    print(f"[{project_name}] 未检测到续期人机验证窗口，继续等待续期结果")
    return False

def js_set_input_value(sb, selector, value):
    sb.execute_script(
        '''
        const el = document.querySelector(arguments[0]);
        if (!el) return false;
        el.focus();
        el.value = arguments[1];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
        return true;
        ''',
        selector,
        value,
    )

def fill_input(sb, selector, value, label, timeout=15):
    sb.wait_for_element_visible(selector, timeout=timeout)
    scroll_to_selector(sb, selector)
    sb.click(selector)
    sb.clear(selector)
    sb.type(selector, value)

    entered_value = sb.get_value(selector)
    if label == '密码':
        print(f"{label}输入框当前值长度: {len(entered_value)}")
    else:
        print(f"{label}输入框当前值: '{entered_value}'")

    if entered_value != value:
        print(f"{label}输入未生效，使用 JavaScript 强制赋值并触发事件")
        js_set_input_value(sb, selector, value)
        entered_value = sb.get_value(selector)
        if label == '密码':
            print(f"JS 赋值后{label}长度: {len(entered_value)}")
        else:
            print(f"JS 赋值后{label}值: '{entered_value}'")

    return entered_value == value

def try_remember_cookie_login(sb):
    cookie_value = extract_remember_cookie_value(COOKIE_VALUE)
    if not cookie_value:
        print("未配置 Remember Cookie，跳过 Cookie 登录。")
        return False

    print(f"📋 尝试 Cookie 登录，Cookie value 长度: {len(cookie_value)}")
    sb.open(BASE_URL)
    sb.wait_for_ready_state_complete()

    cookie = {
        'name': 'remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d',
        'value': cookie_value,
        'domain': '.aclclouds.com',
        'path': '/',
        'secure': True,
        'httpOnly': True,
    }
    try:
        sb.add_cookie(cookie)
    except Exception as e:
        print(f"带 domain 写入 Cookie 失败，尝试当前域写入: {e}")
        cookie.pop('domain', None)
        sb.add_cookie(cookie)

    sb.open(BASE_URL)
    sb.wait_for_ready_state_complete()
    time.sleep(3)

    if is_logged_in(sb):
        print(f"✅ 已通过 Cookie 登录。当前 URL: {sb.get_current_url()}，标题: {sb.get_title()}")
        return True

    print(f"📋 Cookie 登录未成功，当前 URL: {sb.get_current_url()}，标题: {sb.get_title()}")
    return False

def login(sb, email, password):
    """执行登录，返回是否成功"""
    print("开始登录流程...")

    # ---- 填写邮箱 ----
    if not fill_input(sb, '#username', email, '邮箱'):
        print("⚠️ 邮箱仍未能正确填入，可能页面有动态行为。")

    # ---- 填写密码 ----
    if not fill_input(sb, '#password', password, '密码'):
        print("⚠️ 密码仍未能正确填入。")

    # ---- 验证码 ----
    click_captcha_checkbox(sb, '登录验证码')

    sb.sleep(1)

    # ---- 点击登录按钮 ----
    login_page_url = sb.get_current_url()
    clicked = False

    # 优先尝试提交按钮
    for selector in ['button[type="submit"]', 'div.auth-submit-btn',
                     '//button[contains(text(), "Sign in")]',
                     '//div[contains(text(), "Sign in")]']:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            scroll_to_selector(sb, selector)
            sb.click(selector)
            clicked = True
            print(f"点击 Sign in 使用: {selector}")
            break
        except Exception as e:
            print(f"选择器 {selector} 失败: {e}")
    if not clicked:
        print("所有选择器失败，使用 JS 点击")
        sb.execute_script('''
            var els = document.querySelectorAll('div, button, a');
            for (var el of els) {
                if (el.textContent.trim() === 'Sign in') {
                    el.click();
                    return true;
                }
            }
            return false;
        ''')

    # ---- 等待登录结果 ----
    try:
        wait_for_url_change(sb, login_page_url, timeout=30)
        if '/auth/login' not in sb.get_current_url():
            sb.assert_title('Home | ACLClouds')
            print("✅ 登录成功！")
            return True
        else:
            # 提取错误信息
            error_msg = ""
            try:
                errors = sb.driver.find_elements(By.CSS_SELECTOR, '.auth-error-text, .alert-danger, .error-message')
                error_msg = errors[0].text.strip() if errors else ''
            except:
                pass
            print(f"❌ 登录失败，错误: {error_msg}")
            return False
    except Exception as e:
        print(f"登录过程异常: {e}")
        return False
    
# 获取当前出口ip
def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()

def main():

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.getenv('S5_PROXY') or os.getenv('PROXY_SERVER') or "socks://127.0.0.1:1080"

    sb_options = {'uc': True, 'headless': False}
    if IS_PROXY:
        sb_options['proxy'] = PROXY_SERVER
        print(f"🔗 挂载代理: {PROXY_SERVER}")
    else:
        print("🍭 未使用代理，直连访问")

    with SB(**sb_options) as sb:   # 本地调试 headless=False，CI 改为 True
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"获取出口IP失败: {e}")

        sb.set_window_size(1366, 768)

        # 1. 尝试 Cookie
        cookie_login_ok = try_remember_cookie_login(sb)

        if not cookie_login_ok:
            if not is_login_page(sb):
                sb.open(BASE_URL)
                sb.wait_for_ready_state_complete()
                time.sleep(2)

            if is_login_page(sb):
                print("Cookie 登录失败，执行正常登录...")
                if not EMAIL or not PASSWORD:
                    print("❌ 未配置 ACL_EMAIL 或 ACL_PASSWORD，无法执行账号密码登录。")
                    send_telegram("⚠️ Cookie 登录失败，且未配置 ACL_EMAIL 或 ACL_PASSWORD。")
                    return
                if not login(sb, EMAIL, PASSWORD):
                    return
            elif is_logged_in(sb):
                print(f"✅ 当前已登录。URL: {sb.get_current_url()}，标题: {sb.get_title()}")
            else:
                print(f"❌ 未能确认登录状态。URL: {sb.get_current_url()}，标题: {sb.get_title()}")
                send_telegram("⚠️ 未能确认登录状态，请检查 Cookie 或账号密码配置。")
                return

        # 2. 进入项目页
        sb.open(PROJECTS_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(3)

        # 3. 定位卡片
        cards = find_project_cards(sb)

        if not cards:
            print("❌ 未找到项目卡片。")
            log_projects_page_diagnostics(sb)
            send_telegram("⚠️ 未找到项目卡片，请检查页面结构。")
            return

        print(f"找到 {len(cards)} 个项目卡片。")
        for idx, card in enumerate(cards, 1):
            try:
                project_name = get_project_name(card, idx)
                old_expiry = get_project_expiry(card)
                print(f"[{project_name}] 当前过期: {old_expiry}")

                renew_btn = find_renew_buttons(card)

                if renew_btn:
                    action_label = get_action_button_label(renew_btn[0])
                    safe_click_element(sb, renew_btn[0], f"[{project_name}] {action_label}按钮")
                    print(f"[{project_name}] 点击 {action_label}...")
                    handle_renew_antibot(sb, project_name)
                    success, new_expiry, result_note = wait_for_renew_result(sb, idx, timeout=30)
                    if success:
                        print(f"续期成功！状态: {result_note}，新过期: {new_expiry}")
                        send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n✅ 续期成功\n账户: {EMAIL}\n名称: {project_name}\n旧过期: {old_expiry}\n新过期: {new_expiry}\n运行时间: {beijing_time_str()}")
                    else:
                        send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n❌ 续期状态未确认: {project_name}\n账户: {EMAIL}\n旧过期: {old_expiry}\n当前过期: {new_expiry}\n页面提示: {result_note or '未发现成功提示'}")
                else:
                    note = get_renew_note(card)
                    print(f"无 Renew 按钮，提示: {note}")
                    send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n⏳ 未到续期时间\n账户: {EMAIL}\n名称: {project_name}\n过期: {old_expiry}\n提示: {note}\n运行时间: {beijing_time_str()}")
            except Exception as e:
                print(f"处理卡片 {idx} 出错: {e}")
                send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n⚠️ 处理出错: {str(e)}")

        print("所有项目处理完成。")

if __name__ == '__main__':
    main()
