"""Huawei Health 配置流程：浏览器登录 + 粘贴授权码。

应用云端只接受令牌对，而令牌对只能由 HMS Core 本该执行的 HUAWEI ID 登录来签发。
登录本身受华为滑动验证码保护，任何脚本都无法通过，因此流程刻意在此处停下，
请用户在浏览器中完成登录，并把浏览器最终停下的地址粘贴回来。
除这一步手动操作外，其余全部自动化：登录 URL 按所选云区域动态生成、
code 会从粘贴的任意文本中解析出来、令牌对存入配置项。
"""

from __future__ import annotations

import hashlib
import logging
from functools import partial
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .client import (
    HuaweiAuthError,
    HuaweiConnectionError,
    HuaweiError,
    Tokens,
    authorization_code_from,
    authorization_url,
    exchange_authorization_code,
    query_access_token,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CODE,
    CONF_DEVICE_CODE,
    CONF_ENABLE_HEALTH,
    CONF_ENABLE_WORKOUTS,
    CONF_EXPIRES_AT,
    CONF_HISTORY_DAYS,
    CONF_REFRESH_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_SESSION_HOST,
    CONF_SITE_ID,
    CONF_UID,
    CONF_UPDATE_INTERVAL,
    CONF_WORKOUT_DAYS,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_REGION,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WORKOUT_DAYS,
    DOMAIN,
    MAX_HISTORY_DAYS,
    MIN_HISTORY_DAYS,
    REGIONS,
)

_LOGGER = logging.getLogger(__name__)

DOCS_URL = "https://github.com/hassstore/hass_health_huawei#readme"

_REGION_SCHEMA = vol.Schema({
    vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
        SelectSelectorConfig(
            options=list(REGIONS),
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="region",
        )
    )
})

_CODE_SCHEMA = vol.Schema({vol.Required(CONF_CODE): cv.string})


def _placeholders(authorize_url: str) -> dict[str, str]:
    return {"authorize_url": authorize_url, "docs": DOCS_URL}


def _entry_data(tokens: Tokens, region: str) -> dict[str, Any]:
    return {
        CONF_UID: tokens.uid,
        CONF_SITE_ID: tokens.site_id,
        CONF_ACCESS_TOKEN: tokens.access_token,
        CONF_REFRESH_TOKEN: tokens.refresh_token,
        CONF_EXPIRES_AT: tokens.expires_at,
        CONF_REFRESH_EXPIRES_AT: tokens.refresh_expires_at,
        CONF_SESSION_HOST: tokens.session_host,
        CONF_REGION: region,
    }


