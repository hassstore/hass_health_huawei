"""Huawei Health 集成所用的常量。"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "hass_health_huawei"
PLATFORMS: Final = ["sensor"]

# ── 云区域 ──────────────────────────────────────────────────────────────
# Health 应用自己的云分为中国大陆数据中心
# （*.dbankcloud.cn，OAuth 走 cloud.huawei.com.cn）和国际数据中心
# （*.things.dbankcloud.ru / *.dbankcloud.com，OAuth 走 cloud.huawei.com）。
# 一个账号只属于其中一个：CN 数据中心拒绝国际 token，反之亦然，
# 所以 config flow 中选择的区域同时决定了登录所用的 OAuth 基址
# 和每次读取所用的主机。每个区域先列出本区域主机，
# 再以另一区域的主机作为回退（跨区域请求会得到干净的认证错误，而不是挂起）。

REGION_CN: Final = "cn"
REGION_GLOBAL: Final = "global"
DEFAULT_REGION: Final = REGION_CN
REGIONS: Final = (REGION_CN, REGION_GLOBAL)

OAUTH_BASES: Final = {
    REGION_CN: "https://oauth-login.cloud.huawei.com.cn/oauth2",
    REGION_GLOBAL: "https://oauth-login.cloud.huawei.com/oauth2",
}

# Health 应用的数据控制器（GRS key domainHealthCloudApp）：
# 每个国家组一个主机，各自负责应答本区域的任意客户端。
APP_HOSTS_BY_REGION: Final = {
    REGION_CN: (
        "https://healthdata.dbankcloud.cn",
        "https://sportdata-drru.things.dbankcloud.ru",
        "https://sportdata-dre.things.dbankcloud.com",
        "https://sportdata-dra.things.dbankcloud.com",
    ),
    REGION_GLOBAL: (
        "https://sportdata-drru.things.dbankcloud.ru",
        "https://sportdata-dre.things.dbankcloud.com",
        "https://sportdata-dra.things.dbankcloud.com",
        "https://healthdata.dbankcloud.cn",
    ),
}

# Token 的签发/轮换在与数据不同的控制器上
# （GRS key domainHealthCloudCommon）。
SESSION_HOSTS_BY_REGION: Final = {
    REGION_CN: (
        "https://healthcommon-drcn.dbankcloud.cn",
        "https://healthcommon-drru.things.dbankcloud.ru",
        "https://healthcommon-dre.things.dbankcloud.com",
    ),
    REGION_GLOBAL: (
        "https://healthcommon-drru.things.dbankcloud.ru",
        "https://healthcommon-dre.things.dbankcloud.com",
        "https://healthcommon-drcn.dbankcloud.cn",
    ),
}

AUTHORIZE_PATH: Final = "/v3/authorize"
OBTAIN_PATH: Final = "/commonAbility/userAccessToken/obtain"
REFRESH_PATH: Final = "/commonAbility/userAccessToken/refresh"
QUERY_PATH: Final = "/commonAbility/userAccessToken/query"

# token 对所面向的 app id —— com.huawei.health.BuildConfig.HMS_APPLICATION_ID。
# 它是一个*数字*而不是包名，用错误的 app id 兑换 code 会被拒绝。
HMS_APP_ID: Final = "10414141"
APP_ID: Final = "com.huawei.health"
TOKEN_TYPE: Final = 2
# x-version 为 "and_health_" + 该协议来源的 Health 版本的 versionName。
APP_VERSION: Final = "and_health_16.1.6.320"
# ThirdLoginDataStorageUtil.REFRESH_TOKEN_INTERVAL，单位为秒。
REFRESH_TOKEN_TTL: Final = 180 * 24 * 3600
# access token 的寿命只有几十分钟；在到期之前提前轮换，而不是等失败再处理。
TOKEN_LIVE_SLACK: Final = 120

REDIRECT_URI: Final = "hms://redirect_url"
SCOPES: Final = (
    "https://www.huawei.com/auth/account/base.profile",
    "https://www.huawei.com/healthkit/step.both",
    "https://www.huawei.com/healthkit/activityrecord.both",
)

READ_PATHS: Final = {
    "sports_stat": "/dataQuery/sport/v2/getSportsStat",
    "sports_daily": "/dataQuery/sport/getSportsDimenStat",
    "sports_detail": "/dataQuery/sport/getSportsDataByTime",
    "health_stat": "/dataQuery/health/getHealthStat",
    "health_data": "/dataQuery/health/getHealthData",
    "sync_versions": "/dataQuery/common/getSyncVersions",
    "bind_devices": "/profile/device/getBindDevice",
}

# getHealthStat/getHealthData 的 type id，按应答中 samplePoint 的键命名。
HEALTH_TYPES: Final = {7: "heart_rate", 9: "professional_sleep", 11: "stress",
                       12: "exercise_intensity"}
# *ByTime 端点的 sportTypes。5 是手机/手环自动记录的逐分钟步行轨迹
# （覆盖全天，因此不算一次锻炼），6/7/8 是睡眠片段。
# 14 会出现在数据中但应用从不为其命名，因此读作 unknown。
SPORT_TYPES: Final = {1: "stairs", 2: "hill", 3: "cycling", 4: "running", 5: "walking",
                      6: "deep_sleep", 7: "light_sleep", 8: "awake", 9: "swimming",
                      10: "other", 14: "unknown"}
SESSION_SPORT_TYPES: Final = tuple(t for t in SPORT_TYPES if t not in (5, 6, 7, 8))

# 无论使用哪种时间戳格式，getSportsDataByTime/getHealthData 对超过十天的跨度
# 都会返回 1001，所以更长的拉取必须分块进行。
BY_TIME_WINDOW_DAYS: Final = 10

# resultCode -> 对应该如何处理。
AUTH_FAILED_CODES: Final = (1002, 1004)
RT_INVALID_CODE: Final = 20020003
CODE_INVALID_CODE: Final = 20020001
BAD_HUID_CODE: Final = 20010004
INVALID_DEVICE_CODE: Final = 30005

# ── Home Assistant 配置键 ───────────────────────────────────────────────
CONF_UID: Final = "uid"
CONF_SITE_ID: Final = "site_id"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_EXPIRES_AT: Final = "expires_at"
CONF_REFRESH_EXPIRES_AT: Final = "refresh_expires_at"
CONF_SESSION_HOST: Final = "session_host"
CONF_TOKEN_TYPE: Final = "token_type"
CONF_DEVICE_CODE: Final = "device_code"
CONF_HISTORY_DAYS: Final = "history_days"
CONF_WORKOUT_DAYS: Final = "workout_days"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_ENABLE_WORKOUTS: Final = "enable_workouts"
CONF_ENABLE_HEALTH: Final = "enable_health_metrics"
CONF_REGION: Final = "region"
CONF_CODE: Final = "code"

DEFAULT_UPDATE_INTERVAL: Final = timedelta(minutes=15)
DEFAULT_HISTORY_DAYS: Final = 3
DEFAULT_WORKOUT_DAYS: Final = 7
MIN_HISTORY_DAYS: Final = 1
MAX_HISTORY_DAYS: Final = 30
