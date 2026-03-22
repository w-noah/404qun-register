"""
使用 Camoufox 完成 Exa 注册
思路：通过邮箱验证码登录，跳过 onboarding，并提取默认 API Key
"""
import json
import os
import re
import threading
import time
import random

import requests as std_requests
from camoufox.sync_api import Camoufox

from config import API_KEY_TIMEOUT, EMAIL_CODE_TIMEOUT, REGISTER_HEADLESS
from mail_provider import get_email_code
import traceback
import asyncio

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "exa_apikeys.txt")
_SAVE_LOCK = threading.Lock()
_ACCOUNT_PASSWORD_LABEL = "EMAIL_OTP_ONLY"
_EXA_AUTH_URL = "https://auth.exa.ai/?callbackUrl=https%3A%2F%2Fdashboard.exa.ai%2F"
_EXA_HOME_URL = "https://dashboard.exa.ai/home"


def fill_first_input(page, selectors, value):
    """填充第一个存在的输入框"""
    for selector in selectors:
        if page.query_selector(selector):
            page.fill(selector, value)
            return selector
    return None


def click_first(page, selectors):
    """点击第一个存在的按钮/链接"""
    for selector in selectors:
        if page.query_selector(selector):
            page.click(selector, no_wait_after=True)
            return True
    return False


