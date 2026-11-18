# 语音媒体桥 · Voice and media bridge

[![CI](https://github.com/xiaoxia-5201314/dsh-media-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaoxia-5201314/dsh-media-bridge/actions/workflows/ci.yml)

> **English summary.** A DSH bundle that gives the agent eight tools: `check_config`, `transcribe`,
> `synthesize`, `generate_image`, `generate_video`, `job_status`, `list_models`, `voice_manage`. It uses a classical pipeline
> (ASR → agent → TTS) instead of an expensive Realtime model. **Every capability walks a provider
> chain order: subscription plans first, then pay-as-you-go (Xiaomi free tier, DeepSeek, MiniMax, Qwen).**
> A call targets exactly **one** upstream - the first usable entry of that order, or the
> `provider`/`model` you pass. Failures return `{code, provider, model, retryable}` and are
> never retried or re-routed for you, because image and video generation bills even when the
> client times out.
> **No API key value is stored in this directory** — references live in
> `%USERPROFILE%\.dsh\.credentials.yaml`, outside the bundle. Details in Chinese below.

一个 DSH 配置型插件：把语音转文字、文字转语音、文生图、文生视频聚合成智能体可直接调用的工具。
不用 Realtime 全双工模型，走传统 Pipeline（ASR → 智能体 → TTS）。**每次调用只打一个上游**：省略参数时取该能力
顺序列表里第一个可用的，也可以用 `provider` / `model` 明确指定；失败时返回结构化错误码，由智能体决定换不换、
要不要重试——生图与生视频不幂等，超时也计费，替你重试就是替你花钱。

## 花费上限（v0.9.3）

`budget` 配置项，三档独立、**在发出请求之前**拦截：

| 档位 | 字段 | 默认 | 语义 |
|---|---|---|---|
| 统一日额 | `daily_limit_cny` | **10** | 本地日期零点自动清零 |
| 按品牌 | `provider_limit_cny[minimax-payg]` 等 | 未设=不限 | 与日额独立判断 |
| 按模型 | `model_limit_cny["minimax-payg:MiniMax-H3-Lite"]` 或裸模型名 | 未设=不限 | 同上 |

负数 = 不限；`0` = 关闭该类调用。被挡时返回 `code=budget_exhausted`，消息里写清是哪一档、
已用多少、本次预估多少，并且**请求没有发出、不产生费用**。成功调用会在结果里回显
`cost_estimate_cny` 与 `budget_used_today_cny`。

估算用的价目表**只包含已从定价页确认或本机实测过的项**，其余取保守上界（宁多算不少算），
可用 `budget.prices_cny` 覆盖；账本是链文件同目录的 `media-bridge.usage.json`（0600）。
**账本无法写入时调用会被拒绝**（`budget_unavailable`）——宁可挡住，也不在无记账状态下花钱。

## 上游选择（v0.7：单目标，不再自动降级）

一个 MCP 行、六把凭据。每项能力维护一条**顺序列表**（页面上可拖拽、可展开折叠、可整家停用），一次调用只取一个目标：

| 能力 | 顺序列表（第 1 环 = 省略 provider 时的默认目标） |
|---|---|
| `transcribe` 语音识别 | **千问套餐** → 小米(免费) → DeepSeek按量 → 千问按量 → MiniMax套餐 → MiniMax按量 |
| `synthesize` 语音合成 | **MiniMax套餐** → 千问套餐 → 小米(免费) → DeepSeek按量 → MiniMax按量 → 千问按量 |
| `generate_image` 生图 | **MiniMax套餐** → 千问套餐 → MiniMax按量 → 千问按量 |
| `generate_video` 视频 | **千问套餐** → MiniMax按量 → 千问按量 |

按量兜底顺序按你的要求：**小米（TTS 限时免费）→ DeepSeek → MiniMax → 千问**。

- **怎么指定**：`provider` 选厂商，`model` 选该厂商该能力的具体模型；都省略时用列表第 1 环。
  每家该能力的模型顺序本身也可拖拽排序，`list_models` 还能实时拉厂商目录看有没有新模型。
- **失败就报错，不擅自换家**：错误体是 `{code, provider, model, retryable, message}`，`code` 有
  `bad_request / unsupported_provider / provider_disabled / no_credentials / no_target /
  model_not_supported / unknown_task / provider_mismatch / upstream_failed / model_list_unavailable`。
  `retryable: true` 给限流与 5xx（429/502/503/504），其余为 false。
- **默认目标会跳过没有凭据的环节**（选第一个可用的），整条链都没凭据才 `no_target`；
  显式指定 `provider` 而它没有凭据时直接 `no_credentials`，不会悄悄换成别家。`check_config` 只报
  配置状态（布尔值），永不输出密钥。

## 千问两端的真实覆盖（2026-11-17 实测）

同一个 API Key 类型不代表同一模型集合，以下都是打出来的，不是文档推的：

| 模型 | 套餐端 `qwen-plan` | 按量端 `qwen-payg` |
|---|---|---|
| `qwen-audio-3.0-tts-plus` | ✅ 默认 | ⚠️ 免费额度耗尽（账号开了「仅用免费额度」） |
| `qwen-audio-3.1-tts-plus` | ❌ `Model not exist` | ✅ **默认**（支持指令/情感标签/SSML/复刻设计音色） |
| `cosyvoice-v3-flash` | ❌ `Model not exist` | ✅ |
| `qwen3-tts-flash` | 未测 | ✅ |
| `qwen-audio-3.0-asr-flash` | ✅ 默认 | — |
| `qwen-audio-3.1-asr-flash` | ❌ `Model not exist` | ✅ 默认 |
| `voice-enrollment`（造音色） | ❌ `Model not exist` | ✅ 创建免费 |

- 因为音色 ID **按模型族隔离**，改默认模型必须同时改默认音色：按量端默认现在是
  `qwen-audio-3.1-tts-plus` + `longanhuan_v3.1`（实测不指定音色也能出音）。
- 上表里所有 ❌ 现在都在**发出请求之前**返回 `code=provider_mismatch`，并告诉你改用哪一端，
  不再是一句匿名的 `upstream_failed`。

## v0.9 表现力参数与音色复刻/设计

- **`synthesize` 新增千问侧参数**（全部实测通过，返回里的 `params_used` 回显真正发出去的字段）：
  `language_hints`（官方说明**只处理数组第一个元素**，多传直接拒绝）、`hot_fix`
  （`pronunciation` 标拼音 / `replace` 替换文本，专名读音用它）、`volume`（0~100）、
  `rate`（0.5~2.0）。Qwen3-TTS 没有 `volume`/`rate`，会被忽略并在 `ignored_params` 里说明；
  但 `language_hints`/`hot_fix`/`instruction`/SSML 这类**改变听感**的参数一律直接报错，不静默丢。
- **新工具 `voice_manage`**：`create_clone`（wav/mp3/m4a，≥16 kHz，≤60 秒，≤10 MB，本地文件转 data URI）、
  `create_design`（文字描述造音色，返回试听 wav）、`status`（查 DEPLOYING/OK）。
  **实测结论：`voice-enrollment` 只在按量端有**，套餐端回答 `Model not exist`——现在这个情况返回
  `code=provider_mismatch` 并告诉你改用 `provider=qwen-payg`。创建音色免费，用它合成按目标模型计费；
  音色绑定 `target_model`，合成时必须传同一个模型。上限 1000 个、一年未用自动删除。
- **没有实现**：音色的列表与删除——官方我只拿到 `create_voice` / `query_voice` 两个 action 的确切形状，
  不凭猜测调未文档化的接口；要清理请到控制台。
- **MiniMax 复刻依然没接**：首次使用扣 ¥9.9 解锁费，而小米的 `mimo-v2.5-tts-voiceclone` 免费且已可用。

## v0.8 新增的两项能力

- **MiniMax 语音识别 `asr-1.0`**：`provider=minimax-plan` 或 `minimax-payg`。单次上限 **500 秒 / 50 MB**
  （对比：DeepSeek 只有 120 秒 / 8 MiB），`response_format=verbose_json` 返回 `n_speakers` 与逐句
  `speaker/start/end`，`srt`/`vtt` 直接落成本地字幕文件并返回 `subtitle_path`；`language` 走**请求头**
  （zh/yue/ja/ko/th/vi/id/ms/fil/en/fr/de/es/it/pt/pl/ru/uk/ar/tr），不传即自动混合识别。
  已实测：4 秒音频 → `duration_ms=3636`、`n_speakers=1`、`trace_id` 齐全。
- **MiniMax H3 图片编辑**：`generate_image` 传 `images=[...]`（1~16 项）即改走 `/v1/images/edits`。
  本地路径自动转 data URI，也可直接给 http(s) URL 或 `mm_file://`；预检把官方限制搬到调用前：
  格式 JPG/PNG/WEBP/HEIC/HEIF、单张 ≤64 MiB、base64 后总请求体 ≤64 MiB。**注意**：图片编辑需要
  M 套餐包含 H3-Image（本机实测包含）；未人工核对成图内容，只验证了链路与落盘。
- MiniMax 加入转写链**末尾**，默认目标仍是千问套餐，现有调用行为不变。
- `transcribe` 的 schema 原先是 `additionalProperties: false` 却没有声明 `language`，
  也就是说小米早就读这个参数、但谁都传不进来。现已补上 `language` 与 `response_format`。

## 配置页面（v0.6 新增）

设置面板里多了一页 **「语音媒体桥」**（Settings → 语音媒体桥），可视化编辑：

- **降级链**：每项能力的上游顺序（↑↓ 调整）、逐个启用/停用；
- **模型 ID**：每家每个能力用的模型（留空 = 内置默认）；
- **音色**：MiniMax 15 个实测可用音色下拉、DeepSeek stella/tide、千问按量自由填、小米 mimo_default；
- **接口地址**：每家 base URL 覆盖（留空 = 默认，套餐/按量域名别混）；
- **凭据状态**：六个引用名当前是否已配置（只显示布尔，不碰值）。

保存走 DSH 设置域（与官方设置页同一通道），写入 `media-bridge-settings` 行的 Config，
同时自动同步智能体工具 `media_bridge_settings`（get/set 同一份配置），并立即重写
`%USERPROFILE%\.dsh\media-bridge.chains.json`——MCP server 每次调用都重读这个文件，
**改完下一通调用即生效，不用重启会话**。

配置优先级：本页面/工具写的设置 > `cordis.patch.yml` 里 `media-bridge-settings` 的 config > 内置默认。
YAML 直接编辑同样支持（字段：chains / models / voices / baseUrls / disabled）。

实测样例（**v0.5 的自动降级行为**，v0.7 起已移除；保留在此是因为它是各端点错误码的一手证据）：本机 MiniMax 套餐配额正好耗尽：`synthesize` → `fallback_from: ["minimax-plan 2056: 已达到 Token Plan 用量上限…"]` → `provider: xiaomi` 成功出音；小米合成的语音再走 `transcribe` 由千问套餐识别，文字一字不差。

## 密钥怎么填（最重要）

`cordis.patch.yml` 里**只有引用名，没有值**（六个引用都指向凭据库，值不落本目录）：

```yaml
inject: [credentials]      # ← 必需，见下
env:
  MEDIA_BRIDGE_MINIMAX_PLAN: '${credential:MINIMAX_API_KEY}'                # MiniMax M Plan 订阅 Key
  MEDIA_BRIDGE_QWEN_PLAN: '${credential:QWEN_TOKEN_PLAN_CN_API_KEY}'        # 千问 Token Plan 套餐 Key
  MEDIA_BRIDGE_XIAOMI: '${credential:XIAOMI_API_KEY}'                       # 小米 MiMo（TTS 免费）
  MEDIA_BRIDGE_DEEPSEEK: '${credential:DEEPSEEK_API_KEY}'                   # DeepSeek 按量
  MEDIA_BRIDGE_MINIMAX_PAYG: '${credential:MINIMAX_CN_API_KEY}'             # MiniMax 按量
  MEDIA_BRIDGE_QWEN_PAYG: '${credential:QIANWEN_API_KEY}'                   # 千问按量
```

值填在（插件目录之外）：`%USERPROFILE%\.dsh\.credentials.yaml` 的 `refs:` 下。

| 填法 | 位置 | 改动后是否要重启 |
|---|---|---|
| 凭据库文件（推荐） | `%USERPROFILE%\.dsh\.credentials.yaml` 的 `refs:` | **不需要**，文件被监视、自动重载并重建连接 |
| 启动前环境变量 | 启动 DSH 之前设好 | **需要**；环境是启动时的快照 |
| `.env` 文件 | 项目 `.env` 或 `%USERPROFILE%\.dsh\.env`，且必须在插件目录之外 | 建议重启后确认，优先级最低 |

⚠️ **`.gitignore` 只挡 git，挡不住 zip、复制目录、上传分享。** 值只放凭据库，本目录永远不放。

### `inject: [credentials]` 是必需的

用了 `${credential:...}` 的行必须声明 `inject: [credentials]`，否则插件**加载阶段直接失败**
（`MCP credential templates require a credentials provider`），不是"等 key"。

### 套餐 key 与按量 key 不能混（实测）

- 千问：套餐 key 只认 `token-plan.maas.qianwenaiapi.com`，按量 key 只认 `maas.qianwenaiapi.com`，混用 401。
- MiniMax：两把 key 在 `api.minimax.cn` 都能用；`api.minimax.io` 对套餐 key 401。
  MiniMax 接口对无效 key 也返回 **HTTP 200**（错误藏在 `base_resp.status_code`），server 已校验。

安全边界（诚实说明）：凭据库文件仅当前 OS 用户可读，但智能体的工具进程以同一用户身份运行，
**能读到它**；这是本地保管，不是对智能体的隔离。

## 工具（模型侧名字 `mcp__media_bridge__<工具名>`）

| 工具 | 作用 |
|---|---|
| `check_config` | 报告六个引用是否已配置、每条链当前可用的环节、输出目录；只返回布尔 |
| `transcribe` | 本地音频 → 文字；mp3 与 16/24 kHz 单声道 WAV 直传，其他格式需 ffmpeg |
| `synthesize` | 文字 → 本地音频；`voice` 只对本链生效的上游有意义，其他家自动用各自默认音色（结果含 `voice_used`/`voice_overridden`） |
| `generate_image` | 文生图，同步返回，立即下载落盘 |
| `generate_video` | 提交异步任务，返回 `job_id`（MiniMax 兜底时 duration 只能 6/10、resolution 768P/1080P） |
| `job_status` | 查任务，成功后自动下载视频到本地 |

产物默认保存在 `media-out/`，工具只返回本地路径（云端 URL 普遍 24 小时过期）。`MEDIA_BRIDGE_OUT` 可覆盖。

## 后端（链上每一环的真实端点）

| 上游 | 能力 | 端点 | 默认模型 |
|---|---|---|---|
| 千问套餐 | ASR | `token-plan…/api/v1/services/aigc/multimodal-generation/generation` | `qwen-audio-3.0-asr-flash` |
| 千问套餐 | 视频 | `token-plan…/api/v1/services/aigc/video-generation/video-synthesis` + `…/api/v1/tasks/{id}` | `wan3.0-video` |
| 千问套餐 | 生图 | `token-plan…/api/v1/services/aigc/multimodal-generation/generation`（size 用 `*`） | `qwen-image-3.0-pro` |
| MiniMax | TTS | `api.minimax.cn/v1/t2a_v2`（音频是 hex 编码） | `speech-02-hd` |
| MiniMax | 生图 | `api.minimax.cn/v1/image_generation` | `image-01` |
| MiniMax | 视频 | `api.minimax.cn/v2/video_generation` + `/v2/query/video_generation/{id}`（**只此一族**，V1/Hailuo 已下线） | `MiniMax-H3-Lite` |
| 小米 | TTS | `api.xiaomimimo.com/v1/chat/completions`（目标文本放 assistant 消息，返回 base64） | `mimo-v2.5-tts` |
| 小米 | ASR | 同上，content 传 `input_audio` data URI | `mimo-v2.5-asr` |
| DeepSeek | ASR | `api.deepseek.com/v1/tools/asr`（multipart） | — |
| DeepSeek | TTS | `api.deepseek.com/v1/tools/tts`（直接返回 WAV 字节） | — |
| 千问按量 | TTS | `maas…/api/v1/services/audio/tts/SpeechSynthesizer`（返回 audio.url 再下载） | `qwen3-tts-flash` |

## 实测踩过的坑（都已内置处理）

1. 千问套餐 ASR 模型是 `qwen-audio-3.0-asr-flash`（3.1/paraformer 在套餐域名 404）；
   `parameters.format`/`sample_rate` 必填。
2. 千问套餐生图不能用 OpenAI 兼容 `/images/generations`（报 `url error`），走原生 MG 端点、size 用 `*`。
3. MiniMax 音频返回 hex 字符串需 `bytes.fromhex()`；无效 key 也返回 HTTP 200，必须查 `base_resp`。
4. MiniMax 套餐不含视频（2068 全家桶），所以视频链第一环就是千问套餐。
5. 小米 MiMo TTS 目标文本**必须放 assistant 消息**（放 user 是文档点名错误）；TTS 三款限时免费。
6. 本机**没有 ffmpeg**：非 mp3/WAV/Ogg 输入会给出清晰报错而不是静默失败。

## 已验证音色

MiniMax（M Plan 实测出音）：`male-qn-qingse`、`female-shaonv`、`male-qn-jingying`、`female-tianmei`、
`presenter_male`、`presenter_female`、`audiobook_male_1`、`audiobook_female_1`、
`Chinese (Mandarin)_Warm_Girl`、`Chinese (Mandarin)_Sweet_Lady`、`Chinese (Mandarin)_Gentleman`、
`Chinese (Mandarin)_Lyrical_Voice`、`Wise_Woman`、`Calm_Woman`、`English_expressive_narrator`。
小米：`mimo_default`（预置 8 个，其余名字文档未给全）。DeepSeek：`stella`/`tide`。千问：`Mia`。

## 路径可移植性

`cordis.patch.yml` 里本机相关的：`args` 中的 server 脚本路径、`cwd`、`command`（PATH 上的 `python`，
只有 `python3` 的机器改一下）。server 只用 Python 标准库，无需 `pip install`。
安装：DSH **Plugins → Add plugin** 选本目录，或 `plugin_manager install_bundle`。
`link:` 软链安装，**改 server 代码即时生效**；改 patch 行结构需 `remove_bundle` + `install_bundle` 重装。

## 已验证 / 未验证

- ✅ **已实测**（真实 key、真实网络、走 DSH 工具面）：
  - v0.6 链配置文件：Host 插件启动即写文件、MCP 每次调用重读、坏 JSON 自动回落默认（冒烟 8/8）；
  - **降级链端到端**：`synthesize` 触发 MiniMax 2056 → 自动落小米出音（`fallback_from` 完整可读）；
  - 跨厂商往返：小米合成 → 千问套餐识别，文字一字不差；
  - `generate_image`（MiniMax 套餐）真实出图；`generate_video` + `job_status`（千问套餐）真实出片落盘；
  - 六引用全部解析注入、插件行 `enabled` + `active`；`check_config` 只回布尔；协议冒烟全过。
- ❌ **未验证 / 待重启生效**：
  - **v0.6 的设置页面与 `media_bridge_settings` 工具需要重启 DSH 才加载新 JS 模块代**（文档规定：替换已安装包的模块需重启；链文件与 MCP 降级逻辑不受影响，已生效）；
  - 链上**后段**环节的实调：DeepSeek ASR/TTS 早前实测过；小米 TTS/ASR 本轮实测过；
    **MiniMax 按量兜底**（早前 key 验证过生图/视频）、**千问按量兜底**（key 有效但账户开了"仅用免费额度"，
    实际调用会 403——作为链尾兜底它会被如实报错）、**千问按量 TTS**（形状来自文档，未实调）；
  - 凭据轮换后的自动重连；ffmpeg 转码分支（本机无 ffmpeg）；`MEDIA_BRIDGE_OUT` 覆盖；插件详情页渲染。

## 已知限制（v0.9）

- 语音入口是「整段音频文件」，无 VAD/打断/全双工；要麦克风实时形态需另加 Client slot。
- 视频异步，出片几分钟，期间对话不受影响。
- stdio 子进程只拿 patch 显式声明的 env——刻意的安全设计。
- `voice` 参数跨上游时按各家默认音色降级（结果里 `voice_overridden` 会标明）。
- 上游按各家套餐/定价计费；配额耗尽会整链报错，需要升级套餐或充值。
