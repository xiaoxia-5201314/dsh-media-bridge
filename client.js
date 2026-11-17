window.__ModuleLoader__.load({
  id: '@local/media-bridge',
  factory(require) {
    const React = require('react');
    const h = React.createElement;

    const NS = 'settings.mediaBridge';
    const ENTRY = 'media-bridge-settings';
    const CAPS = ['transcribe', 'synthesize', 'generate_image', 'generate_video'];
    const PROVIDERS = {
      'minimax-plan': { label: 'MiniMax M Plan', caps: ['transcribe', 'synthesize', 'generate_image'] },
      'minimax-payg': { label: 'MiniMax 按量', caps: ['transcribe', 'synthesize', 'generate_image', 'generate_video'] },
      'qwen-plan': { label: '千问 Token Plan', caps: ['transcribe', 'synthesize', 'generate_image', 'generate_video'] },
      'qwen-payg': { label: '千问按量', caps: ['transcribe', 'synthesize', 'generate_image', 'generate_video'] },
      'xiaomi': { label: '小米 MiMo（TTS 免费）', caps: ['transcribe', 'synthesize'] },
      'deepseek': { label: 'DeepSeek 按量', caps: ['transcribe', 'synthesize'] },
    };
    const CRED_REFS = {
      'minimax-plan': 'MINIMAX_API_KEY', 'minimax-payg': 'MINIMAX_CN_API_KEY',
      'qwen-plan': 'QWEN_TOKEN_PLAN_CN_API_KEY', 'qwen-payg': 'QIANWEN_API_KEY',
      'xiaomi': 'XIAOMI_API_KEY', 'deepseek': 'DEEPSEEK_API_KEY',
    };
    const DEFAULT_CHAINS = {
      transcribe: ['qwen-plan', 'xiaomi', 'deepseek', 'qwen-payg', 'minimax-plan', 'minimax-payg'],
      synthesize: ['minimax-plan', 'qwen-plan', 'xiaomi', 'deepseek', 'minimax-payg', 'qwen-payg'],
      generate_image: ['minimax-plan', 'qwen-plan', 'minimax-payg', 'qwen-payg'],
      generate_video: ['qwen-plan', 'minimax-payg', 'qwen-payg'],
    };
    // MiniMax 系统音色：官方「中文（普通话）」「中文（粤语）」两节，ID 与中文名逐条照抄文档。
    const MINIMAX_VOICES = [
      ['male-qn-qingse', '青涩青年音色'], ['male-qn-jingying', '精英青年音色'], ['male-qn-badao', '霸道青年音色'],
      ['male-qn-daxuesheng', '青年大学生音色'], ['female-shaonv', '少女音色'], ['female-yujie', '御姐音色'],
      ['female-chengshu', '成熟女性音色'], ['female-tianmei', '甜美女性音色'],
      ['male-qn-qingse-jingpin', '青涩青年 · beta'], ['male-qn-jingying-jingpin', '精英青年 · beta'],
      ['male-qn-badao-jingpin', '霸道青年 · beta'], ['male-qn-daxuesheng-jingpin', '青年大学生 · beta'],
      ['female-shaonv-jingpin', '少女 · beta'], ['female-yujie-jingpin', '御姐 · beta'],
      ['female-chengshu-jingpin', '成熟女性 · beta'], ['female-tianmei-jingpin', '甜美女性 · beta'],
      ['clever_boy', '聪明男童'], ['cute_boy', '可爱男童'], ['lovely_girl', '萌萌女童'], ['cartoon_pig', '卡通猪小琪'],
      ['bingjiao_didi', '病娇弟弟'], ['junlang_nanyou', '俊朗男友'], ['chunzhen_xuedi', '纯真学弟'],
      ['lengdan_xiongzhang', '冷淡学长'], ['badao_shaoye', '霸道少爷'], ['tianxin_xiaoling', '甜心小玲'],
      ['qiaopi_mengmei', '俏皮萌妹'], ['wumei_yujie', '妩媚御姐'], ['diadia_xuemei', '嗲嗲学妹'],
      ['danya_xuejie', '淡雅学姐'],
      ['Chinese (Mandarin)_Reliable_Executive', '沉稳高管'], ['Chinese (Mandarin)_News_Anchor', '新闻女声'],
      ['Chinese (Mandarin)_Mature_Woman', '傲娇御姐'], ['Chinese (Mandarin)_Unrestrained_Young_Man', '不羁青年'],
      ['Arrogant_Miss', '嚣张小姐'], ['Robot_Armor', '机械战甲'],
      ['Chinese (Mandarin)_Kind-hearted_Antie', '热心大婶'], ['Chinese (Mandarin)_HK_Flight_Attendant', '港普空姐'],
      ['Chinese (Mandarin)_Humorous_Elder', '搞笑大爷'], ['Chinese (Mandarin)_Gentleman', '温润男声'],
      ['Chinese (Mandarin)_Warm_Bestie', '温暖闺蜜'], ['Chinese (Mandarin)_Male_Announcer', '播报男声'],
      ['Chinese (Mandarin)_Sweet_Lady', '甜美女声'], ['Chinese (Mandarin)_Southern_Young_Man', '南方小哥'],
      ['Chinese (Mandarin)_Wise_Women', '阅历姐姐'], ['Chinese (Mandarin)_Gentle_Youth', '温润青年'],
      ['Chinese (Mandarin)_Warm_Girl', '温暖少女'], ['Chinese (Mandarin)_Kind-hearted_Elder', '花甲奶奶'],
      ['Chinese (Mandarin)_Cute_Spirit', '憨憨萌兽'], ['Chinese (Mandarin)_Radio_Host', '电台男主播'],
      ['Chinese (Mandarin)_Lyrical_Voice', '抒情男声'], ['Chinese (Mandarin)_Straightforward_Boy', '率真弟弟'],
      ['Chinese (Mandarin)_Sincere_Adult', '真诚青年'], ['Chinese (Mandarin)_Gentle_Senior', '温柔学姐'],
      ['Chinese (Mandarin)_Stubborn_Friend', '嘴硬竹马'], ['Chinese (Mandarin)_Crisp_Girl', '清脆少女'],
      ['Chinese (Mandarin)_Pure-hearted_Boy', '清澈邻家弟弟'], ['Chinese (Mandarin)_Soft_Girl', '柔和少女'],
      ['Cantonese_ProfessionalHost（F)', '专业女主持 · 粤语'], ['Cantonese_GentleLady', '温柔女声 · 粤语'],
      ['Cantonese_ProfessionalHost（M)', '专业男主持 · 粤语'], ['Cantonese_PlayfulMan', '活泼男声 · 粤语'],
      ['Cantonese_CuteGirl', '可爱女孩 · 粤语'], ['Cantonese_KindWoman', '善良女声 · 粤语'],
    ];
    // 小米 MiMo 内置音色（官方表）。mimo_default 在中国集群取冰糖，其他集群取 Mia。
    const XIAOMI_VOICES = [
      ['mimo_default', 'MiMo 默认'], ['冰糖', '中文女声'], ['茉莉', '中文女声'], ['苏打', '中文男声'],
      ['白桦', '中文男声'], ['Mia', '英文女声'], ['Chloe', '英文女声'], ['Milo', '英文男声'], ['Dean', '英文男声'],
    ];
    // 千问两族音色不通用（官方：混用会 InvalidParameter / Engine error 411）。
    // 这里只列已验证的族默认值，其余音色仍可手填。
    // Qwen-Audio-TTS / CosyVoice 一族（走 SpeechSynthesizer 路由）。
    // 3.0-tts-plus 的官方系统音色只有两个；longanhuan_v3.6 属于 3.0-tts-flash，
    // 但在本账号的套餐端实测可用，所以标注来源，不当成通用音色。
    const QWEN_AUDIO_TTS_VOICES = [
      // 3.1 一族（qwen-audio-3.1-tts-plus / -flash）自己的系统音色
      ['longanhuan_v3.1', '龙安欢 · 女 · 该族默认'], ['longanlingxin_v3.1', '龙安灵心 · 女 · 知心温暖'],
      ['longanfengyue_v3.1', '龙安风悦 · 女 · 自然亲切'], ['xunanchuan_v3.1', '许南川 · 男 · 磁性真诚'],
      ['yuxiaoyun_v3.1', '于小云 · 女 · 元气亲切'], ['qiaoxiaojiao_v3.1', '乔小娇 · 女 · 俏丽可爱'],
      ['xiaxiaochen_v3.1', '夏小晨 · 女 · 元气明亮'], ['anmingyuan_v3.1', '安明远 · 男 · 清亮自然'],
      ['wenhuaiqing_v3.1', '温怀清 · 女 · 清亮柔和'], ['anxiaolan_v3.1', '安小岚 · 女 · 清甜纯净'],
      ['longanlingxin', '龙安灵心 · 女 · 知心温暖（3.0-tts-plus）'],
      ['longanlufeng', '龙安鲁风 · 男 · 明亮开朗（3.0-tts-plus）'],
      ['longanhuan_v3.6', '龙安欢 · 女 · 欢脱元气（3.0-tts-flash，套餐端实测可用）'],
      ['longanfengyue', '龙安风悦 · 女 · 自然亲切（3.0-tts-flash）'],
      ['longanyuanfei', '龙安元妃 · 女 · 高傲妃子音（3.0-tts-flash）'],
      ['longanlingxi', '龙安灵希 · 女 · 可爱甜美（3.0-tts-flash）'],
      ['longanxiaoxin', '龙安小昕 · 女 · 亲切活泼（3.0-tts-flash）'],
      ['longjielidou_v3.6', '龙杰力豆 · 男童 · 天真（3.0-tts-flash）'],
      ['longpaopao_v3.6', '龙泡泡 · 女童 · 软糯（3.0-tts-flash）'],
      ['longhuohuo_v3.6', '龙火火 · 男童 · 顽皮（3.0-tts-flash）'],
      ['longchuanshu_v3.6', '龙川叔 · 男 · 川普大叔（3.0-tts-flash）'],
      ['loongmary', 'loongmary · 女 · 温暖英音（仅英文）'],
      ['loongeva_v3.6', 'loongeva · 女 · 高智美音（仅英文）'],
      ['loongjohn', 'loongJohn · 男 · 沉稳美音（仅英文）'],
    ];
    // CosyVoice 一族：与 Qwen-Audio-TTS 共用同一条合成路由，但是不同的模型。
    // 实测：按量端有 cosyvoice-v3-flash，套餐端回答 Model not exist，所以只在按量端列出。
    const COSYVOICE_VOICES = [
      ['longanyang', '龙安洋 · 男 · 阳光大男孩（cosyvoice-v3）'],
      ['longanhuan', '龙安欢 · 女 · 欢脱元气（cosyvoice-v3）'],
      ['longanhuan_v3', '龙安欢 V3 · 女 · 支持多方言（cosyvoice-v3-flash）'],
      ['longxiaochun_v3', '龙小淳 · 女 · 知性积极（cosyvoice-v3-flash）'],
      ['longshuo_v3', '龙硕 · 男 · 博才干练（cosyvoice-v3-flash）'],
      ['longshu_v3', '龙书 · 男 · 沉稳青年（cosyvoice-v3-flash）'],
    ];
    // Qwen3-TTS 一族（走 multimodal-generation 路由），系统音色与上面不通用。
    const QWEN3_TTS_VOICES = [
      ['Cherry', 'Cherry · 女 · 阳光亲切'], ['Serena', 'Serena · 女 · 温柔'],
      ['Ethan', 'Ethan · 男 · 标准普通话带北方口音'], ['Chelsie', 'Chelsie · 女 · 二次元女友'],
      ['Momo', 'Momo · 女 · 撒娇搞怪'], ['Vivian', 'Vivian · 女 · 小暴躁'],
      ['Moon', 'Moon · 男 · 率性月白'], ['Maia', 'Maia · 女 · 知性温柔'],
      ['Kai', 'Kai · 男 · 耳畔 SPA'], ['Nofish', 'Nofish · 男 · 不翘舌设计师'],
      ['Bella', 'Bella · 女 · 小萝莉'], ['Jennifer', 'Jennifer · 女 · 品牌级美语'],
      ['Ryan', 'Ryan · 男 · 戏感张力'], ['Katerina', 'Katerina · 女 · 御姐'],
      ['Aiden', 'Aiden · 男 · 美语大男孩'], ['Eldric Sage', 'Eldric Sage · 男 · 沧桑智者'],
      ['Mia', 'Mia · 女 · 温顺乖巧（本族默认，已实测）'], ['Mochi', 'Mochi · 男 · 早慧小大人'],
      ['Bellona', 'Bellona · 女 · 洪亮清晰'], ['Vincent', 'Vincent · 男 · 沙哑烟嗓'],
      ['Bunny', 'Bunny · 女 · 萌萝莉'], ['Neil', 'Neil · 男 · 新闻主播'],
      ['Elias', 'Elias · 女 · 学科讲解'], ['Arthur', 'Arthur · 男 · 村口旱烟'],
      ['Nini', 'Nini · 女 · 软糯甜腻'], ['Seren', 'Seren · 女 · 助眠舒缓'],
      ['Pip', 'Pip · 男 · 童真调皮'], ['Stella', 'Stella · 女 · 少女正义'],
      ['Bodega', 'Bodega · 男 · 西班牙大叔'], ['Sonrisa', 'Sonrisa · 女 · 拉美大姐'],
      ['Alek', 'Alek · 男 · 俄语冷冽'], ['Dolce', 'Dolce · 男 · 意大利大叔'],
      ['Sohee', 'Sohee · 女 · 韩语欧尼'], ['Ono Anna', 'Ono Anna · 女 · 日语青梅'],
      ['Lenn', 'Lenn · 男 · 德语青年'], ['Emilien', 'Emilien · 男 · 法语大哥哥'],
      ['Andre', 'Andre · 男 · 磁性自然'], ['Radio Gol', 'Radio Gol · 男 · 足球解说'],
      ['Jada', 'Jada · 女 · 上海话'], ['Dylan', 'Dylan · 男 · 北京话'],
      ['Li', 'Li · 男 · 南京话'], ['Marcus', 'Marcus · 男 · 陕西话'],
      ['Roy', 'Roy · 男 · 闽南语'], ['Peter', 'Peter · 男 · 天津话'],
      ['Sunny', 'Sunny · 女 · 四川话'], ['Eric', 'Eric · 男 · 成都话'],
      ['Rocky', 'Rocky · 男 · 粤语'], ['Kiki', 'Kiki · 女 · 粤语'],
    ];

    // 音色绑定模型族：给错族的 ID 会回 InvalidParameter / Engine error 411。
    // An empty model list means "use the provider default", not "show every family".
    const qwenModel = (provider, model) => String(model || (MODEL_DEFAULTS[provider] || {}).synthesize || '').toLowerCase();
    const qwenVoiceChoices = (provider, model) => {
      const name = qwenModel(provider, model);
      if (name.includes('cosyvoice')) return COSYVOICE_VOICES;
      if (name.includes('qwen3-tts')) return QWEN3_TTS_VOICES;
      if (provider === 'qwen-plan') return QWEN_AUDIO_TTS_VOICES;
      return QWEN_AUDIO_TTS_VOICES.concat(COSYVOICE_VOICES);
    };
    const qwenVoiceFallback = (provider, model) => {
      const name = qwenModel(provider, model);
      if (name.includes('cosyvoice')) return 'longanyang';
      if (name.includes('qwen3-tts')) return 'Mia';
      return provider === 'qwen-plan' ? 'longanlingxin' : 'longanhuan_v3.6';
    };
    const MODEL_DEFAULTS = {
      'qwen-plan': { transcribe: 'qwen-audio-3.0-asr-flash', synthesize: 'qwen-audio-3.0-tts-plus', generate_video: 'wan3.0-video', generate_image: 'qwen-image-3.0-pro' },
      'qwen-payg': { transcribe: 'qwen-audio-3.1-asr-flash', generate_video: 'wan3.0-video', generate_image: 'qwen-image-3.0-pro', synthesize: 'qwen-audio-3.1-tts-plus' },
      'minimax-plan': { transcribe: 'asr-1.0', synthesize: 'speech-2.8-hd', generate_image: 'MiniMax-H3-Image' },
      'minimax-payg': { transcribe: 'asr-1.0', synthesize: 'speech-2.8-hd', generate_image: 'MiniMax-H3-Image', generate_video: 'MiniMax-H3-Lite' },
      'xiaomi': { transcribe: 'mimo-v2.5-asr', synthesize: 'mimo-v2.5-tts' },
      'deepseek': {},
    };
    const DEFAULT_BASES = {
      'qwen-plan': 'https://token-plan.maas.qianwenaiapi.com', 'qwen-payg': 'https://maas.qianwenaiapi.com',
      'minimax-plan': 'https://api.minimax.cn', 'minimax-payg': 'https://api.minimax.cn',
      'xiaomi': 'https://api.xiaomimimo.com/v1', 'deepseek': 'https://api.deepseek.com',
    };

    const zh = {
      title: '语音媒体桥',
      description: '语音与媒体上游：供应商优先级、每家可选的模型顺序、音色与接口地址。保存后 Host 会同步运行配置，可用 media_bridge_settings 的 get 核对。',
      chains: '降级链（套餐优先，按量兜底）',
      chainHint: '按列表顺序尝试；拖拽整行或用行末小箭头排序。勾选框停用该供应商，对所有能力同时生效。',
      endpointHint: '自定义地址会收到 API 凭据：HTTPS 也要确认目标可信，HTTP 可能泄露凭据。URL 不允许用户名、密码、查询参数或片段。',
      invalidUrl: '地址必须是完整的 HTTP(S) URL，且不能含用户名、密码、查询参数或片段。',
      modelHint: '每家可配多个模型，列表首位优先；拖拽或小箭头排序，✕ 移除。',
      addModel: '添加模型', remove: '移除',
      conflict: '配置已被其他编辑器修改；请放弃草稿并重新修改。', unknown: '未知',
      models: '模型 ID',
      voices: '音色',
      endpoints: '接口地址（留空用默认）',
      credentials: '凭据状态',
      capTranscribe: '语音识别', capSynthesize: '语音合成', capGenerate_image: '文生图', capGenerate_video: '文生视频',
      save: '保存', discard: '放弃修改', saved: '已保存', saving: '保存中…',
      saveFailed: '保存失败（配置可能被更高优先级层覆盖）',
      readOnly: '当前文档只读，无法在此保存；可改 cordis.patch.yml。',
      notServed: '该配置尚未由 Host 提供（缺少 volatile 配置声明或 Host 未发布此命名空间）。',
      unset: '默认',
      configured: '已配置', missing: '未配置',
      preferred: '首选', providerOff: '已停用', unsaved: '未保存的修改', toggleGlobal: '全局启用/停用',
      modelsCount: '{n} 个', modelsDefault: '用内置默认', expandAll: '全部展开', collapseAll: '全部折叠', voiceHint: '音色只对这一家、且只对同族模型生效：千问两族音色不通用，混用会报 InvalidParameter / Engine error 411；复刻与设计音色还额外绑定创建时的 target_model。下面每条都标了所属模型，并按你配的模型自动筛选（CosyVoice 只有按量端有，套餐端实测 Model not exist）；MiniMax 和千问也接受复刻与音色设计 ID（用 voice_manage 创建，千问的复刻/设计只有按量端有），可手填。', customVoice: '自定义', voiceId: '音色 ID', voicePreset: '预设音色', voiceFemale: '女声', voiceMale: '男声',
      budget: '花费上限',
      budgetHint: '在发出请求之前拦截，被挡的调用不会到达厂商、不产生费用。留空 = 该档不限；填 0 = 关闭该类调用；负数在工具里表示不限。日额按本地日期零点清零，单价为估算，可在工具里用 budget.prices_cny 覆盖。',
      budgetDaily: '今日总额上限（元）',
      budgetUnlimited: '不限',
      budgetModelHint: '按模型上限（provider:模型）——比品牌档更细，先命中者生效。',
      moveUp: '上移', moveDown: '下移',
      credentialHint: '密钥只写入 DSH 凭据库，不进配置文件、不留存在页面状态。保存或清除后需重启 media-bridge MCP 行（或重启 DSH）才生效。',
      keyInput: '粘贴 API Key', keyReplace: '已配置，粘贴可覆盖',
      saveKey: '保存密钥', clearKey: '清除',
      keySet: '已保存（重启 MCP 行后生效）', keyCleared: '已清除（重启 MCP 行后生效）', keyFailed: '写入失败',
      keyUnavailable: '当前环境未提供凭据写入接口，请在 DSH 凭据库或 cordis.patch.yml 中配置。',
      keyBusy: '写入中…',
    };
    const en = {
      title: 'Voice and media bridge',
      description: 'Upstream order, per-provider model priority, voices and endpoints. Saving syncs the runtime file; verify with media_bridge_settings get.',
      chains: 'Fallback chains (plans first, pay-as-you-go after)',
      chainHint: 'Tried in list order. Drag a row or use its small arrows; the checkbox disables a provider for every capability at once.',
      endpointHint: 'Custom endpoints receive API credentials: verify the destination even over HTTPS, and treat HTTP as leaking. URL userinfo, query and fragment are prohibited.',
      invalidUrl: 'Use an absolute HTTP(S) URL without userinfo, query or fragment.',
      modelHint: 'Each provider can list several models; the first is preferred. Drag or use arrows to reorder, the cross to remove.',
      addModel: 'Add model', remove: 'Remove',
      conflict: 'Configuration changed in another editor; discard this draft and edit again.', unknown: 'unknown',
      models: 'Model ids',
      voices: 'Voices',
      endpoints: 'Endpoints (blank = default)',
      credentials: 'Credential status',
      capTranscribe: 'Transcription', capSynthesize: 'Speech synthesis', capGenerate_image: 'Image generation', capGenerate_video: 'Video generation',
      save: 'Save', discard: 'Discard', saved: 'Saved', saving: 'Saving…',
      saveFailed: 'Save failed (a higher-priority layer may override this config)',
      readOnly: 'This document is read-only; edit cordis.patch.yml instead.',
      notServed: 'The Host has not published this namespace yet.',
      unset: 'default',
      configured: 'configured', missing: 'not configured',
      preferred: 'First', providerOff: 'Off', unsaved: 'Unsaved changes', toggleGlobal: 'enable or disable globally',
      modelsCount: '{n} models', modelsDefault: 'built-in default', expandAll: 'Expand all', collapseAll: 'Collapse all', voiceHint: 'A voice applies to one provider and one model family: the two Qwen families do not share voice ids, and mixing them answers InvalidParameter / Engine error 411; cloned and designed voices are also bound to the target_model they were created with. Every entry names its model, and the list follows the model you configured (CosyVoice exists only on the pay-as-you-go endpoint; the token-plan endpoint answers Model not exist); voice_manage creates cloned and designed voices; on Qwen that endpoint exists only on the pay-as-you-go tier.', customVoice: 'custom', voiceId: 'voice id', voicePreset: 'preset voice', voiceFemale: 'female', voiceMale: 'male',
      budget: 'Spend caps',
      budgetHint: 'Applied before dispatch: a capped call never reaches the vendor and costs nothing. Blank = no cap for that tier, 0 = that class is closed (negative means unlimited via the tool). The daily total resets at local midnight; prices are estimates and can be overridden through budget.prices_cny.',
      budgetDaily: 'Daily total cap (CNY)',
      budgetUnlimited: 'unlimited',
      budgetModelHint: 'Per-model caps (provider:model) - finer than the provider tier; whichever limit is hit first applies.',
      moveUp: 'Move up', moveDown: 'Move down',
      credentialHint: 'Keys go only to the DSH credential store, never into this config or page state. Restart the media-bridge MCP row (or DSH) after saving or clearing.',
      keyInput: 'Paste API key', keyReplace: 'Configured; paste to replace',
      saveKey: 'Save key', clearKey: 'Clear',
      keySet: 'Saved (restart the MCP row to apply)', keyCleared: 'Cleared (restart the MCP row to apply)', keyFailed: 'Write failed',
      keyUnavailable: 'This environment does not expose a credential writer; use the DSH credential store or cordis.patch.yml.',
      keyBusy: 'Writing…',
    };

    // ---- controller: draft over the settings form + credential badges ----
    function makeController(form, ctx) {
      const listeners = new Set();
      let snapshot = form.getSnapshot();
      let draft = null;
      let cred = null;
      const credBusy = {};
      const credMsg = {};
      // Which provider groups are open. Deliberately outside the draft: it is a
      // view preference, so expanding a row must not mark the page dirty.
      let expanded = {};      let status = '';
      let saving = false;
      let base = null;
      let revision;
      let generation = 0;
      const clone = value => JSON.parse(JSON.stringify(value));
      const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
      const BUDGET_DEFAULT = { daily_limit_cny: 10, provider_limit_cny: {}, model_limit_cny: {}, prices_cny: {} };
      function budgetTable(value) {
        const out = {};
        for (const [name, raw] of Object.entries(object(value))) {
          const n = Number(raw);
          if (Number.isFinite(n)) out[name] = n;
        }
        return out;
      }
      function normalizeBudget(value) {
        const raw = object(value);
        const daily = Number(raw.daily_limit_cny);
        return {
          daily_limit_cny: Number.isFinite(daily) ? daily : BUDGET_DEFAULT.daily_limit_cny,
          provider_limit_cny: budgetTable(raw.provider_limit_cny),
          model_limit_cny: budgetTable(raw.model_limit_cny),
          prices_cny: budgetTable(raw.prices_cny),
        };
      }
      function normalize(value) {
        const source = object(value);
        const rawChains = object(source.chains);
        const chains = {};
        for (const cap of CAPS) {
          const order = Array.isArray(rawChains[cap]) ? rawChains[cap].filter(p => Object.hasOwn(PROVIDERS, p)) : [];
          for (const p of DEFAULT_CHAINS[cap]) if (!order.includes(p)) order.push(p);
          chains[cap] = order;
        }
        return { chains, models: clone(object(source.models)), voices: clone(object(source.voices)),
          baseUrls: clone(object(source.baseUrls)), disabled: Array.isArray(source.disabled) ? clone(source.disabled) : [],
          budget: normalizeBudget(source.budget) };
      }
      function modelList(provider, cap) {
        const raw = current().models?.[provider]?.[cap];
        if (Array.isArray(raw)) return raw.filter(x => typeof x === 'string' && x.trim());
        return typeof raw === 'string' && raw.trim() ? [raw] : [];
      }
      function writeModels(provider, cap, list) {
        const d = edit();
        if (!d) return;
        d.models[provider] = d.models[provider] ?? {};
        const cleaned = list.filter(x => typeof x === 'string' && x.trim());
        if (cleaned.length) d.models[provider][cap] = cleaned;
        else delete d.models[provider][cap];
        if (!Object.keys(d.models[provider]).length) delete d.models[provider];
        status = ''; publish();
      }
      function equal(a, b) {
        if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => equal(v, b[i]));
        if (a && b && typeof a === 'object' && typeof b === 'object') {
          const keys = Object.keys(a).sort();
          return keys.length === Object.keys(b).length && keys.every(k => Object.hasOwn(b, k) && equal(a[k], b[k]));
        }
        return a === b;
      }
      function validUrls(value) {
        return Object.values(value.baseUrls).every(text => {
          if (!text) return true;
          try { const u = new URL(text); return /^https?:$/.test(u.protocol) && !u.username && !u.password && !u.search && !u.hash && !/[?#]/.test(text); }
          catch { return false; }
        });
      }
      const off = form.subscribe(() => {
        const pristine = draft !== null && !dirty();
        snapshot = form.getSnapshot();
        if (pristine && !saving) { draft = null; base = null; revision = undefined; }
        publish();
      });
      function publish() { for (const l of listeners) l(); }
      function current() { return draft ?? normalize(snapshot.value); }
      function edit() {
        if (snapshot.status !== 'ready' || saving) return null;
        if (draft !== null && !dirty()) { draft = null; base = null; revision = undefined; }
        if (draft === null) { base = normalize(snapshot.value); draft = clone(base); revision = snapshot.revision; }
        generation++;
        return draft;
      }
      function dirty() { return draft !== null && !equal(draft, base); }
      const api = {
        subscribe(l) { listeners.add(l); return () => { listeners.delete(l); }; },
        get snapshot() { return snapshot; },
        get draft() { return current(); },
        dirty, saving: () => saving, status: () => status, cred: () => cred,
        move(cap, provider, dir) {
          const d = edit();
          if (!d) return;
          const order = d.chains[cap].slice();
          const i = order.indexOf(provider);
          const j = i + dir;
          if (i < 0 || j < 0 || j >= order.length) return;
          order.splice(j, 0, order.splice(i, 1)[0]);
          d.chains[cap] = order;
          status = ''; publish();
        },
        toggle(cap, provider) {
          // disabled list is global per provider, not per chain
          const d = edit();
          if (!d) return;
          const i = d.disabled.indexOf(provider);
          if (i >= 0) d.disabled.splice(i, 1); else d.disabled.push(provider);
          status = ''; publish();
        },
        isDisabled(provider) { return current().disabled.includes(provider); },
        reorderChain(cap, from, to) {
          const d = edit();
          if (!d) return;
          const list = (d.chains[cap] ?? []).slice();
          if (!Number.isInteger(from) || !Number.isInteger(to) || from < 0 || to < 0 || from >= list.length || to >= list.length || from === to) return;
          list.splice(to, 0, list.splice(from, 1)[0]);
          d.chains[cap] = list;
          status = ''; publish();
        },
        models(provider, cap) { return modelList(provider, cap); },
        setModels(provider, cap, list) { writeModels(provider, cap, Array.isArray(list) ? list : []); },
        addModel(provider, cap, text) {
          const value = String(text ?? '').trim();
          if (!value || PROVIDERS[provider]?.caps.includes(cap) !== true) return;
          writeModels(provider, cap, [...modelList(provider, cap), value]);
        },
        removeModel(provider, cap, index) {
          const list = modelList(provider, cap);
          if (!Number.isInteger(index) || index < 0 || index >= list.length) return;
          list.splice(index, 1);
          writeModels(provider, cap, list);
        },
        moveModel(provider, cap, index, dir) {
          const list = modelList(provider, cap);
          const j = index + dir;
          if (!Number.isInteger(index) || index < 0 || j < 0 || j >= list.length) return;
          list.splice(j, 0, list.splice(index, 1)[0]);
          writeModels(provider, cap, list);
        },
        drag: { kind: null, cap: null, provider: null, index: null },
        beginDrag(kind, cap, provider, index) { this.drag = { kind, cap, provider, index }; },
        dropAt(kind, cap, provider, index) {
          const src = this.drag;
          this.drag = { kind: null, cap: null, provider: null, index: null };
          if (!src || src.kind !== kind || src.cap !== cap || !Number.isInteger(index)) return;
          if (kind === 'chain') this.reorderChain(cap, src.index, index);
          if (kind === 'model' && src.provider === provider) this.moveModelTo(provider, cap, src.index, index);
        },
        moveModelTo(provider, cap, from, to) {
          const d = edit();
          if (!d) return;
          const raw = d.models?.[provider]?.[cap];
          const source = Array.isArray(raw) ? raw.slice() : (typeof raw === 'string' && raw ? [raw] : []);
          if (!Number.isInteger(from) || !Number.isInteger(to) || from < 0 || to < 0 || from >= source.length || to >= source.length || from === to) return;
          source.splice(to, 0, source.splice(from, 1)[0]);
          writeModels(provider, cap, source);
        },
        setModel(provider, cap, text) {
          const d = edit();
          if (!d) return;
          d.models[provider] = d.models[provider] ?? {};
          if (text === '') { delete d.models[provider][cap]; if (!Object.keys(d.models[provider]).length) delete d.models[provider]; } else d.models[provider][cap] = text;
          status = ''; publish();
        },
        setVoice(provider, text) {
          const d = edit();
          if (!d) return;
          if (text === '') delete d.voices[provider]; else d.voices[provider] = text;
          status = ''; publish();
        },
        setBudgetDaily(text) {
          const d = edit();
          if (!d) return;
          const n = Number(text);
          if (text !== '' && Number.isFinite(n)) { d.budget.daily_limit_cny = n; status = ''; publish(); }
        },
        setBudgetLimit(field, name, text) {
          const d = edit();
          if (!d) return;
          const table = d.budget[field] || (d.budget[field] = {});
          if (text === '') delete table[name];
          else { const n = Number(text); if (Number.isFinite(n)) table[name] = n; }
          status = ''; publish();
        },
        setBaseUrl(provider, text) {
          const d = edit();
          if (!d) return;
          if (text === '') delete d.baseUrls[provider]; else d.baseUrls[provider] = text;
          status = ''; publish();
        },
        async save() {
          if (!dirty() || saving || !snapshot.writable || snapshot.status !== 'ready') return;
          if (!validUrls(draft)) { status = 'invalidUrl'; publish(); return; }
          if (revision === undefined || snapshot.revision !== revision) { status = 'conflict'; publish(); return; }
          const ops = ['chains', 'models', 'voices', 'baseUrls', 'disabled', 'budget']
            .filter(field => !equal(draft[field], base[field]))
            .map(field => ({ op: 'set', path: [field], value: clone(draft[field]) }));
          const submittedGeneration = generation;
          saving = true; status = ''; publish();
          try {
            const ok = await form.mutate(ops, revision);
            status = ok ? 'saved' : 'failed';
            if (ok && generation === submittedGeneration) { draft = null; base = null; revision = undefined; }
          } catch { status = 'failed'; }
          saving = false;
          snapshot = form.getSnapshot();
          publish();
        },
        discard() { if (saving) return; draft = null; base = null; revision = undefined; generation++; status = ''; publish(); },
        refreshCred() {
          const remote = ctx.remote;
          if (!remote || !remote.credentials || typeof remote.credentials.describe !== 'function') return;
          void remote.credentials.describe(Object.values(CRED_REFS)).then(response => {
            cred = response && response.ok ? response.value : null; publish();
          }).catch(() => { cred = null; publish(); });
        },
        credBusy: () => credBusy, credStatus: () => credMsg,
        isExpanded(provider) { return expanded[provider] === true; },
        allExpanded() { return Object.keys(PROVIDERS).every(p => expanded[p] === true); },
        toggleProvider(provider) { expanded = { ...expanded, [provider]: !expanded[provider] }; publish(); },
        expandAll(open) { const next = {}; if (open) for (const p of Object.keys(PROVIDERS)) next[p] = true; expanded = next; publish(); },
        keyWritable() { const r = ctx.remote; return !!(r && r.credentials && typeof r.credentials.set === 'function' && typeof r.credentials.unset === 'function'); },
        async setCredential(ref, value) {
          const text = String(value ?? '');
          if (!this.keyWritable() || !text.trim()) return;
          const r = ctx.remote;
          credBusy[ref] = true; delete credMsg[ref]; publish();
          try { const res = await r.credentials.set(ref, text.trim()); credMsg[ref] = res && res.ok === false ? 'keyFailed' : 'keySet'; }
          catch { credMsg[ref] = 'keyFailed'; }
          credBusy[ref] = false; publish(); this.refreshCred();
        },
        async clearCredential(ref) {
          if (!this.keyWritable()) return;
          const r = ctx.remote;
          credBusy[ref] = true; delete credMsg[ref]; publish();
          try { const res = await r.credentials.unset(ref); credMsg[ref] = res && res.ok === false ? 'keyFailed' : 'keyCleared'; }
          catch { credMsg[ref] = 'keyFailed'; }
          credBusy[ref] = false; publish(); this.refreshCred();
        },
        dispose() { off(); listeners.clear(); },
      };
      return api;
    }

    // ---- view ----
    // One scoped class block instead of scattered inline defaults: the page then
    // inherits the host theme (surfaces, borders, focus ring, state colours) and
    // keeps a consistent spacing rhythm. The <style> element is part of the
    // rendered tree, so unmounting the section removes it again.
    const STYLE = `
      .mb-root{max-width:780px;font-size:13px;line-height:1.45;color:var(--dsw-alias-label-primary,inherit)}
      .mb-lede{margin:0 0 14px;font-size:12.5px;opacity:.7}
      .mb-card{margin:0 0 12px;padding:13px 15px 15px;border:1px solid var(--dsw-alias-border-l1,rgba(128,128,128,.26));border-radius:11px;background:var(--dsw-alias-bg-layer-1,transparent)}
      .mb-card>h3{margin:0;font-size:13px;font-weight:600}
      .mb-note{margin:3px 0 11px;font-size:11.5px;line-height:1.5;opacity:.6}
      .mb-grid{display:grid;gap:2px 18px;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr))}
      .mb-row{display:flex;align-items:center;gap:8px;min-height:28px}
      .mb-item{display:flex;align-items:center;gap:8px;padding:2px 4px 2px 2px;border-radius:8px;cursor:grab;user-select:none;transition:background .12s ease}
      .mb-item:hover{background:var(--dsw-alias-bg-layer-2,rgba(128,128,128,.07))}
      .mb-item.is-off{opacity:.42}
      .mb-rank{flex:0 0 18px;height:18px;display:inline-flex;align-items:center;justify-content:center;border-radius:50%;font-size:10.5px;font-variant-numeric:tabular-nums;background:var(--dsw-alias-bg-layer-2,rgba(128,128,128,.13));opacity:.85}
      .mb-item.is-first .mb-rank{border:1px solid var(--dsw-alias-brand-primary,currentColor);color:var(--dsw-alias-brand-primary,inherit);opacity:1}
      .mb-name{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .mb-tag{flex:0 0 auto;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10.5px;padding:1px 7px;border:1px solid var(--dsw-alias-border-l1,rgba(128,128,128,.26));border-radius:999px;opacity:.62}
      .mb-dot{flex:0 0 auto;font-size:9px;line-height:1}
      .mb-dot.ok{color:var(--dsw-alias-state-success-primary,#2e7d32)}
      .mb-dot.no{color:var(--dsw-alias-state-error-primary,#c62828)}
      .mb-dot.un{color:var(--dsw-alias-state-idle-primary,#9aa0a6)}
      .mb-step{flex:0 0 auto;display:inline-flex;border:1px solid var(--dsw-alias-border-l1,rgba(128,128,128,.26));border-radius:6px;overflow:hidden;opacity:.4;transition:opacity .12s ease}
      .mb-item:hover .mb-step,.mb-step:focus-within{opacity:1}
      .mb-step button{border:0;background:transparent;color:inherit;font:inherit;font-size:8px;line-height:1;padding:4px 5px;cursor:pointer}
      .mb-step button+button{border-left:1px solid var(--dsw-alias-border-l1,rgba(128,128,128,.26))}
      .mb-step button:disabled{cursor:default;opacity:.3}
      .mb-input,.mb-select{font:inherit;font-size:12px;padding:3px 8px;min-width:0;border:1px solid var(--dsw-alias-border-l1,rgba(128,128,128,.26));border-radius:7px;background:var(--dsw-alias-bg-base,transparent);color:inherit}
      .mb-input{flex:1 1 140px}
      .mb-input:focus-visible,.mb-select:focus-visible,.mb-btn:focus-visible{outline:2px solid var(--dsw-alias-brand-primary,rgba(128,128,128,.45));outline-offset:1px}
      .mb-input::placeholder{opacity:.45}
      .mb-input:disabled,.mb-select:disabled{opacity:.5}
      .mb-btn{font:inherit;font-size:12px;padding:3px 10px;border-radius:7px;border:1px solid var(--dsw-alias-border-l2,rgba(128,128,128,.3));background:var(--dsw-alias-bg-layer-2,transparent);color:inherit;cursor:pointer;white-space:nowrap}
      .mb-btn:hover:not(:disabled){border-color:var(--dsw-alias-brand-primary,currentColor)}
      .mb-btn:disabled{opacity:.45;cursor:default}
      .mb-btn.is-quiet{border-color:transparent;background:transparent;opacity:.7;padding-left:6px;padding-right:6px}
      .mb-btn.is-danger:hover:not(:disabled){color:var(--dsw-alias-state-error-primary,#c62828);opacity:1}
      .mb-btn.is-primary{border-color:var(--dsw-alias-brand-primary,currentColor);color:var(--dsw-alias-brand-primary,inherit);font-weight:600}
      .mb-group{margin-top:11px}
      .mb-group:first-of-type{margin-top:0}
      .mb-group>b{display:block;margin:0 0 3px;font-size:12px;font-weight:600;opacity:.75}
      .mb-group .mb-group{margin-top:8px}
      .mb-group .mb-group>b{font-size:11.5px;font-weight:500;opacity:.6}
      .mb-label{flex:0 0 150px;opacity:.8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .mb-voice{flex:0 1 180px}
      .mb-prov{border-top:1px solid var(--dsw-alias-border-l1,rgba(128,128,128,.18))}
      .mb-prov:first-of-type{border-top:0}
      .mb-prov-head{display:flex;align-items:center;gap:8px;width:100%;padding:7px 4px;border:0;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer;border-radius:8px}
      .mb-prov-head:hover{background:var(--dsw-alias-bg-layer-2,rgba(128,128,128,.07))}
      .mb-prov-head:focus-visible{outline:2px solid var(--dsw-alias-brand-primary,rgba(128,128,128,.45));outline-offset:1px}
      .mb-caret{flex:0 0 auto;width:10px;font-size:9px;opacity:.6}
      .mb-prov-body{padding:0 4px 10px}
      .mb-cardtools{display:flex;justify-content:flex-end;margin:-4px 0 4px}
      .mb-handle{flex:0 0 auto;width:10px;opacity:0;font-size:11px;line-height:1;transition:opacity .12s ease}
      .mb-item:hover .mb-handle,.mb-item:focus-within .mb-handle{opacity:.35}
      .mb-check{flex:0 0 auto;accent-color:var(--dsw-alias-brand-primary,currentColor);cursor:pointer}
      .mb-foot{position:sticky;bottom:0;display:flex;align-items:center;gap:8px;padding:11px 0 1px;border-top:1px solid var(--dsw-alias-border-l1,rgba(128,128,128,.22));background:var(--dsw-alias-bg-base,transparent)}
      .mb-msg{font-size:11.5px;opacity:.72}
      .mb-msg.bad{color:var(--dsw-alias-state-error-primary,#c62828);opacity:1}
      .mb-msg.good{color:var(--dsw-alias-state-success-primary,#2e7d32);opacity:1}
    `;

    const cls = (...parts) => parts.filter(Boolean).join(' ');
    const capLabel = t => t[0].toUpperCase() + t.slice(1);

    function Section(props) {
      const ctrl = props.mediaBridge;
      const t = props.t;
      const [, force] = React.useReducer(x => x + 1, 0);
      React.useEffect(() => ctrl.subscribe(force), []);
      const snap = ctrl.snapshot;
      if (props.view === 'summary') return t('description');
      if (snap.status === 'loading') return h('p', { style: { opacity: 0.6 } }, '…');
      if (snap.status === 'unavailable') return h('p', { style: { opacity: 0.6 } }, t('notServed'));
      const draft = ctrl.draft;

      const dragProps = (kind, cap, provider, index) => ({
        draggable: true,
        onDragStart: e => { ctrl.beginDrag(kind, cap, provider, index); if (e && e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; },
        onDragOver: e => { if (e && e.preventDefault) e.preventDefault(); },
        onDrop: e => { if (e && e.preventDefault) e.preventDefault(); ctrl.dropAt(kind, cap, provider, index); },
      });

      // A single two-way control: it reads as one unit, appears only while the
      // row is hovered or focused, and keeps keyboard access.
      const stepper = (up, down, atTop, atBottom) => h('span', { className: 'mb-step' },
        h('button', { disabled: atTop, title: t('moveUp'), 'aria-label': t('moveUp'), onClick: e => { e.stopPropagation(); up(); } }, '▴'),
        h('button', { disabled: atBottom, title: t('moveDown'), 'aria-label': t('moveDown'), onClick: e => { e.stopPropagation(); down(); } }, '▾'));

      const chainOrder = cap => { const list = draft.chains[cap] ?? []; return list.length ? list : DEFAULT_CHAINS[cap]; };

      const credRow = p => {
        const ref = CRED_REFS[p];
        const view = ctrl.cred() && ctrl.cred()[ref];
        const ok = !!(view && view.configured);
        const busy = !!ctrl.credBusy()[ref];
        const msg = ctrl.credStatus()[ref];
        const editable = ctrl.keyWritable();
        return h('div', { key: p, className: 'mb-row' },
          h('span', { className: cls('mb-dot', view ? (ok ? 'ok' : 'no') : 'un'), title: view ? (ok ? t('configured') : t('missing')) : t('unknown') }, ok ? '●' : view ? '○' : '◌'),
          h('span', { className: 'mb-name' }, PROVIDERS[p].label),
          h('span', { className: 'mb-tag' }, ref),
          // The value is read from the DOM at submit and never kept in state, so
          // it cannot leak into the config draft or a re-render.
          h('input', { className: 'mb-input', type: 'password', autoComplete: 'off', spellCheck: false, 'aria-label': PROVIDERS[p].label + ' API Key', disabled: !editable || busy, placeholder: ok ? t('keyReplace') : t('keyInput'), 'data-cred-ref': ref }),
          h('button', { className: 'mb-btn', disabled: !editable || busy, onClick: e => { const box = e.target.previousSibling; const text = box && box.value ? box.value : ''; if (box) box.value = ''; if (text.trim()) void ctrl.setCredential(ref, text); } }, busy ? t('keyBusy') : t('saveKey')),
          ok ? h('button', { className: 'mb-btn is-quiet is-danger', disabled: !editable || busy, 'aria-label': PROVIDERS[p].label + ' · ' + t('clearKey'), onClick: () => void ctrl.clearCredential(ref) }, t('clearKey')) : null,
          msg ? h('span', { className: cls('mb-msg', msg === 'keyFailed' ? 'bad' : 'good') }, t(msg)) : null);
      };

      const modelRow = (p, cap, name, i, total) => h('div', { key: name + ':' + i, className: cls('mb-item', i === 0 && 'is-first'), ...dragProps('model', cap, p, i) },
        h('span', { className: 'mb-handle', 'aria-hidden': true }, '⠿'),
        h('span', { className: 'mb-rank' }, String(i + 1)),
        h('span', { className: 'mb-name' }, name),
        i === 0 ? h('span', { className: 'mb-tag' }, t('preferred')) : null,
        stepper(() => ctrl.moveModel(p, cap, i, -1), () => ctrl.moveModel(p, cap, i, 1), i === 0, i === total - 1),
        h('button', { className: 'mb-btn is-quiet is-danger', title: t('remove'), 'aria-label': t('remove'), onClick: e => { e.stopPropagation(); ctrl.removeModel(p, cap, i); } }, '✕'));

      const addModelRow = (p, cap) => h('div', { className: 'mb-row' },
        h('input', {
          className: 'mb-input',
          'aria-label': PROVIDERS[p].label + ' · ' + t('cap' + capLabel(cap)),
          placeholder: (MODEL_DEFAULTS[p] && MODEL_DEFAULTS[p][cap]) || '—',
          'data-add-model': p + '|' + cap,
          onKeyDown: e => { if (e.key === 'Enter' && e.target.value.trim()) { ctrl.addModel(p, cap, e.target.value); e.target.value = ''; } },
        }),
        h('button', { className: 'mb-btn', onClick: e => { const box = e.target.previousSibling; if (box && box.value && box.value.trim()) { ctrl.addModel(p, cap, box.value); box.value = ''; } } }, t('addModel')));

      const voiceSelect = (provider, options, preferName) => h('select', {
        className: 'mb-select', 'aria-label': PROVIDERS[provider].label + ' · ' + t('voices'),
        value: draft.voices[provider] || '', onChange: ev => ctrl.setVoice(provider, ev.target.value),
      },
        h('option', { value: '' }, t('unset')),
        draft.voices[provider] && !options.some(v => v[0] === draft.voices[provider])
          ? h('option', { value: draft.voices[provider] }, draft.voices[provider] + ' · ' + t('customVoice')) : null,
        options.map(([id, name]) => h('option', { key: id, value: id }, preferName ? name : id + ' · ' + name)));

      // Qwen 音色绑定模型族：给下拉，也留手填框
      const voiceCombo = (provider, options, fallback) => h('span', { style: { display: 'inline-flex', gap: 6, flex: '1 1 220px' } },
        h('select', { className: 'mb-select', 'aria-label': PROVIDERS[provider].label + ' · ' + t('voicePreset'),
          value: options.some(([id]) => id === draft.voices[provider]) ? draft.voices[provider] : '',
          onChange: ev => { if (ev.target.value) ctrl.setVoice(provider, ev.target.value); } },
          h('option', { value: '' }, draft.voices[provider] ? t('customVoice') : t('unset')),
          options.map(([id, name]) => h('option', { key: id, value: id }, id + ' · ' + name))),
        h('input', { className: 'mb-input', 'aria-label': PROVIDERS[provider].label + ' · ' + t('voiceId'),
          placeholder: fallback, value: draft.voices[provider] || '', onChange: ev => ctrl.setVoice(provider, ev.target.value) }));

      const voiceInput = (provider, placeholder) => h('input', { className: 'mb-input mb-voice', 'aria-label': PROVIDERS[provider].label + ' · ' + t('voices'), placeholder, value: draft.voices[provider] || '', onChange: e => ctrl.setVoice(provider, e.target.value) });

      const voiceRow = (provider, control) => h('div', { key: provider, className: 'mb-row' },
        h('span', { className: 'mb-label' }, PROVIDERS[provider].label),
        control);

      return h('div', { className: 'mb-root' },
        h('style', { dangerouslySetInnerHTML: { __html: STYLE } }),
        h('p', { className: 'mb-lede' }, t('description')),

        h('section', { className: 'mb-card' },
          h('h3', null, t('credentials')),
          h('p', { className: 'mb-note' }, ctrl.keyWritable() ? t('credentialHint') : t('keyUnavailable')),
          Object.keys(PROVIDERS).map(credRow)),

        h('section', { className: 'mb-card' },
          h('h3', null, t('chains')),
          h('p', { className: 'mb-note' }, t('chainHint')),
          CAPS.map(cap => h('div', { className: 'mb-group', key: cap },
            h('b', null, t('cap' + capLabel(cap))),
            chainOrder(cap).map((p, i) => {
              const off = ctrl.isDisabled(p);
              return h('div', { key: cap + p, className: cls('mb-item', off && 'is-off', i === 0 && !off && 'is-first'), ...dragProps('chain', cap, p, i) },
                h('span', { className: 'mb-handle', 'aria-hidden': true }, '⠿'),
                h('input', { className: 'mb-check', type: 'checkbox', checked: !off, title: t('toggleGlobal'), 'aria-label': PROVIDERS[p].label + ' · ' + t('toggleGlobal'), onChange: () => ctrl.toggle(cap, p) }),
                h('span', { className: 'mb-rank' }, String(i + 1)),
                h('span', { className: 'mb-name' }, PROVIDERS[p].label),
                off ? h('span', { className: 'mb-tag' }, t('providerOff')) : i === 0 ? h('span', { className: 'mb-tag' }, t('preferred')) : null,
                stepper(() => ctrl.move(cap, p, -1), () => ctrl.move(cap, p, 1), i === 0, i === chainOrder(cap).length - 1));
            })))),

        h('section', { className: 'mb-card' },
          h('h3', null, t('models')),
          h('p', { className: 'mb-note' }, t('modelHint')),
          h('div', { className: 'mb-cardtools' },
            h('button', { className: 'mb-btn is-quiet', onClick: () => ctrl.expandAll(!ctrl.allExpanded()) }, ctrl.allExpanded() ? t('collapseAll') : t('expandAll'))),
          Object.keys(PROVIDERS).map(p => {
            const open = ctrl.isExpanded(p);
            const counts = PROVIDERS[p].caps.reduce((sum, cap) => sum + ctrl.models(p, cap).length, 0);
            const first = ctrl.models(p, PROVIDERS[p].caps[0])[0] || PROVIDERS[p].caps.map(cap => ctrl.models(p, cap)[0]).find(Boolean);
            const summary = counts === 0 ? t('modelsDefault')
              : t('modelsCount').replace('{n}', String(counts)) + (first ? ' · ' + first : '');
            return h('div', { key: p, className: 'mb-prov' },
              h('button', { className: 'mb-prov-head', onClick: () => ctrl.toggleProvider(p), 'aria-expanded': open },
                h('span', { className: 'mb-caret', 'aria-hidden': true }, open ? '▾' : '▸'),
                h('span', { className: 'mb-name' }, PROVIDERS[p].label),
                h('span', { className: 'mb-tag' }, summary)),
              open ? h('div', { className: 'mb-prov-body' },
                PROVIDERS[p].caps.map(cap => {
                  const list = ctrl.models(p, cap);
                  return h('div', { className: 'mb-group', key: cap },
                    h('b', null, t('cap' + capLabel(cap))),
                    list.map((model, i) => modelRow(p, cap, model, i, list.length)),
                    addModelRow(p, cap));
                })) : null);
          })),

        h('section', { className: 'mb-card' },
          h('h3', null, t('voices')),
          h('p', { className: 'mb-note' }, t('voiceHint')),
          h('div', { className: 'mb-grid' },
            voiceRow('minimax-plan', voiceSelect('minimax-plan', MINIMAX_VOICES, true)),
            voiceRow('minimax-payg', voiceSelect('minimax-payg', MINIMAX_VOICES, true)),
            voiceRow('xiaomi', voiceSelect('xiaomi', XIAOMI_VOICES, false)),
            voiceRow('deepseek', voiceSelect('deepseek', [['stella', t('voiceFemale')], ['tide', t('voiceMale')]], true)),
            voiceRow('qwen-plan', voiceCombo('qwen-plan', qwenVoiceChoices('qwen-plan', ctrl.models('qwen-plan', 'synthesize')[0]), qwenVoiceFallback('qwen-plan', ctrl.models('qwen-plan', 'synthesize')[0]))),
            voiceRow('qwen-payg', voiceCombo('qwen-payg', qwenVoiceChoices('qwen-payg', ctrl.models('qwen-payg', 'synthesize')[0]), qwenVoiceFallback('qwen-payg', ctrl.models('qwen-payg', 'synthesize')[0]))))),

        h('section', { className: 'mb-card' },
          h('h3', null, t('endpoints')),
          h('p', { className: 'mb-note' }, t('endpointHint')),
          h('div', { className: 'mb-grid' }, Object.keys(PROVIDERS).map(p => h('div', { key: p, className: 'mb-row' },
            h('span', { className: 'mb-label' }, PROVIDERS[p].label),
            h('input', { className: 'mb-input', 'aria-label': PROVIDERS[p].label + ' · ' + t('endpoints'), placeholder: DEFAULT_BASES[p], value: draft.baseUrls[p] || '', onChange: e => ctrl.setBaseUrl(p, e.target.value) }))))),

        h('section', { className: 'mb-card' },
          h('h3', null, t('budget')),
          h('p', { className: 'mb-note' }, t('budgetHint')),
          h('div', { className: 'mb-row' },
            h('span', { className: 'mb-label' }, t('budgetDaily')),
            h('input', { className: 'mb-input', type: 'number', min: '0', step: '0.5', 'aria-label': t('budgetDaily'),
              value: String(draft.budget.daily_limit_cny), onChange: e => ctrl.setBudgetDaily(e.target.value) })),
          h('div', { className: 'mb-grid' }, Object.keys(PROVIDERS).map(p => h('div', { key: p, className: 'mb-row' },
            h('span', { className: 'mb-label' }, PROVIDERS[p].label),
            h('input', { className: 'mb-input', type: 'number', min: '0', step: '0.5', placeholder: t('budgetUnlimited'),
              'aria-label': PROVIDERS[p].label + ' · ' + t('budget'),
              value: draft.budget.provider_limit_cny[p] ?? '',
              onChange: e => ctrl.setBudgetLimit('provider_limit_cny', p, e.target.value) })))),
          h('p', { className: 'mb-note' }, t('budgetModelHint')),
          h('div', { className: 'mb-grid' }, (() => {
            const rows = [];
            for (const p of Object.keys(PROVIDERS)) {
              for (const cap of CAPS) {
                const configured = ctrl.models(p, cap);
                const seed = configured.length ? configured : [(MODEL_DEFAULTS[p] || {})[cap]].filter(Boolean);
                for (const model of seed) {
                  const key = p + ':' + model;
                  rows.push(h('div', { key, className: 'mb-row' },
                    h('span', { className: 'mb-label' }, PROVIDERS[p].label + ' · ' + model),
                    h('input', { className: 'mb-input', type: 'number', min: '0', step: '0.5', placeholder: t('budgetUnlimited'),
                      'aria-label': key + ' · ' + t('budget'),
                      value: draft.budget.model_limit_cny[key] ?? '',
                      onChange: e => ctrl.setBudgetLimit('model_limit_cny', key, e.target.value) })));
                }
              }
            }
            return rows;
          })())),

        h('div', { className: 'mb-foot', 'aria-live': 'polite' },
          h('button', { className: 'mb-btn is-primary', disabled: !snap.writable || !ctrl.dirty() || ctrl.saving(), onClick: () => void ctrl.save() }, ctrl.saving() ? t('saving') : t('save')),
          h('button', { className: 'mb-btn is-quiet', disabled: !ctrl.dirty() || ctrl.saving(), onClick: () => ctrl.discard() }, t('discard')),
          ctrl.status() === 'saved' ? h('span', { className: 'mb-msg good' }, t('saved')) : null,
          ['failed', 'conflict', 'invalidUrl'].includes(ctrl.status()) ? h('span', { className: 'mb-msg bad' }, t(ctrl.status() === 'failed' ? 'saveFailed' : ctrl.status())) : null,
          ctrl.dirty() && !ctrl.saving() ? h('span', { className: 'mb-msg' }, t('unsaved')) : null,
          !snap.writable ? h('span', { className: 'mb-msg' }, t('readOnly')) : null));
    }

    function apply(ctx) {
      ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'media-bridge:locale');
      const form = ctx.configForms.get(ENTRY);
      const ctrl = makeController(form, ctx);
      ctx.effect(() => () => ctrl.dispose(), 'media-bridge:controller');
      ctx.effect(() => ctx.remote.$on('credentials/reference-updated', () => ctrl.refreshCred()), 'media-bridge:cred-events');
      ctrl.refreshCred();
      const t = ctx.locale.bind(NS);
      // A top-level settings section registers unconditionally, the way the
      // built-in Models / General / Plugins sections do. whileServed is for
      // pages that edit another owner's namespace; this page edits this
      // bundle's own row, so gating it on the Host serving that namespace
      // would hide the page whenever the Host has not published it.
      ctx.effect(() => ctx.slots.inject('settings.section', () => ctx.slots.register({
        name: 'settings.section',
        id: 'media-bridge',
        order: 30,
        label: () => t('title'),
        locale: NS,
        inject: () => ({ mediaBridge: ctrl }),
      }, Section)), 'media-bridge:page');
    }

    return { inject: ['slots', 'locale', 'remote', 'remote.credentials', 'configForms'], apply };
  },
});
