"""华为运动健康 App 云端的同步客户端。

本模块完全不涉及 Home Assistant：整个模块只依赖标准库且为阻塞式，因此 coordinator
可以在 executor job 中运行它。它是从华为运动健康 16.1.6.320 逆向出的协议的直接
移植，并已对线上 RU 云验证过，包括那些最耗时才挖出来的坑：

* token 是 JSON *body* 字段，App 同时还会在 ``Authorization`` 和 ``x-token``
  中发送——云端只接受以这种方式成对呈现的凭据；
* ``*Stat`` 端点接受 YYYYMMDD 整数，而 ``*ByTime`` 端点接受 YYYYMMDD 或 epoch
  毫秒，epoch *秒* 在所有端点都会被拒绝；
* 每个 ``*ByTime`` 查询窗口上限为十个自然日。
"""

from __future__ import annotations

import gzip
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Iterable, Iterator

from .const import (
    APP_HOSTS_BY_REGION,
    APP_ID,
    APP_VERSION,
    AUTH_FAILED_CODES,
    AUTHORIZE_PATH,
    BAD_HUID_CODE,
    BY_TIME_WINDOW_DAYS,
    CODE_INVALID_CODE,
    DEFAULT_REGION,
    HMS_APP_ID,
    OAUTH_BASES,
    OBTAIN_PATH,
    QUERY_PATH,
    READ_PATHS,
    REDIRECT_URI,
    REFRESH_PATH,
    REFRESH_TOKEN_TTL,
    RT_INVALID_CODE,
    SCOPES,
    SESSION_HOSTS_BY_REGION,
    SESSION_SPORT_TYPES,
    TOKEN_LIVE_SLACK,
    TOKEN_TYPE,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Linux; Android 12) hass-health-huawei/2.0"

# 登录/授权页面以账号所在区域的语言呈现。
_REGION_LANGUAGES = {DEFAULT_REGION: "zh-cn"}


def app_hosts_for(region: str | None) -> tuple[str, ...]:
    """该区域的数据主机，本地 DC 优先，另一区域的作为回退。"""
    return APP_HOSTS_BY_REGION.get(region) or APP_HOSTS_BY_REGION[DEFAULT_REGION]


def session_hosts_for(region: str | None) -> tuple[str, ...]:
    """该区域的 token 主机，本地 DC 优先，另一区域的作为回退。"""
    found = SESSION_HOSTS_BY_REGION.get(region)
    return found or SESSION_HOSTS_BY_REGION[DEFAULT_REGION]


def oauth_base_for(region: str | None) -> str:
    """该区域的登录必须指向的 OAuth base。"""
    return OAUTH_BASES.get(region) or OAUTH_BASES[DEFAULT_REGION]


class HuaweiError(Exception):
    """客户端的基础错误。"""


class HuaweiConnectionError(HuaweiError):
    """无法连接云端——稍后重试。"""


class HuaweiApiError(HuaweiError):
    """云端返回了非零的 resultCode。"""

    def __init__(self, code: Any, url: str, message: str = "") -> None:
        super().__init__(f"{url}: resultCode {code} - {message}")
        self.code = code
        self.url = url


class HuaweiAuthError(HuaweiError):
    """存储的凭据已彻底失效，账号必须重新登录。"""


# ── 小型容错解析器 ────────────────────────────────────────────────────────


def to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    got = to_float(value)
    return None if got is None else int(got)


def normalize_day(raw: Any, tz: tzinfo | None = None) -> date | None:
    """华为在不同端点混用 YYYYMMDD 整数、ISO 字符串和毫秒/纳秒 epoch。

    ``tz`` 只在 epoch 分支用到：云端按用户本地日期归档，而进程时区通常是 UTC，
    因此调用方（coordinator）会传入 Home Assistant 配置的时区。
    """
    if isinstance(raw, str) and "-" in raw:
        digits = "".join(ch for ch in raw if ch.isdigit())
        return _from_ymd(digits[:8]) if len(digits) >= 8 else None
    value = to_float(raw)
    if value is None:
        return None
    text = str(int(value))
    if len(text) == 8:
        return _from_ymd(text)
    if len(text) == 13:
        return datetime.fromtimestamp(value / 1000, tz=tz).date()
    if len(text) == 10:
        return datetime.fromtimestamp(value, tz=tz).date()
    if len(text) == 19:
        return datetime.fromtimestamp(value / 1e9, tz=tz).date()
    return None