def extract_api_key(page):
    """从页面文本或 HTML 中提取 Exa API Key。"""
    patterns = []

    try:
        patterns.extend(re.findall(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", page.locator("main").inner_text(), re.I))
    except Exception:
        pass

    try:
        patterns.extend(re.findall(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", page.content(), re.I))
    except Exception:
        pass

    for candidate in patterns:
        return candidate
    return None


def fetch_api_key_via_dashboard_api(page):
    """直接调用已登录 dashboard 的 get-api-keys 接口，优先拿完整 key。"""
    try:
        payload = page.evaluate(
            """
            async () => {
                const response = await fetch('/api/get-api-keys', {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                        'accept': 'application/json',
                    },
                });

                return {
                    status: response.status,
                    body: await response.text(),
                };
            }
            """
        )
    except Exception:
        return None

    if int(payload.get("status") or 0) != 200:
        return None

    try:
        data = json.loads(payload.get("body") or "{}")
    except Exception:
        return None

    for item in data.get("apiKeys", []):
        candidate = (item.get("id") or "").strip()
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", candidate, re.I):
            return candidate
    return None


def ensure_dashboard_ready(page):
    """跳过 onboarding，落到可读 API Key 的 dashboard 页面。"""
    if "dashboard.exa.ai" not in page.url.lower():
        page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")

    if "/onboarding" in page.url.lower():
        click_first(page, ['button:text-is("Skip")'])
        time.sleep(1)
        click_first(
            page,
            [
                'button:text-is("Yes, I don\\\'t want the $10 in credits anyway!")',
                'button:text-is("Yes")',
            ],
        )
        page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)

    if "/home" not in page.url.lower():
        page.goto(_EXA_HOME_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)


def wait_for_api_key(page, timeout=20):
    """等待主页 API Key 卡片渲染并显示完整 key。"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        ensure_dashboard_ready(page)
        api_key = fetch_api_key_via_dashboard_api(page)
        if api_key:
            return api_key

        click_first(page, ['button:text-is("Show")'])
        time.sleep(1)
        api_key = extract_api_key(page)
        if api_key:
            return api_key
        time.sleep(1)
    return None


def save_account(api_key):
    """并发注册时串行写入 exa_apikeys.txt，一行一个 key"""
    with _SAVE_LOCK:
        with open(_SAVE_FILE, "a", encoding="utf-8") as file_obj:
            file_obj.write(f"{api_key}\n")


def verify_api_key(api_key, timeout=30):
    """真实调用 Exa API，验证新 key 可用"""
    try:
        response = std_requests.post(
            "https://api.exa.ai/search",
            json={
                "query": "api key verification",
                "numResults": 1,
            },
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
    except Exception as exc:
        print(f"❌ API Key 调用测试失败: {exc}")
        return False

    if response.status_code == 200:
        print("✅ API Key 调用测试通过")
        return True

    preview = response.text.strip().replace("\n", " ")[:160]
    print(f"❌ API Key 调用测试失败: HTTP {response.status_code}")
    if preview:
        print(f"   响应: {preview}")
    return False


def _apply_stealth(page):
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    headers = {
        "User-Agent": ua,
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Chromium";v="145", "Google Chrome";v="145", "Not A(Brand";v="99"',
        "sec-ch-ua-platform": '"macOS"',
        "sec-ch-ua-mobile": "?0",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        page.set_extra_http_headers(headers)
    except Exception:
        pass
    try:
        page.set_viewport_size({"width": 1366, "height": 900})
    except Exception:
        pass
    try:
        page.add_init_script(
            """
            (() => {
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
                const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
                if (originalQuery) {
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications'
                            ? Promise.resolve({ state: 'denied' })
                            : originalQuery(parameters)
                    );
                }
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter){
                    if(parameter === 37445) return 'Google Inc. (ATI Technologies Inc.)';
                    if(parameter === 37446) return 'ANGLE (AMD, AMD Radeon Pro 560X OpenGL Engine, OpenGL 4.1)';
                    return getParameter.apply(this, arguments);
                };
                Object.defineProperty(window, 'devicePixelRatio', { get: () => 2 });
                const origDate = Date.prototype.getTimezoneOffset;
                Date.prototype.getTimezoneOffset = function(){ return -480; };
                Intl.DateTimeFormat = (function(orig){
                    return function(locale, options){
                        const fmt = new orig(locale, options);
                        return {
                            resolvedOptions: () => ({ timeZone: 'Asia/Shanghai', locale: 'en-US' })
                        };
                    }
                })(Intl.DateTimeFormat);
                // navigator.userAgentData shim (minimal)
                try {
                    const brands = [
                        { brand: 'Chromium', version: '145' },
                        { brand: 'Google Chrome', version: '145' },
                        { brand: 'Not A(Brand', version: '99' }
                    ];
                    navigator.userAgentData = {
                        brands,
                        mobile: false,
                        platform: 'macOS',
                        getHighEntropyValues: async (hints) => {
                            const base = { brands, mobile: false, platform: 'macOS' };
                            return Object.assign(base, {
                                architecture: 'x86',
                                bitness: '64',
                                model: '',
                                platformVersion: '15.7',
                                uaFullVersion: '145.0.0.0',
                                wow64: false,
                            });
                        }
                    };
                } catch (e) {}
            })();
            """
        )
    except Exception:
        pass


def _launch_camoufox():
    """避免在已有 asyncio loop 中直接调用 sync API"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 在已有事件循环中，另起线程调用同步 Camoufox
        result = {}

        def _run():
            result['browser'] = Camoufox(headless=REGISTER_HEADLESS)
            result['ctx'] = result['browser'].__enter__()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join()
        class _Ctx:
            def __init__(self, browser, ctx):
                self.browser = browser
                self.ctx = ctx
            def __enter__(self):
                return self.ctx
            def __exit__(self, exc_type, exc, tb):
                return self.browser.__exit__(exc_type, exc, tb)
        return _Ctx(result.get('browser'), result.get('ctx'))

    # 无事件循环，直接用同步 API
    return Camoufox(headless=REGISTER_HEADLESS)


def register_with_browser(email, password):
    """使用浏览器完成 Exa 邮箱验证码注册"""
    print(f"🌐 使用浏览器模式注册 Exa: {email}")

    try:
        print(f"[debug] headless={REGISTER_HEADLESS}")
        with _launch_camoufox() as browser:
            print("[debug] browser launched")
            page = browser.new_page(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
            print("[debug] new page created")
            _apply_stealth(page)
            try:
                page.set_extra_http_headers({"Referer": "https://exa.ai/", "Origin": "https://exa.ai"})
            except Exception:
                pass

            # 预热：先访问官网和首页，产生 cookies/localStorage
            try:
                page.goto("https://exa.ai/", wait_until="domcontentloaded", timeout=20000)
                time.sleep(2)
                page.goto("https://exa.ai/home", wait_until="domcontentloaded", timeout=20000)
                time.sleep(2)
            except Exception:
                pass

            page.goto(_EXA_AUTH_URL, wait_until="networkidle", timeout=45000)
            print(f"[debug] page goto done: {page.url}")
            time.sleep(2)

            email_selector = fill_first_input(
                page,
                ['input[type="email"]', 'input[placeholder="Email"]', 'input[aria-label="Email"]'],
                email,
            )
            print(f"[debug] email_selector={email_selector}")
            if not email_selector:
                print("❌ Exa 登录页未找到邮箱输入框")
                return None

            # 模拟人类输入节奏
            time.sleep(1 + random.random())
            if not click_first(
                page,
                [
                    'button:text-is("Continue")',
                    'button:has-text("Continue")',
                    'button:text-is("Continue with email")',
                    'button:has-text("Continue with email")',
                    'button:text-is("Verify")',
                    'button:has-text("Verify")',
                    'button[type="submit"]',
                ],
            ):
                # 兜底回车提交
                page.press(email_selector, "Enter")
                time.sleep(1 + random.random())
                if not click_first(page, ['button[type="submit"]']):
                    print("❌ Exa 登录页未找到 Continue/Submit 按钮")
                    return None

            print("[debug] clicked continue/verify")
            time.sleep(2)

            # 更耐心地等待验证码输入框，禁止随机点击，尝试 iframe
            selectors = [
                'input[placeholder*="verification" i]',
                'input[aria-label*="verification" i]',
                'input[placeholder*="code" i]',
                'input[aria-label*="code" i]',
                'input[type="tel"]',
                'input[name*="code" i]',
            ]
            start = time.time()
            code_selector = None
            while time.time() - start < 90:
                # 先在主文档找
                for sel in selectors:
                    node = page.query_selector(sel)
                    if node:
                        code_selector = sel
                        break
                # 若找不到，尝试 iframe
                if not code_selector:
                    frames = page.frames
                    for f in frames:
                        for sel in selectors:
                            node = f.query_selector(sel)
                            if node:
                                code_selector = sel
                                page = f
                                break
                        if code_selector:
                            break
                if code_selector:
                    break
                time.sleep(1)
            if not code_selector:
                print("❌ Exa 验证码页未出现输入框")
                return None

            print(f"[debug] code_selector={code_selector}")
            print("✅ 到达 Exa 邮箱验证码页")

            code = get_email_code(email, timeout=EMAIL_CODE_TIMEOUT, service="exa")
            print(f"[debug] received code={code}")
            if not code:
                print("❌ 未拿到验证码，放弃本轮")
                return None

            code_selector = fill_first_input(
                page,
                [
                    'input[placeholder*="verification" i]',
                    'input[aria-label*="verification" i]',
                    'input[placeholder*="code" i]',
                    'input[aria-label*="code" i]',
                    'input[type="tel"]',
                    'input[name*="code" i]',
                ],
                code,
            )
            if not code_selector:
                print("❌ Exa 验证码页未找到输入框")
                return None

            if not click_first(page, ['button:text-is("VERIFY CODE")', 'button:text-is("Verify Code")', 'button:text-is("Verify")']):
                page.press(code_selector, "Enter")

            # 等待 30s 看是否跳回 Google，如果跳则放弃本轮
            try:
                page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
            except Exception:
                if "accounts.google.com" in page.url.lower():
                    print("⚠️ 检测到跳转 Google 登录，放弃本轮")
                    return None
                raise
            print("✅ Exa 登录成功")

            page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
            print("✅ Exa 登录成功")

            api_key = wait_for_api_key(page, timeout=API_KEY_TIMEOUT)
            if not api_key:
                print("⚠️  未找到 Exa API Key")
                return None

            print("🧪 验证 API Key 可用性...")
            if not verify_api_key(api_key):
                return None

            save_account(api_key)

            print("🎉 Exa 注册成功")
            print(f"   邮箱: {email}")
            print(f"   密码: {_ACCOUNT_PASSWORD_LABEL}")
            print(f"   Key : {api_key}")
            return api_key
    except Exception as exc:
        print(f"❌ Exa 注册失败: {exc}")
        traceback.print_exc()
        return None