class HuaweiHealthConfigFlow(ConfigFlow, domain=DOMAIN):
    """处理浏览器辅助登录：新账号配置或重新授权。"""

    VERSION = 1

    def __init__(self) -> None:
        self._region: str = DEFAULT_REGION
        self._authorize_url: str = ""
        self._reauth_entry: ConfigEntry | None = None
        self._uid: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
            config_entry: ConfigEntry) -> HuaweiHealthOptionsFlow:
        return HuaweiHealthOptionsFlow()

    # ── 新账号 ──────────────────────────────────────────────────────────
    async def async_step_user(
            self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """先选云区域——它决定登录走哪个 OAuth 域名。"""
        if user_input is not None:
            self._region = str(user_input[CONF_REGION])
            return await self.async_step_login()
        return self.async_show_form(step_id="user", data_schema=_REGION_SCHEMA)

    async def async_step_login(
            self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """唯一能自动化的环节就是生成这个 URL。"""
        if user_input is not None:
            return await self.async_step_code()
        self._authorize_url = authorization_url(region=self._region)
        return self.async_show_form(
            step_id="login", description_placeholders=_placeholders(self._authorize_url))

    async def async_step_code(
            self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """把粘贴的地址兑换成令牌对。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            code = authorization_code_from(user_input[CONF_CODE])
            if not code:
                errors[CONF_CODE] = "no_code"
            else:
                try:
                    tokens = await self.hass.async_add_executor_job(
                        partial(exchange_authorization_code, code,
                                region=self._region, uid=self._uid))
                except HuaweiAuthError as err:
                    # code 是一次性的，本次流程必须重新生成自己的登录页。
                    _LOGGER.error("Huawei refused the authorization code: %s", err)
                    errors[CONF_CODE] = "invalid_code"
                    self._authorize_url = authorization_url(region=self._region)
                except HuaweiConnectionError:
                    errors[CONF_CODE] = "cannot_connect"
                except HuaweiError as err:
                    _LOGGER.error("Huawei token exchange failed: %s", err)
                    errors[CONF_CODE] = "unknown"
                else:
                    return await self._finish(tokens)
        return self.async_show_form(
            step_id="code", data_schema=_CODE_SCHEMA, errors=errors,
            description_placeholders=_placeholders(self._authorize_url))

    async def _finish(self, tokens: Tokens) -> ConfigFlowResult:
        """把令牌对写回正在重新授权的条目，或为账号新建一条。"""
        data = _entry_data(tokens, self._region)

        if (entry := self._reauth_entry) is not None:
            await self.async_set_unique_id(tokens.uid or entry.unique_id)
            # 换了 HUAWEI ID 就不能写回原条目，否则两个账号会争同一个 unique_id。
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            # token 轮换响应偶尔不带 uid/siteId，别用 None 覆盖已存的值。
            merged = {**entry.data,
                      **{key: value for key, value in data.items()
                         if value is not None}}
            return self.async_update_reload_and_abort(
                entry, data_updates=merged)

        unique_id = tokens.uid or hashlib.sha256(
            (tokens.access_token or "").encode()).hexdigest()[:16]
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates=data)
        name = await self.hass.async_add_executor_job(
            partial(_profile_name, tokens.access_token, tokens.session_host,
                    self._region))
        return self.async_create_entry(title=name or f"Huawei {unique_id[-4:]}",
                                       data=data)

    # ── 重新授权 ──────────────────────────────────────────────────────────
    async def async_step_reauth(
            self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """refresh token 失效（华为的 180 天到期，或账号已退出登录）。"""
        self._reauth_entry = self._get_reauth_entry()
        self._uid = entry_data.get(CONF_UID)
        self._region = str(entry_data.get(CONF_REGION) or DEFAULT_REGION)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
            self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_code()
        self._authorize_url = authorization_url(region=self._region)
        entry = self._reauth_entry
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders=dict(
                _placeholders(self._authorize_url),
                account=entry.title if entry else ""))


def _profile_name(access_token: str | None, session_host: str | None,
                  region: str | None) -> str | None:
    """取 HUAWEI ID 昵称作为条目标题；取不到昵称也不阻塞配置。"""
    if not access_token:
        return None
    try:
        info = query_access_token(access_token, session_host=session_host,
                                  region=region)
    except HuaweiError as err:
        _LOGGER.debug("Could not read the HUAWEI ID profile: %s", err)
        return None
    name = info.get("nickName") or info.get("displayName")
    return str(name) if name else None


class HuaweiHealthOptionsFlow(OptionsFlowWithReload):
    """按账号调优：轮询频率与回溯天数。

    继承 OptionsFlowWithReload：选项保存后由 HA 负责重载该条目，
    因此集成不再注册 update listener（两者同时使用会被 HA 拒绝）。
    """

    async def async_step_init(
            self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            options = {
                CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                CONF_HISTORY_DAYS: int(user_input[CONF_HISTORY_DAYS]),
                CONF_WORKOUT_DAYS: int(user_input[CONF_WORKOUT_DAYS]),
                CONF_ENABLE_WORKOUTS: bool(user_input[CONF_ENABLE_WORKOUTS]),
                CONF_ENABLE_HEALTH: bool(user_input[CONF_ENABLE_HEALTH]),
            }
            device_code = user_input.get(CONF_DEVICE_CODE)
            if device_code:
                options[CONF_DEVICE_CODE] = int(device_code)
            return self.async_create_entry(title="", data=options)
        current = self.config_entry.options
        days = vol.All(vol.Coerce(int),
                       vol.Range(min=MIN_HISTORY_DAYS, max=MAX_HISTORY_DAYS))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL,
                                        DEFAULT_UPDATE_INTERVAL.
                                        total_seconds() // 60)):
                    vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
                vol.Optional(CONF_HISTORY_DAYS,
                             default=current.get(CONF_HISTORY_DAYS,
                                                 DEFAULT_HISTORY_DAYS)): days,
                vol.Optional(CONF_WORKOUT_DAYS,
                             default=current.get(CONF_WORKOUT_DAYS,
                                                 DEFAULT_WORKOUT_DAYS)): days,
                vol.Optional(CONF_ENABLE_WORKOUTS,
                             default=current.get(CONF_ENABLE_WORKOUTS, True)): bool,
                vol.Optional(CONF_ENABLE_HEALTH,
                             default=current.get(CONF_ENABLE_HEALTH, True)): bool,
                vol.Optional(CONF_DEVICE_CODE,
                             default=current.get(CONF_DEVICE_CODE) or 0): cv.positive_int,
            }))