def _from_ymd(digits: str) -> date | None:
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def day_int(value: date) -> int:
    return int(value.strftime("%Y%m%d"))


def day_windows(start: int, end: int,
                window: int = BY_TIME_WINDOW_DAYS) -> Iterator[tuple[int, int]]:
    """把 YYYYMMDD 范围切成 *ByTime 端点可接受的闭区间子段。"""
    first = _from_ymd(str(start))
    last = _from_ymd(str(end))
    if not first or not last:
        return
    while first <= last:
        stop = min(first + timedelta(days=window - 1), last)
        yield day_int(first), day_int(stop)
        first = stop + timedelta(days=1)


# ── token 状态 ──────────────────────────────────────────────────────────


@dataclass
class Tokens:
    """token 对及其签发对象的标识。

    ``on_change`` 存在的原因是：轮换会使旧的 access token 失效——新 token 对必须先
    落到持久化存储，其他任何组件才能继续代表该账号发言。
    """

    access_token: str | None = None
    refresh_token: str | None = None
    uid: str | None = None
    site_id: Any = None
    expires_at: float = 0.0
    refresh_expires_at: float = 0.0
    session_host: str | None = None
    on_change: Callable[[Tokens], None] | None = field(default=None, repr=False)
    dirty: bool = False

    @property
    def refresh_dead(self) -> bool:
        return bool(self.refresh_expires_at and time.time() > self.refresh_expires_at)

    def needs_rotation(self) -> bool:
        if not self.refresh_token or self.refresh_dead:
            return False
        return not self.access_token or time.time() > self.expires_at - TOKEN_LIVE_SLACK

    def apply(self, response: dict) -> Tokens:
        """把 obtain/refresh 响应（一个 ThirdPartyLoginInfo）应用到当前状态。"""
        access = response.get("accessToken")
        if not access:
            raise HuaweiAuthError(f"no accessToken in {str(response)[:200]}")
        self.access_token = str(access)
        expire_ms = to_float(response.get("accessTokenExpireTime"))
        # accessTokenExpireTime 是 epoch 毫秒时间戳，包括数字字符串形式。
        self.expires_at = (expire_ms / 1000 if expire_ms
                           else time.time() + 1800)
        new_refresh = response.get("refreshToken")
        # 已对线上环境验证：轮换会返回*同一个* refresh token，因此它的 180 天有效期
        # 从登录那一刻起持续计算，不能因轮换而顺延。
        if new_refresh and new_refresh != self.refresh_token:
            self.refresh_token = str(new_refresh)
            self.refresh_expires_at = time.time() + REFRESH_TOKEN_TTL
        elif not self.refresh_expires_at:
            self.refresh_expires_at = time.time() + REFRESH_TOKEN_TTL
        if response.get("uid"):
            self.uid = str(response["uid"])
        if response.get("siteId") is not None:
            self.site_id = response["siteId"]
        self.dirty = True
        if self.on_change:
            self.on_change(self)
        return self

    @property
    def as_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "uid": self.uid,
            "site_id": self.site_id,
            "expires_at": self.expires_at,
            "refresh_expires_at": self.refresh_expires_at,
            "session_host": self.session_host,
        }


# ── 登录 / token 端点 ────────────────────────────────────────────────────


def authorization_url(*, client_id: str = HMS_APP_ID, scopes: Iterable[str] = SCOPES,
                      region: str | None = None, language: str | None = None) -> str:
    """需要由用户在浏览器中完成的授权页面。

    ``hms://redirect_url`` 是该客户端唯一注册的重定向地址，因此浏览器最终会停在
    一个任何应用都打不开的链接上，code 必须从这个地址中读出。OAuth base 跟随区域：
    CN 账号只能在 cloud.huawei.com.cn 上完成授权。
    """
    query = urllib.parse.urlencode({
        "client_id": str(client_id),
        "response_type": "code",
        "access_type": "offline",
        "display": "touch",
        "scope": " ".join(scopes),
        "redirect_uri": REDIRECT_URI,
        "state": uuid.uuid4().hex[:8],
        "lang": language or _REGION_LANGUAGES.get(region or DEFAULT_REGION, "en"),
        "prompt": "consent",
    })
    return f"{oauth_base_for(region)}{AUTHORIZE_PATH}?{query}"


