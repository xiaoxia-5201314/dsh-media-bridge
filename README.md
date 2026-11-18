# dsh-media-bridge · 语音媒体桥

[![CI](https://github.com/xiaoxia-5201314/dsh-media-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/xiaoxia-5201314/dsh-media-bridge/actions/workflows/ci.yml)

DeepSeek Harness（DSH）配置型插件：给智能体 8 个媒体工具——转写、语音合成、生图、生视频、造音色，
以及配置与诊断。**每次调用只打一个上游**，失败返回结构化错误码，由调用方决定重试还是换家。

> **用前必读**
> 1. 本仓库不含任何密钥，只有凭据**引用名**；值由你自己放进 DSH 凭据库。
> 2. 它会把你的文本/图片/音频发给你配置的第三方服务（MiniMax、千问、小米 MiMo、DeepSeek）。
> 3. 调用会产生**你账号下的真实费用**；生图/生视频不幂等，客户端超时也照常计费。MIT 提供，AS IS。

## 安装（不需要 npm 账号）

插件不走 npm，`package.json` 只是 DSH bundle 的清单（且 `private: true`）。装法和你机器上其他
GitHub 来源的插件一样：

1. 把仓库放到 DSH 能读到的目录：`git clone https://github.com/xiaoxia-5201314/dsh-media-bridge.git ~/dsh-media-bridge`
2. 在该 profile 的依赖里加一行 `github:xiaoxia-5201314/dsh-media-bridge`（本地开发也可用
   `link:/绝对/路径`），然后启用。插件会自动注册两行：
   - `media-bridge-mcp`：stdio 启动 `server/media_bridge.py`，暴露 8 个工具；
   - `media-bridge-settings`：设置页 + `media_bridge_settings` 工具。
3. **必改一处**：`cordis.patch.yml` 里三处路径是按机器写死的——脚本位置、`cwd`、
   `MEDIA_BRIDGE_CHAINS_FILE`。换机器直接覆盖这个文件会让 MCP 行**静默启动失败**
   （仓库里发布的是 `/PATH/TO/...` 模板，就是提醒这件事）。
4. 填凭据（值不进代码目录）：`MINIMAX_API_KEY`、`QWEN_TOKEN_PLAN_CN_API_KEY`、`XIAOMI_API_KEY`、
   `DEEPSEEK_API_KEY`、`MINIMAX_CN_API_KEY`、`QIANWEN_API_KEY`。缺哪个，链上对应环节自动跳过；
   全缺时只剩 `check_config`。套餐 key 与按量 key/域名不能混用。

## 工具

| 工具 | 作用 | 费用 |
|---|---|---|
| `check_config` | 报告每家配置状态与默认顺序，**只回布尔值，永不输出密钥** | 免费 |
| `transcribe` | 音频→文本；`response_format=verbose_json` 出说话人分离与逐句时间戳，`srt`/`vtt` 落字幕文件 | 按量 |
| `synthesize` | 文本→语音；可带 `instruction`（方言/情感/语速）、`ssml`、`volume`/`rate`、`hot_fix`、`emotion` | 按量 |
| `generate_image` | 文生图；带 `images=[1..16]` 即改走图片编辑（本地文件自动转 data URI） | **不幂等，超时也计费** |
| `generate_video` | 提交异步任务拿 `job_id` | **不幂等，超时也计费** |
| `job_status` | 查任务并把成品下载到本地 | 免费查询 |
| `list_models` | 拉厂商模型目录，看有没有新模型 | 免费 |
| `voice_manage` | `create_clone`（样本音频复刻）/ `create_design`（文字描述造音色）/ `list` / `status` / `delete` | **创建与查询免费**，用它合成按目标模型计费 |

## 设置页（设置 → 插件 → 语音媒体桥）

六张卡片：降级顺序（拖拽排序、整家停用）、每家的模型顺序（可增删、可折叠）、音色（**已汉化**，
按厂商+按模型族过滤，ID 与中文名并列）、接口密钥（直接在页面填/清除）、接口地址、**花费上限**。
保存后运行配置即时生效，下一次调用就用新的顺序/模型/音色。

## 花费上限

| 档位 | 字段 | 默认 |
|---|---|---|
| 统一日额 | `budget.daily_limit_cny` | **10** |
| 按品牌 | `budget.provider_limit_cny[供应商]` | 留空 = 不限 |
| 按模型 | `budget.model_limit_cny["供应商:模型"]` | 留空 = 不限 |

`0` = 关闭该类调用，负数（走工具）= 不限；本地日期零点自动清零。拦截发生在**发请求之前**，
被挡的调用返回 `code=budget_exhausted`、不产生费用；成功的调用回显 `cost_estimate_cny` 与
`budget_used_today_cny`。账本是与链文件同目录的 `media-bridge.usage.json`（0600），
**账本写不进去时直接拒绝调用**——宁可挡住，也不做无记账的扣费。单价是估算，
可用 `budget.prices_cny` 覆盖自己的实际报价。

## 主要限制

- 千问**套餐端不含** `qwen-audio-3.1-*`、`cosyvoice-*`、`voice-enrollment`（实测 `Model not exist`），
  这些只在按量端有；能力矩阵与原因见 `docs/NOTES.md`。
- 音色 ID 绑定模型族，跨族使用会返回 `InvalidParameter / Engine error 411`；复刻与设计音色还额外绑定
  创建时的 `target_model`。
- MiniMax 音色复刻**故意没接**：首次使用扣 ¥9.9 解锁费，而小米 `mimo-v2.5-tts-voiceclone` 免费可用。
- 表现力参数（`instruction`/`ssml`/`emotion`/`hot_fix`）已验证厂商接受且不报错，但**效果未经人工听测**。
- 单次上限各家不同（如 MiniMax 转写 500 秒 / 50 MB，DeepSeek 120 秒 / 8 MiB），超限在调用前拒绝。

## 开发

```bash
python -m unittest server.test_tts_variants server.test_budget server.test_v09_controls \
  server.test_single_target_routing server.test_minimax_video server.test_deepseek_asr \
  server.test_minimax_image server.test_list_models server.test_download_naming test_synthesize_runtime
node --test client.test.cjs host.test.mjs
```

108 Python + 26 JS，全部离线（厂商 HTTP 层被 mock），所以 CI 不需要密钥、也不会产生费用。

许可证：**MIT**（见 `LICENSE`）。踩坑与演进记录：`docs/NOTES.md`。