def authorization_code_from(answer: str) -> str | None:
    """从用户粘贴的任何内容中取出 code。

    完整的 ``hms://redirect_url?code=..`` 地址、Chrome 控制台输出的
    ``Failed to launch 'hms://redirect_url?code=..' because the scheme does not have a
    registered handler.`` 一行、或者裸 code，都按同一种方式处理。code 经过 URL 转义
    且约 360 字符长，因此只有按 query 解析才安全——直接切地址或整体 unquote 都会
    破坏它。
    """
    text = (answer or "").strip().strip('"').strip("'")
    if not text:
        return None
    query = urllib.parse.urlparse(text).query
    if query:
        found = urllib.parse.parse_qs(query).get("code")
        if found and found[0]:
            return found[0]
    if "code=" in text:
        tail = text.split("code=", 1)[1].split("&", 1)[0].split("'")[0]
        if tail:
            return urllib.parse.unquote(tail)
    if "://" in text or text.startswith("Failed to launch"):
        return None
    if any(char.isspace() for char in text):
        return None
    # 裸 code，仍是百分号转义的：它来自地址栏，而不是 JSON body。
    return urllib.parse.unquote(text)


def _session_headers(tokens: Tokens, uid: str | None = None) -> dict[str, str]:
    """ThirdPartyHttpUtils.getHeader()：x-ts + x-version，已知时再加 x-huid。"""
    headers = {
        "x-ts": str(int(time.time() * 1000)),
        "x-version": APP_VERSION,
        "Content-Type": "application/json",
    }
    if uid:
        headers["x-huid"] = str(uid)
    return headers


def _obtain_once(url: str, tokens: Tokens, uid: str | None, body: dict,
                 timeout: int) -> dict:
    """一次 session-controller POST；遇到 20010004 时去掉 x-huid 重试一次。

    存储的 x-huid 可能属于本机上更早登录过的另一个账号，因此首个 20010004 响应
    应当去掉该头重试，而不是直接报错。
    """
    response = _json_request("POST", url, headers=_session_headers(tokens, uid),
                             body=body, timeout=timeout, raw=True)
    if _result_code(response) == BAD_HUID_CODE and uid:
        # 存储的 uid 可能来自更早登录的账号；把它忘掉，避免其他组件再携带它，
        # 让登录响应来提供正确的值。
        tokens.uid = None
        response = _json_request("POST", url, headers=_session_headers(tokens, None),
                                 body=body, timeout=timeout, raw=True)
    ensure_ok(response, url)
    return response


def _obtain(path: str, tokens: Tokens, uid: str | None, body: dict, *,
            hosts: Iterable[str], timeout: int) -> dict:
    """在主机列表上执行 _obtain_once：某个 DC 不可达时顺延到下一个。"""
    hosts = tuple(dict.fromkeys(h for h in hosts if h))
    last: HuaweiConnectionError | None = None
    for host in hosts:
        url = f"{host}{path}"
        try:
            response = _obtain_once(url, tokens, uid, body, timeout)
        except HuaweiConnectionError as err:
            _LOGGER.debug("Session host %s unreachable: %s", host, err)
            last = err
            continue
        tokens.session_host = host
        return response
    if last is not None:
        raise last
    raise HuaweiConnectionError("no session hosts configured")


def exchange_authorization_code(
        code: str, *, client_id: str = HMS_APP_ID,
        region: str | None = None,
        session_host: str | None = None, uid: str | None = None,
        timeout: int = 30) -> Tokens:
    """把浏览器里抓到的 authorization code 换成 token 对。

    code 是一次性的：被拒绝后无法重放，所以交换失败意味着必须回浏览器重新走一遍
    授权流程。``region`` 决定 obtain 调用优先尝试的 session 主机——CN 账号只能在
    dbankcloud.cn 控制器上签发。
    """
    hosts = ([session_host] if session_host else []) + list(session_hosts_for(region))
    tokens = Tokens(refresh_expires_at=time.time() + REFRESH_TOKEN_TTL,
                    session_host=hosts[0])
    body = {"authorizationCode": code, "appId": str(client_id)}
    response = _obtain(OBTAIN_PATH, tokens, uid, body, hosts=hosts, timeout=timeout)
    return tokens.apply(response)


def refresh_tokens(tokens: Tokens, *, client_id: str = HMS_APP_ID,
                   region: str | None = None, timeout: int = 30) -> Tokens:
    """就地轮换 access token；token 对耗尽时抛出 HuaweiAuthError。"""
    if not tokens.refresh_token:
        raise HuaweiAuthError("no refresh token stored")
    if tokens.refresh_dead:
        raise HuaweiAuthError("refresh token older than its 180 days")
    hosts = ([tokens.session_host] if tokens.session_host else []
             ) + list(session_hosts_for(region))
    body = {"refreshToken": tokens.refresh_token, "appId": str(client_id)}
    response = _obtain(REFRESH_PATH, tokens, tokens.uid, body,
                       hosts=hosts, timeout=timeout)
    return tokens.apply(response)


def query_access_token(access_token: str, *, client_id: str = HMS_APP_ID,
                       region: str | None = None,
                       session_host: str | None = None, timeout: int = 30) -> dict:
    """查询该 access token 属于谁——响应中不含 refresh token。"""
    hosts = ([session_host] if session_host else []) + list(session_hosts_for(region))
    tokens = Tokens()
    return _obtain(QUERY_PATH, tokens, None,
                   {"accessToken": access_token, "appId": str(client_id)},
                   hosts=hosts, timeout=timeout)


# ── HTTP 请求 ───────────────────────────────────────────────────────────


def _json_request(method: str, url: str, *, headers: dict | None = None,
                  body: dict | None = None, params: dict | None = None,
                  timeout: int = 30, raw: bool = False) -> dict:
    """基于 urllib 的 POST/GET JSON 请求，处理云端特有的两类失败形态。

    未认证的 ``/dataQuery`` 调用会返回 HTTP 200 且 resultCode 为 1005，因此仅凭
    状态行永远无法判断凭据是否有效；``raw`` 让调用方自己读取 resultCode 而不是收到
    异常，这正是 1004 重试路径所需要的。
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    payload = None
    request_headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip, identity",
        "User-Agent": USER_AGENT,
        "x-request-id": str(uuid.uuid4()),
    }
    request_headers.update(headers or {})
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json;charset=utf-8")
    request = urllib.request.Request(url, data=payload, headers=request_headers,
                                     method=method)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=context) as response:
            raw_body = _decode(response.read(), response.headers.get("Content-Encoding"))
    except urllib.error.HTTPError as exc:
        body_bytes = _decode(exc.read(), exc.headers.get("Content-Encoding")
                             if exc.headers else None)
        parsed = _loads(body_bytes, url)
        code = _result_code(parsed)
        if code not in (None, 0, "0"):
            # 有些控制器把真正的判定结果放在 4xx 的 body 里。
            if not raw:
                ensure_ok(parsed, url)
            return parsed
        raise HuaweiApiError(exc.code, url, body_bytes.decode(
            "utf-8", "replace")[:300]) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HuaweiConnectionError(f"{url}: {getattr(exc, 'reason', exc)}") from exc
    parsed = _loads(raw_body, url)
    if not raw:
        ensure_ok(parsed, url)
    return parsed


def _loads(raw: bytes, url: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise HuaweiApiError(0, url, f"answer is not JSON: {raw[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise HuaweiApiError(0, url, f"answer is not an object: {str(parsed)[:200]}")
    return parsed


def _decode(raw: bytes, encoding: str | None) -> bytes:
    if raw and (encoding or "").lower().startswith("gzip"):
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    return raw


_ERROR_KEYS = ("resultCode", "code", "retCode")
_DESC_KEYS = ("resultDesc", "errorMsg", "message", "errorDesc", "retMsg")
# 只有重新走一遍浏览器登录才能清除的错误码。
_FATAL_CODES = (RT_INVALID_CODE, CODE_INVALID_CODE, BAD_HUID_CODE, 1002, 1004, 1005)


def _result_code(payload: dict) -> Any:
    for key in _ERROR_KEYS:
        if payload.get(key) is not None:
            return payload[key]
    return None


def _result_desc(payload: dict) -> str:
    for key in _DESC_KEYS:
        if payload.get(key):
            return str(payload[key])
    return str(payload)[:200]


def ensure_ok(payload: dict, url: str) -> dict:
    """把响应的 resultCode 转成调用方可据以处理的异常类型。"""
    code = _result_code(payload)
    if code in (None, 0, "0"):
        return payload
    message = _result_desc(payload)
    if code in _FATAL_CODES:
        raise HuaweiAuthError(f"{url}: {code} - {message}")
    raise HuaweiApiError(code, url, message)


# ── 数据控制器 ──────────────────────────────────────────────────────────


class HuaweiHealthClient:
    """用自动轮换的 token 对读取账号的健康数据。"""

    def __init__(self, tokens: Tokens, *, region: str | None = None,
                 hosts: Iterable[str] | None = None,
                 device_code: int | None = None, version: str = APP_VERSION,
                 token_type: int = TOKEN_TYPE, app_id: str = APP_ID,
                 client_id: str = HMS_APP_ID, language: str | None = None,
                 timeout: int = 30) -> None:
        self.tokens = tokens
        self.region = region
        self.hosts = tuple(dict.fromkeys(
            [h for h in (hosts or ()) if h] + list(app_hosts_for(region))))
        if tokens.session_host is None:
            tokens.session_host = session_hosts_for(region)[0]
        self.device_code = device_code
        self.version = version
        self.token_type = token_type
        self.app_id = app_id
        self.client_id = client_id
        self.language = language
        self.timeout = timeout

    # ── 传输层 ──────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        """同一个 token 以三种方式呈现，外加身份头。

        APK 中 Suggestion_CloudFactory/gkp.java 会同时发送 body token + Authorization +
        x-token，云端也接受这种组合；而仅带 x-huid 就足以让绑定设备的路由对错误的
        token 对返回 20010004。
        """
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {self.tokens.access_token}",
            "x-token": self.tokens.access_token or "",
            "x-token-type": str(self.token_type),
        }
        if self.tokens.uid:
            headers["x-huid"] = str(self.tokens.uid)
        if self.version:
            headers["x-version"] = self.version
        return headers

    def _body(self, extra: dict | None = None) -> dict:
        payload: dict[str, Any] = {
            "token": self.tokens.access_token or "",
            "tokenType": self.token_type,
            "appId": self.app_id,
            "ts": int(time.time() * 1000),
            "source": 1,
        }
        # 编造一个 device 字段会触发校验失败，而缺失却能通过，所以只发送登录
        # 实际返回的内容。
        if self.tokens.site_id is not None:
            payload["siteId"] = self.tokens.site_id
        if self.language:
            payload["language"] = self.language
        if self.device_code:
            payload["deviceCode"] = int(self.device_code)
        payload.update(extra or {})
        return payload

    def post(self, path: str, extra: dict | None = None) -> dict:
        if self.tokens.needs_rotation():
            self.rotate()
        response = self._send_to_hosts(path, extra)
        code = _result_code(response)
        # 1002/1004 表示 App（或上一次运行）在我们不知情的情况下轮换了 token 对。
        if code in AUTH_FAILED_CODES and self.tokens.refresh_token:
            _LOGGER.debug("Token rejected (%s), rotating once and retrying", code)
            self.rotate()
            response = self._send_to_hosts(path, extra)
            code = _result_code(response)
        if code not in (None, 0, "0"):
            if code == BAD_HUID_CODE and self.tokens.uid:
                # 存储的 uid 可能来自更早登录的账号；忘掉它，让下一次的取值来自
                # 登录响应。
                self.tokens.uid = None
                response = self._send_to_hosts(path, extra)
                code = _result_code(response)
                if code in (None, 0, "0"):
                    return response
            raise HuaweiApiError(code, f"{self.hosts[0]}{path}",
                               _result_desc(response))
        return response

    def _send_to_hosts(self, path: str, extra: dict | None) -> dict:
        """对该区域的主机发一次 POST，跳过不应答的 DC。"""
        last: HuaweiConnectionError | None = None
        for host in self.hosts:
            try:
                return self._send(f"{host}{path}", extra)
            except HuaweiConnectionError as err:
                _LOGGER.debug("Data host %s unreachable: %s", host, err)
                last = err
        raise last or HuaweiConnectionError("no data hosts configured")

    def _send(self, url: str, extra: dict | None) -> dict:
        return _json_request("POST", url, headers=self._headers(),
                             body=self._body(extra), timeout=self.timeout, raw=True)

    def rotate(self) -> None:
        refresh_tokens(self.tokens, client_id=self.client_id, region=self.region,
                       timeout=self.timeout)

    # ── 端点 ────────────────────────────────────────────────────────────
    def sports_stat(self, start: int, end: int, device_code: int | None = None) -> dict:
        """按运动类型汇总的每日总量——即运动健康 App 图表中展示的数字。"""
        body: dict[str, Any] = {"startTime": int(start), "endTime": int(end),
                                "dataSource": 2}
        code = self.device_code if device_code is None else device_code
        if code:
            body["deviceCode"] = int(code)
        return self.post(READ_PATHS["sports_stat"], body)

    def sports_daily(self, start: int, end: int) -> dict:
        return self.post(READ_PATHS["sports_daily"], {
            "startTime": int(start), "endTime": int(end), "dataSource": 2})

    def sports_detail(self, start: int, end: int, sport_types: Iterable[int],
                      query_type: int = 1, data_type: int = 2) -> dict:
        return self.post(READ_PATHS["sports_detail"], {
            "startTime": int(start), "endTime": int(end),
            "queryType": int(query_type), "dataType": int(data_type),
            "sportTypes": [int(t) for t in sport_types]})

    def health_stat(self, start: int, end: int, types: Iterable[int],
                    device_code: int = 0) -> dict:
        """按天读取心率/睡眠/压力等指标。

        ``deviceCode`` 固定为 0（整个账号）：getHealthStat 的健康指标是按账号汇总的，
        带具体设备号查询未经验证，因此这里不做设备过滤——deviceCode 选项只影响
        活动类统计（见 ``sports_stat``）。
        """
        return self.post(READ_PATHS["health_stat"], {
            "startTime": int(start), "endTime": int(end),
            "types": [int(t) for t in types],
            "deviceCode": int(device_code), "dataSource": 2})

    def health_data(self, start: int, end: int, type_id: int, query_type: int = 1,
                    data_type: int = 2) -> dict:
        return self.post(READ_PATHS["health_data"], {
            "startTime": int(start), "endTime": int(end), "type": int(type_id),
            "queryType": int(query_type), "dataType": int(data_type)})

    def bind_devices(self) -> dict:
        return self.post(READ_PATHS["bind_devices"], {})

    def _by_time_range(self, fetch: Callable[[int, int], dict], start: int,
                       end: int) -> list[dict]:
        """按允许的 <=10 天窗口，在 YYYYMMDD 范围上执行 *ByTime 查询。"""
        records: list[dict] = []
        seen: set[Any] = set()
        for window_start, window_end in day_windows(start, end):
            response = fetch(window_start, window_end)
            for rows in (response.get("data") or {}).values():
                for record in rows or []:
                    if not isinstance(record, dict):
                        continue
                    key = record.get("dataId")
                    if key is not None:
                        if key in seen:
                            continue
                        seen.add(key)
                    records.append(record)
        return records

    # ── 成型读取 ─────────────────────────────────────────────────────────
    # ``today`` 由调用方（coordinator）按 Home Assistant 配置的时区传入——
    # 云端按用户本地日期归档，而 executor 线程里的系统时区往往就是 UTC。
    # 本模块不 import homeassistant，独立调试时退回系统本地日期。
    def daily_summary(self, days: int = 3, *, today: date | None = None,
                      tz: tzinfo | None = None) -> list[dict]:
        """账号每日活动按 (day, sportType) 每天每类型一行。"""
        end_day = today or date.today()
        end = day_int(end_day)
        start = day_int(end_day - timedelta(days=max(days, 1) - 1))
        response = self.sports_stat(start, end)
        return daily_activity(response.get("sportStat") or [], tz)

    def sessions(self, days: int = 7, *,
                 today: date | None = None) -> list[dict]:
        """已记录的运动，由云端存储的按分钟分段重建而来。"""
        end_day = today or date.today()
        end = day_int(end_day)
        start = day_int(end_day - timedelta(days=max(days, 1) - 1))
        records = self._by_time_range(
            lambda a, b: self.sports_detail(a, b, SESSION_SPORT_TYPES), start, end)
        return sport_sessions(records)

    def health_summary(self, days: int = 3,
                       types: Iterable[int] = (7, 9, 11, 12), *,
                       today: date | None = None,
                       tz: tzinfo | None = None) -> list[dict]:
        end_day = today or date.today()
        end = day_int(end_day)
        start = day_int(end_day - timedelta(days=max(days, 1) - 1))
        response = self.health_stat(start, end, types=types)
        return health_series(response, tz)


# ── 响应整形 ────────────────────────────────────────────────────────────


def daily_activity(rows: Iterable[dict], tz: tzinfo | None = None) -> list[dict]:
    """getSportsStat/getSportsDimenStat 响应中每个 recordDay 对应一行。"""
    out = []
    for record in rows:
        if not isinstance(record, dict):
            continue
        day = normalize_day(record.get("recordDay"), tz)
        if not day:
            continue
        basic = record.get("sportBasicInfo") or {}
        walk = record.get("dimenDailyActivity") or {}
        goal = record.get("goalAchieveBasic") or {}
        exercise = record.get("exerciseTimeBasic") or {}
        active = record.get("activeHourBasic") or {}
        out.append({
            "date": day.isoformat(),
            "sport_type": record.get("sportType"),
            # 线上数据中 calorie 的单位是 0.001 kcal，distance 的单位是米。
            "steps": to_int(basic.get("steps")),
            "distance_m": to_int(basic.get("distance")),
            "kcal": _round(to_float(basic.get("calorie"))),
            "duration_min": to_int(basic.get("duration")),
            "walk_min": to_int(walk.get("walkDurations")),
            "active_hours": to_int(active.get("countActiveHour")),
            "exercise_min": to_int(exercise.get("totalMidHighIntensity")),
            "step_goal": to_int(goal.get("stepGoalValueStat")),
        })
    out.sort(key=lambda row: (row["date"], row["sport_type"] or 0))
    return out


def _round(calorie_milli: float | None) -> float | None:
    return None if calorie_milli is None else round(calorie_milli / 1000, 1)


def daily_totals(rows: Iterable[dict]) -> dict[str, dict]:
    """把 daily_activity 的行合并为 {ISO 日期: 行}。

    getSportsStat 每天返回一行聚合数据（sportType 0），另外视账号设备情况可能还有
    按活动类型的行；聚合行与运动健康 App 自身的圆环数值一致，因此它比任何求和结果
    都更优先。
    """
    out: dict[str, dict] = {}
    for row in rows:
        day = row["date"]
        current = out.get(day)
        if current is None or (not row["sport_type"] and current["sport_type"]):
            out[day] = row
    return out


# getHealthStat 每个指标族返回一个列表，每条记录内嵌一个 *Basic 块。
HEALTH_FAMILIES = {
    "heartRateTotal": ("heartRateBasic", {
        "lastRestHeartRate": "resting_heart_rate",
        "lastHeartRate": "heart_rate",
        "avgRestingHeartRate": "average_resting_heart_rate",
        "maxHeartRate": "max_heart_rate",
        "minHeartRate": "min_heart_rate"}),
    "professionalSleepTotal": ("professionalSleep", {
        "allSleepTime": "sleep_duration",
        "deepSleepTime": "sleep_deep",
        "lightSleepTime": "sleep_light",
        "dreamTime": "sleep_rem",
        "awakeTime": "sleep_awake",
        "sleepScore": "sleep_score",
        "sleepEfficiency": "sleep_efficiency",
        "sleepLatency": "sleep_latency",
        "wakeupCnt": "sleep_wake_ups",
        "lastAvgHeartrate": "sleep_average_heart_rate",
        "lastAvgHrv": "sleep_hrv",
        "lastAvgSpO2": "sleep_spo2"}),
    "stressTotal": ("stressBasic", {
        "meanScore": "stress_average",
        "lastScore": "stress_last",
        "maxScore": "stress_max",
        "minScore": "stress_min",
        "measureCount": "stress_measurements"}),
    "exerciseIntensityTotal": ("exerciseIntensityBasic", {
        "totalMidHighIntensity": "moderate_high_intensity_minutes"}),
}


def health_series(payload: dict, tz: tzinfo | None = None) -> list[dict]:
    """按天的健康指标，指标族取决于账号设备实际上报了哪些。"""
    rows: dict[str, dict] = {}
    for family, (block, fields) in HEALTH_FAMILIES.items():
        for record in payload.get(family) or []:
            if not isinstance(record, dict):
                continue
            day = normalize_day(record.get("recordDay"), tz)
            values = record.get(block) or {}
            if not day or not isinstance(values, dict):
                continue
            row = rows.setdefault(day.isoformat(), {"date": day.isoformat()})
            for key, name in fields.items():
                got = to_float(values.get(key))
                # 0 和 -1 是云端表示"手环从未测量过此项"的占位值，不是真实读数。
                if got is not None and got > 0:
                    row[name] = got
            if block == "professionalSleep":
                # 一晚的睡眠归属于醒来的那一天，所以带上它的真实起止时间。
                for key, name in (("fallAsleepTime", "fall_asleep"),
                                  ("wakeupTime", "wakeup")):
                    stamp = to_float(values.get(key))
                    if stamp and stamp > 1e12:
                        row[name] = datetime.fromtimestamp(
                            stamp / 1000, tz=tz).strftime("%Y-%m-%d %H:%M")
    return [rows[key] for key in sorted(rows)]


def sport_sessions(records: Iterable[dict], gap_minutes: int = 15) -> list[dict]:
    """从按分钟的分段重建运动记录。

    每台感知到该运动某一分钟的设备都会各自上传一份副本（同一次骑行中手环和手机
    都会出现），因此对原始分段求和会重复计数。改为在每个运动类型下、每个自然分钟
    只保留数据最全的一条记录，再把连续的分钟拼接成一次运动。
    """
    by_type: dict[Any, dict[int, dict]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        start = to_float(record.get("startTime"))
        end = to_float(record.get("endTime"))
        if not start or not end:
            continue
        basic = (record.get("sportBasicInfos") or [{}])[0]
        weight = (to_float(basic.get("distance")) or 0, to_float(basic.get("steps")) or 0,
                  to_float(basic.get("calorie")) or 0)
        minutes = by_type.setdefault(to_int(record.get("sportType")), {})
        key = int(start // 60000)
        if key not in minutes or weight > minutes[key]["weight"]:
            minutes[key] = {"start_ms": start, "end_ms": end, "basic": basic,
                            "device_code": record.get("deviceCode"), "weight": weight}

    sessions: list[dict] = []
    for sport_type, minutes in sorted(by_type.items(), key=lambda kv: kv[0] or 0):
        for _, entry in sorted(minutes.items()):
            basic = entry["basic"]
            start, end = entry["start_ms"], entry["end_ms"]
            duration = to_int(basic.get("duration")) or 0
            if (sessions and sessions[-1]["sport_type"] == sport_type
                    and start - sessions[-1]["end_ms"] <= gap_minutes * 60000):
                last = sessions[-1]
                last["end_ms"] = max(last["end_ms"], end)
                last["duration_min"] += duration
                last["steps"] += to_int(basic.get("steps")) or 0
                last["distance_m"] += to_int(basic.get("distance")) or 0
                last["kcal"] += (to_float(basic.get("calorie")) or 0) / 1000
                last["segments"] += 1
                continue
            sessions.append({
                "sport_type": sport_type,
                "start_ms": start, "end_ms": end,
                "duration_min": duration,
                "steps": to_int(basic.get("steps")) or 0,
                "distance_m": to_int(basic.get("distance")) or 0,
                "kcal": (to_float(basic.get("calorie")) or 0) / 1000,
                "segments": 1,
                "device_code": entry["device_code"],
            })
    for session in sessions:
        session["start"] = _stamp(session.pop("start_ms"))
        session["end"] = _stamp(session.pop("end_ms"))
        session["kcal"] = round(session["kcal"], 1)
    sessions.sort(key=lambda session: session["start"])
    return sessions


def _stamp(ms: float | None) -> datetime | None:
    """云端时间戳是 epoch 毫秒；返回带 UTC 时区的 datetime，交给展示层换时区。"""
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
