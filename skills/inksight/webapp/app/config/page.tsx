"use client";

import { useEffect, useState, useCallback, Suspense, useMemo, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { DeviceInfo } from "@/components/config/device-info";
import { LocationPicker } from "@/components/config/location-picker";
import { ModeSelector } from "@/components/config/mode-selector";
import { ModeComposer, type ComposerPropMeta, type ComposerCatalog, type ComposerFragmentState, type ComposerState, withPropDefaults, findCatalogItem, randomComposerId } from "@/components/config/mode-composer";
import { EInkPreviewPanel } from "@/components/config/eink-preview-panel";
import { CalendarReminders } from "@/components/config/calendar-reminders";
import { TimetableEditor, type TimetableData } from "@/components/config/timetable-editor";
import { RefreshStrategyEditor, TimeSlotRule } from "@/components/config/refresh-strategy-editor";
import { StatCard } from "@/components/config/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  Settings,
  Sliders,
  BarChart3,
  RefreshCw,
  Save,
  AlertCircle,
  Loader2,
  Plus,
  Trash2,
  Monitor,
  X,
  Users,
  Keyboard,
} from "lucide-react";
import { authHeaders, fetchCurrentUser, onAuthChanged } from "@/lib/auth";
import { localeFromPathname, withLocalePath } from "@/lib/i18n";
import {
  buildLocationValue,
  cleanLocationValue,
  describeLocation,
  extractLocationValue,
  locationsEqual,
  type LocationValue,
} from "@/lib/locations";

interface UserDevice {
  mac: string;
  nickname: string;
  bound_at: string;
  last_seen: string | null;
  role?: string;
  status?: string;
}

interface DeviceMember {
  user_id: number;
  username: string;
  role: string;
  status: string;
  nickname?: string;
  created_at: string;
}

interface AccessRequestItem {
  id: number;
  mac: string;
  requester_user_id: number;
  requester_username: string;
  status: string;
  created_at: string;
}

type ModeCatalogItem = {
  mode_id: string;
  category: "core" | "more" | "custom" | string;
  source?: string;
  display_name?: string;
  description?: string;
  settings_schema?: ModeSettingSchemaItem[];
  i18n?: {
    zh?: { name?: string; tip?: string };
    en?: { name?: string; tip?: string };
  };
};

const STRATEGIES: Record<string, string> = {
  random: "从已启用的模式中随机选取",
  cycle: "按顺序循环切换已启用的模式",
  time_slot: "根据时间段显示不同内容模式",
  smart: "根据时间段自动匹配最佳模式",
};

const MODE_LANGUAGE_OPTIONS = [
  { value: "zh", label: "中文" },
  { value: "en", label: "英文" },
] as const;

const TONE_OPTIONS = [
  { value: "positive", label: "积极鼓励" },
  { value: "neutral", label: "中性克制" },
  { value: "deep", label: "深沉内省" },
  { value: "humor", label: "轻松幽默" },
] as const;
const PERSONA_PRESETS = ["鲁迅", "王小波", "JARVIS", "苏格拉底", "村上春树"] as const;

import { calculateBatteryPct, queueImmediateRefreshIfOnline } from "@/lib/device-utils";
import { apiUrl, siteUrl, uploadSrc } from "@/lib/api";

function normalizeTone(v: unknown): string {
  if (typeof v !== "string") return "neutral";
  if (v === "positive" || v === "neutral" || v === "deep" || v === "humor") return v;
  const found = TONE_OPTIONS.find((x) => x.label === v);
  return found?.value || "neutral";
}


function normalizeModeIdFromName(name: string) {
  let modeId = name.trim().toUpperCase().replace(/[^A-Z0-9_]/g, "_");
  if (!modeId) modeId = `CUSTOM_${Math.random().toString(36).slice(2, 10).toUpperCase()}`;
  if (!/^[A-Z]/.test(modeId)) modeId = `CUSTOM_${modeId}`;
  return modeId;
}

function fallbackValueForField(fieldName: string) {
  const cleaned = fieldName.trim();
  if (!cleaned) return "Sample";
  return cleaned
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function defaultComposerState(catalog: ComposerCatalog | null): ComposerState {
  const defaultPreset = catalog?.presets?.[0]?.name || "";
  const defaultPresetMeta = catalog?.presets?.[0];
  const defaultFragment = catalog?.fragments?.[0];
  const defaultFragmentStack = Object.fromEntries(
    (catalog?.fragment_stack?.props || []).flatMap((prop) => (prop.default !== undefined ? [[prop.name, prop.default]] : [])),
  );
  return {
    layoutKind: "preset",
    bodyPreset: defaultPreset,
    presetProps: withPropDefaults({}, defaultPresetMeta?.props),
    fragments: defaultFragment
      ? [
          {
            id: randomComposerId(),
            fragment: defaultFragment.name,
            props: withPropDefaults({}, defaultFragment.props),
          },
        ]
      : [],
    fragmentStack: defaultFragmentStack,
    prompt: "",
    cacheable: true,
  };
}

function collectComposerFieldNames(
  composer: ComposerState,
  catalog: ComposerCatalog | null,
) {
  const names = new Set<string>();
  const collect = (metas: ComposerPropMeta[] | undefined, props: Record<string, unknown>) => {
    for (const meta of metas || []) {
      if (meta.value_kind !== "field") continue;
      const value = props[meta.name];
      if (typeof value === "string" && value.trim()) names.add(value.trim());
    }
  };
  if (composer.layoutKind === "preset") {
    const preset = findCatalogItem(catalog?.presets, composer.bodyPreset);
    collect(preset?.props, composer.presetProps);
  } else {
    for (const fragment of composer.fragments) {
      const item = findCatalogItem(catalog?.fragments, fragment.fragment);
      collect(item?.props, fragment.props);
    }
  }
  if (names.size === 0) names.add("text");
  return Array.from(names);
}

const FIELD_HINT_ZH: Record<string, string> = {
  title: "标题（简短有力）",
  subtitle: "副标题",
  meta: "元信息（如分类、来源等简短标注）",
  body: "正文内容",
  text: "正文内容",
  quote: "一句引言或语录",
  author: "作者或来源署名",
  source: "来源出处",
  question: "一个引人思考的问题",
  answer: "问题的答案",
  hint: "提示或线索",
  note: "补充说明（简短）",
  word: "一个词语或短语",
  phonetic: "注音或音标",
  definition: "释义或解释",
  example: "一个例句或案例",
  greeting: "称呼语（如「亲爱的朋友：」）",
  closing: "落款（如「此致敬意」）",
  postscript: "附言（P.S.）",
  tip: "一条实用小建议",
  category: "分类标签",
  name: "名称",
  description: "简要描述",
  summary: "摘要",
  antidote: "应对建议或解决方法",
  message: "一段寄语",
};
const FIELD_HINT_EN: Record<string, string> = {
  title: "a short, punchy title",
  subtitle: "subtitle",
  meta: "brief meta info (category, source, etc.)",
  body: "main body text",
  text: "main body text",
  quote: "a quote or saying",
  author: "author or attribution",
  source: "source or origin",
  question: "a thought-provoking question",
  answer: "the answer",
  hint: "a hint or clue",
  note: "a brief supplementary note",
  word: "a word or short phrase",
  phonetic: "phonetic transcription",
  definition: "definition or explanation",
  example: "an example sentence or case",
  greeting: "greeting (e.g. 'Dear friend,')",
  closing: "closing (e.g. 'Best regards')",
  postscript: "postscript (P.S.)",
  tip: "a practical tip",
  category: "category label",
  name: "name",
  description: "brief description",
  summary: "summary",
  antidote: "advice or remedy",
  message: "a short message",
};

function buildComposerPrompt(prompt: string, fieldNames: string[]) {
  const hints = FIELD_HINT_ZH;
  const userPrompt = prompt.trim();
  const fieldDescs = fieldNames.map((f) => {
    const hint = hints[f];
    return hint ? `"${f}": ${hint}` : `"${f}": ...`;
  });
  const schema = `{ ${fieldNames.map((f) => `"${f}": "..."`).join(", ")} }`;

  return [
    `你是一个墨水屏内容创作者。`,
    userPrompt ? `主题：${userPrompt}` : `请生成简洁有趣的内容。`,
    `请只返回合法 JSON，包含以下字段：`,
    ...fieldDescs.map((d) => `  - ${d}`),
    `所有值必须是字符串。每个字段内容要简洁（适合小屏幕显示）。`,
    `格式示例：${schema}`,
    `以下环境信息仅供参考灵感，不要直接复述：`,
    `{context}`,
  ].join("\n");
}

function buildComposerModeDefinition(args: {
  composer: ComposerState;
  catalog: ComposerCatalog | null;
  modeName: string;
  modeDescription: string;
}) {
  const { composer, catalog, modeName, modeDescription } = args;
  const modeId = normalizeModeIdFromName(modeName || "Custom Mode");
  const displayName = modeName.trim() || modeId;
  const fieldNames = collectComposerFieldNames(composer, catalog);
  const outputSchema = Object.fromEntries(
    fieldNames.map((field) => [field, fallbackValueForField(field)]),
  );
  const fallback = Object.fromEntries(fieldNames.map((field) => [field, fallbackValueForField(field)]));
  const layout: Record<string, unknown> = {
    layout_engine: "component_tree",
    status_bar: { line_width: 1, dashed: false },
    footer: { label: modeId, attribution_template: "— InkSight" },
  };
  if (composer.layoutKind === "preset") {
    layout.body_preset = composer.bodyPreset;
    layout.preset_props = composer.presetProps;
  } else {
    layout.fragments = composer.fragments.map((fragment) => ({
      fragment: fragment.fragment,
      props: fragment.props,
    }));
    layout.fragment_stack = composer.fragmentStack;
  }
  return {
    mode_id: modeId,
    display_name: displayName,
    icon: "star",
    cacheable: composer.cacheable,
    description: modeDescription.trim() || composer.prompt.trim() || displayName,
    content: {
      type: "llm_json",
      prompt_template: buildComposerPrompt(composer.prompt, fieldNames),
      output_schema: outputSchema,
      temperature: 0.7,
      fallback,
    },
    layout,
  };
}

function parseComposerModeDefinition(
  raw: unknown,
  catalog: ComposerCatalog | null,
): { composer: ComposerState | null; error: string | null } {
  if (!raw || typeof raw !== "object") {
    return { composer: null, error: "Invalid mode definition" };
  }
  const modeDef = raw as Record<string, unknown>;
  const layout = (modeDef.layout && typeof modeDef.layout === "object") ? (modeDef.layout as Record<string, unknown>) : null;
  if (!layout || layout.layout_engine !== "component_tree") {
    return { composer: null, error: "Only component_tree preset/fragment layouts are supported in Composer." };
  }
  const content = (modeDef.content && typeof modeDef.content === "object") ? (modeDef.content as Record<string, unknown>) : null;
  const prompt = typeof content?.prompt_template === "string" ? content.prompt_template : "";
  const cacheable = modeDef.cacheable !== false;
  if (typeof layout.body_preset === "string" && layout.body_preset.trim()) {
    const presetName = layout.body_preset.trim();
    if (!findCatalogItem(catalog?.presets, presetName)) {
      return { composer: null, error: `Unsupported preset: ${presetName}` };
    }
    return {
      composer: {
        layoutKind: "preset",
        bodyPreset: presetName,
        presetProps: withPropDefaults(
          (layout.preset_props && typeof layout.preset_props === "object") ? (layout.preset_props as Record<string, unknown>) : {},
          findCatalogItem(catalog?.presets, presetName)?.props,
        ),
        fragments: [],
        fragmentStack: {},
        prompt,
        cacheable,
      },
      error: null,
    };
  }
  if (Array.isArray(layout.fragments)) {
    const fragments: ComposerFragmentState[] = [];
    for (const entry of layout.fragments) {
      if (!entry || typeof entry !== "object") {
        return { composer: null, error: "Unsupported fragment entry." };
      }
      const fragmentName = typeof (entry as Record<string, unknown>).fragment === "string"
        ? ((entry as Record<string, unknown>).fragment as string).trim()
        : "";
      const fragmentMeta = findCatalogItem(catalog?.fragments, fragmentName);
      if (!fragmentName || !fragmentMeta) {
        return { composer: null, error: `Unsupported fragment: ${fragmentName || "unknown"}` };
      }
      const props = "props" in (entry as Record<string, unknown>)
        ? (((entry as Record<string, unknown>).props && typeof (entry as Record<string, unknown>).props === "object")
            ? ((entry as Record<string, unknown>).props as Record<string, unknown>)
            : {})
        : Object.fromEntries(
            Object.entries(entry as Record<string, unknown>).filter(([key]) => key !== "fragment"),
          );
      fragments.push({
        id: randomComposerId(),
        fragment: fragmentName,
        props: withPropDefaults(props, fragmentMeta.props),
      });
    }
    return {
      composer: {
        layoutKind: "fragments",
        bodyPreset: catalog?.presets?.[0]?.name || "",
        presetProps: {},
        fragments,
        fragmentStack: withPropDefaults(
          (layout.fragment_stack && typeof layout.fragment_stack === "object") ? (layout.fragment_stack as Record<string, unknown>) : {},
          catalog?.fragment_stack?.props,
        ),
        prompt,
        cacheable,
      },
      error: null,
    };
  }
  return { composer: null, error: "Only body_preset or fragments layouts are supported in Composer." };
}

// Custom mode templates removed (AI-only creation)


const TABS = [
  { id: "modes", label: "模式", icon: Settings },
  { id: "preferences", label: "个性化", icon: Sliders },
  { id: "sharing", label: "共享成员", icon: Users },
  { id: "stats", label: "状态", icon: BarChart3 },
] as const;

type TabId = (typeof TABS)[number]["id"];

interface DeviceConfig {
  mac?: string;
  modes?: string[];
  refreshStrategy?: string;
  refreshInterval?: number;
  refresh_strategy?: string;
  refresh_minutes?: number;
  city?: string;
  latitude?: number;
  longitude?: number;
  timezone?: string;
  admin1?: string;
  country?: string;
  language?: string;
  contentTone?: string;
  content_tone?: string;
  characterTones?: string[];
  character_tones?: string[];
  countdownEvents?: { name: string; date: string }[];
  countdown_events?: { name: string; date: string }[];
  memoText?: string;
  memo_text?: string;
  mode_overrides?: Record<string, ModeOverride>;
  modeOverrides?: Record<string, ModeOverride>;
  is_focus_listening?: boolean;
  focus_listening?: number;
  is_always_active?: boolean;
  always_active?: number | boolean;
}

interface ModeOverride {
  city?: string;
  latitude?: number;
  longitude?: number;
  timezone?: string;
  admin1?: string;
  country?: string;
  [key: string]: unknown;
}

interface PendingPreviewConfirm {
  mode: string;
  forceNoCache: boolean;
  forcedModeOverride?: ModeOverride;
}

type ParamModalType = "quote" | "weather" | "cities" | "memo" | "countdown" | "habit" | "lifebar" | "calendar" | "timetable" | "vocab";
interface ParamModalState {
  type: ParamModalType;
  mode: string;
  action: "preview" | "apply";
}

interface ModeSettingSchemaItem {
  key: string;
  label: string;
  type?: "text" | "textarea" | "number" | "select" | "boolean" | "location";
  placeholder?: string;
  default?: unknown;
  min?: number;
  max?: number;
  step?: number;
  description?: string;
  as_json?: boolean;
  options?: Array<{ value: string; label: string } | string>;
}

interface DeviceStats {
  total_renders?: number;
  cache_hit_rate?: number;
  last_battery_voltage?: number;
  last_rssi?: number;
  last_refresh?: string;
  error_count?: number;
  mode_frequency?: Record<string, number>;
}

type RuntimeMode = "active" | "interval" | "unknown";

type AlbumPhoto = {
  id: string;
  filename: string;
  url: string;
};

const ALBUM_MAX_PHOTOS = 7;

function ConfigPageInner() {
  const pathname = usePathname();
  const locale = localeFromPathname(pathname || "/");
  const searchParams = useSearchParams();
  const mac = searchParams.get("mac") || "";
  const preferMac = searchParams.get("prefer_mac") || "";
  const prefillCode = searchParams.get("code") || "";
  const [currentUser, setCurrentUser] = useState<{ user_id: number; username: string } | null | undefined>(undefined);
  const [userDevices, setUserDevices] = useState<UserDevice[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(false);
  const [pairCodeInput, setPairCodeInput] = useState("");
  const [pairingDevice, setPairingDevice] = useState(false);
  const [bindMacInput, setBindMacInput] = useState("");
  const [bindNicknameInput, setBindNicknameInput] = useState("");
  const [deviceMembers, setDeviceMembers] = useState<DeviceMember[]>([]);
  const [pendingRequests, setPendingRequests] = useState<AccessRequestItem[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [requestsLoading, setRequestsLoading] = useState(false);
  const [shareUsernameInput, setShareUsernameInput] = useState("");
  const [macAccessDenied, setMacAccessDenied] = useState(false);

  const refreshCurrentUser = useCallback(() => {
    fetchCurrentUser()
      .then((d) => setCurrentUser(d ? { user_id: d.user_id, username: d.username } : null))
      .catch(() => setCurrentUser(null));
  }, []);

  useEffect(() => {
    refreshCurrentUser();
  }, [refreshCurrentUser]);

  useEffect(() => {
    const off = onAuthChanged(refreshCurrentUser);
    const onFocus = () => refreshCurrentUser();
    window.addEventListener("focus", onFocus);
    return () => {
      off();
      window.removeEventListener("focus", onFocus);
    };
  }, [refreshCurrentUser]);

  const loadUserDevices = useCallback(async () => {
    setDevicesLoading(true);
    try {
      const res = await fetch(apiUrl("/api/user/devices"), { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        setUserDevices(data.devices || []);
      }
    } catch { /* ignore */ }
    finally { setDevicesLoading(false); }
  }, []);

  const loadPendingRequests = useCallback(async () => {
    setRequestsLoading(true);
    try {
      const res = await fetch(apiUrl("/api/user/devices/requests"), { headers: authHeaders() });
      if (res.ok) {
        const data = await res.json();
        setPendingRequests(data.requests || []);
      }
    } catch { /* ignore */ }
    finally { setRequestsLoading(false); }
  }, []);

  const loadDeviceMembers = useCallback(async (deviceMac: string) => {
    if (!deviceMac) return;
    setMembersLoading(true);
    try {
      const res = await fetch(apiUrl(`/api/user/devices/${encodeURIComponent(deviceMac)}/members`), {
        headers: authHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        setDeviceMembers(data.members || []);
      } else {
        setDeviceMembers([]);
      }
    } catch {
      setDeviceMembers([]);
    } finally {
      setMembersLoading(false);
    }
  }, []);

  useEffect(() => {
    if (currentUser) {
      loadUserDevices();
      loadPendingRequests();
    }
  }, [currentUser, loadPendingRequests, loadUserDevices]);

  useEffect(() => {
    if (mac) return;
    const normalizedCode = prefillCode.trim().toUpperCase();
    if (normalizedCode) {
      setPairCodeInput((prev) => prev || normalizedCode);
    }
    const normalizedMac = preferMac.trim().toUpperCase();
    if (normalizedMac) {
      setBindMacInput((prev) => prev || normalizedMac);
    }
  }, [mac, preferMac, prefillCode]);

  useEffect(() => {
    if (mac || !preferMac || !currentUser || devicesLoading) return;
    const normalizedMac = preferMac.trim().toUpperCase();
    if (!normalizedMac) return;
    const alreadyBound = userDevices.some((item) => item.mac.toUpperCase() === normalizedMac);
    if (alreadyBound) {
      window.location.href = `${siteUrl(withLocalePath(locale, "/config"))}?mac=${encodeURIComponent(normalizedMac)}`;
    }
  }, [currentUser, devicesLoading, locale, mac, preferMac, userDevices]);

  const handlePairDevice = async () => {
    const normalized = pairCodeInput.trim().toUpperCase();
    if (!normalized) return;
    setPairingDevice(true);
    try {
      const res = await fetch(apiUrl("/api/claim/consume"), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ pair_code: normalized }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.error || "配对失败", "error");
        return;
      }
      setPairCodeInput("");
      if (data.status === "claimed" || data.status === "already_member" || data.status === "active") {
        await loadUserDevices();
        await loadPendingRequests();
        window.location.href = `${siteUrl(withLocalePath(locale, "/config"))}?mac=${encodeURIComponent(data.mac)}`;
        return;
      }
      await loadPendingRequests();
      showToast("已提交绑定申请，等待 owner 同意", "info");
    } catch {
      showToast("配对失败", "error");
    } finally {
      setPairingDevice(false);
    }
  };

  const handleBindDevice = async (deviceMac: string, nickname?: string) => {
    try {
      const res = await fetch(apiUrl("/api/user/devices"), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ mac: deviceMac, nickname: nickname || "" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.error || "绑定失败", "error");
        return null;
      }
      setBindMacInput("");
      setBindNicknameInput("");
      await loadUserDevices();
      await loadPendingRequests();
      return data;
    } catch {
      showToast("绑定失败", "error");
      return null;
    }
  };

  const handleUnbindDevice = async (deviceMac: string, force = false) => {
    try {
      const url = apiUrl(`/api/user/devices/${encodeURIComponent(deviceMac)}${force ? "?force=true" : ""}`);
      const res = await fetch(url, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (res.ok) {
        await loadUserDevices();
        return;
      }
      if (res.status === 409 && !force) {
        const ok = confirm("该设备下还有共享成员，强制解绑将移除所有成员。是否继续？");
        if (ok) await handleUnbindDevice(deviceMac, true);
      }
    } catch { /* ignore */ }
  };

  const handleApproveRequest = async (requestId: number) => {
    try {
      const res = await fetch(apiUrl(`/api/user/devices/requests/${requestId}/approve`), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: "{}",
      });
      if (res.ok) {
        await loadPendingRequests();
        if (mac) await loadDeviceMembers(mac);
        showToast("已同意绑定请求", "success");
      } else {
        showToast("同意失败", "error");
      }
    } catch {
      showToast("同意失败", "error");
    }
  };

  const handleRejectRequest = async (requestId: number) => {
    try {
      const res = await fetch(apiUrl(`/api/user/devices/requests/${requestId}/reject`), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: "{}",
      });
      if (res.ok) {
        await loadPendingRequests();
        showToast("已拒绝绑定请求", "success");
      } else {
        showToast("拒绝失败", "error");
      }
    } catch {
      showToast("拒绝失败", "error");
    }
  };

  const handleShareDevice = async () => {
    if (!mac || !shareUsernameInput.trim()) return;
    try {
      const res = await fetch(apiUrl(`/api/user/devices/${encodeURIComponent(mac)}/share`), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ username: shareUsernameInput.trim() }),
      });
      if (!res.ok) throw new Error("share failed");
      setShareUsernameInput("");
      await loadDeviceMembers(mac);
      await loadPendingRequests();
      showToast("分享成功", "success");
    } catch {
      showToast("分享失败", "error");
    }
  };

  const handleRemoveMember = async (targetUserId: number) => {
    if (!mac) return;
    try {
      const res = await fetch(apiUrl(`/api/user/devices/${encodeURIComponent(mac)}/members/${targetUserId}`), {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error("remove failed");
      await loadDeviceMembers(mac);
      showToast("成员已移除", "success");
    } catch {
      showToast("移除成员失败", "error");
    }
  };

  const [activeTab, setActiveTab] = useState<TabId>("modes");
  const [config, setConfig] = useState<DeviceConfig>({});
  const [selectedModes, setSelectedModes] = useState<Set<string>>(new Set(["STOIC", "ZEN", "DAILY"]));
  const [strategy, setStrategy] = useState("random");
  const [refreshMin, setRefreshMin] = useState(60);
  const [timeSlotRules, setTimeSlotRules] = useState<TimeSlotRule[]>([]);
  const [city, setCity] = useState("");
  const [locationMeta, setLocationMeta] = useState<LocationValue>({});
  const [modeLanguage, setModeLanguage] = useState("zh");
  const [contentTone, setContentTone] = useState("neutral");
  const [characterTones, setCharacterTones] = useState<string[]>([]);
  const [customPersonaTone, setCustomPersonaTone] = useState("");
  const [modeOverrides, setModeOverrides] = useState<Record<string, ModeOverride>>({});
  const [memoText, setMemoText] = useState("");

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" | "info" } | null>(null);
  const [stats, setStats] = useState<DeviceStats | null>(null);
  const [previewImg, setPreviewImg] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewStatusText, setPreviewStatusText] = useState("");
  const [previewMode, setPreviewMode] = useState("");
  const [previewColors, setPreviewColors] = useState(2);
  const [previewWidth, setPreviewWidth] = useState(400);
  const [previewHeight, setPreviewHeight] = useState(300);
  const [previewNoCacheOnce, setPreviewNoCacheOnce] = useState(false);
  const [previewCacheHit, setPreviewCacheHit] = useState<boolean | null>(null);
  const [previewLlmStatus, setPreviewLlmStatus] = useState<string | null>(null);
  const [previewConfirm, setPreviewConfirm] = useState<PendingPreviewConfirm | null>(null);

  // 自适应图片（MY_ADAPTIVE）多图上传弹窗
  const adaptiveFileInputRef = useRef<HTMLInputElement | null>(null);
  const [adaptiveModal, setAdaptiveModal] = useState<{ action: "preview" | "apply" } | null>(null);
  const [adaptiveImageUrls, setAdaptiveImageUrls] = useState<string[]>([]);
  const [adaptiveUploading2, setAdaptiveUploading2] = useState(false);

  // 相册（CUSTOM_ALBUM）照片管理弹窗：照片存在服务端，按账号归档（设备 owner 的账号相册）
  const albumFileInputRef = useRef<HTMLInputElement | null>(null);
  const [albumModal, setAlbumModal] = useState<{ action: "preview" | "apply" } | null>(null);
  const [albumPhotos, setAlbumPhotos] = useState<AlbumPhoto[]>([]);
  const [albumUploading, setAlbumUploading] = useState(false);
  const [albumLoading, setAlbumLoading] = useState(false);

  // 参数弹窗（与 /preview 页面保持一致）
  const [paramModal, setParamModal] = useState<ParamModalState | null>(null);
  const [quoteDraft, setQuoteDraft] = useState("");
  const [authorDraft, setAuthorDraft] = useState("");
  const [weatherDraftLocation, setWeatherDraftLocation] = useState<LocationValue>({});
  // 自选多城（如诗笺日课的双城）：每个 location 槽位一份草稿
  const [cityPairDraft, setCityPairDraft] = useState<Record<string, LocationValue>>({});
  const [memoDraft, setMemoDraft] = useState<{ title1: string; text1: string; title2: string; text2: string; title3: string; text3: string }>({ title1: "", text1: "", title2: "", text2: "", title3: "", text3: "" });
  const [countdownName, setCountdownName] = useState("元旦");
  const [countdownDate, setCountdownDate] = useState("2027-01-01");
  const [habitItems, setHabitItems] = useState(
    [{ name: "早起", done: false }, { name: "运动", done: false }, { name: "阅读", done: false }],
  );
  const [userAge, setUserAge] = useState(30);
  const [lifeExpectancy, setLifeExpectancy] = useState(80);
  const [vocabDeckId, setVocabDeckId] = useState("core_en");
  const [vocabDailyLimit, setVocabDailyLimit] = useState(30);
  const [timetableData, setTimetableData] = useState<TimetableData>({
    style: "weekly",
    periods: ["08:00-09:30", "10:00-11:30", "14:00-15:30", "16:00-17:30"],
    courses: {
          "0-0": "高等数学/A201", "0-2": "线性代数/A201",
          "1-1": "大学英语/B305", "1-3": "体育/操场",
          "2-0": "数据结构/C102", "2-2": "计算机网络/C102",
          "3-1": "概率论/A201", "3-3": "毛概/D405",
          "4-0": "操作系统/C102",
        },
  });
  const [, setCurrentMode] = useState<string>("");
  const [applyToScreenLoading, setApplyToScreenLoading] = useState(false);
  const [, setFavoritedModes] = useState<Set<string>>(new Set());
  const favoritesLoadedMacRef = useRef<string>("");
  const previewStreamRef = useRef<EventSource | null>(null);
  const previewObjectUrlRef = useRef<string | null>(null);
  const [runtimeMode, setRuntimeMode] = useState<RuntimeMode>("unknown");
  const [isOnline, setIsOnline] = useState(false);
  const [lastSeen, setLastSeen] = useState<string | null>(null);
  const [isFocusListening, setIsFocusListening] = useState(false);
  const [alwaysActive, setAlwaysActive] = useState(false);
  const [focusToggleLoading, setFocusToggleLoading] = useState(false);
  const [focusAlertToken, setFocusAlertToken] = useState<string>("");
  const [showFocusTokenModal, setShowFocusTokenModal] = useState(false);

  const [customDesc, setCustomDesc] = useState("");
  const [customModeName, setCustomModeName] = useState("");
  const [customModeDescription, setCustomModeDescription] = useState("");
  const [customJson, setCustomJson] = useState("");
  const [customGenerating, setCustomGenerating] = useState(false);
  const [customPreviewImg, setCustomPreviewImg] = useState<string | null>(null);
  const [, setCustomPreviewLoading] = useState(false);
  const [editingCustomMode, setEditingCustomMode] = useState(false);
  const [customEditorSource, setCustomEditorSource] = useState<"ai" | "composer" | "json" | null>(null);
  const [customEditorTab, setCustomEditorTab] = useState<"ai" | "composer">("ai");
  const [composerCatalog, setComposerCatalog] = useState<ComposerCatalog | null>(null);
  const [composerState, setComposerState] = useState<ComposerState>({ layoutKind: "preset", bodyPreset: "", presetProps: {}, fragments: [], fragmentStack: {}, prompt: "", cacheable: true });
  const [composerSyncError, setComposerSyncError] = useState<string | null>(null);
  const [, setCustomJsonError] = useState<string | null>(null);
  const [composerPreviewUrl, setComposerPreviewUrl] = useState<string | null>(null);
  const [composerPreviewLoading, setComposerPreviewLoading] = useState(false);
  const [previewModeLabelOverride, setPreviewModeLabelOverride] = useState<string | null>(null);
  const previewPanelRef = useRef<HTMLDivElement | null>(null);

  const [catalogItems, setCatalogItems] = useState<ModeCatalogItem[]>([]);

  const showToast = useCallback((msg: string, type: "success" | "error" | "info" = "info") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }, []);

  const currentLocation = useMemo(
    () => buildLocationValue(city, locationMeta),
    [city, locationMeta],
  );
  const defaultWeatherLocation = useMemo(
    () => (currentLocation.city ? cleanLocationValue(currentLocation) : buildLocationValue("杭州")),
    [currentLocation],
  );

  const applyGlobalLocation = useCallback((next: Partial<LocationValue> | null | undefined) => {
    const cleaned = cleanLocationValue(next);
    setCity(cleaned.city || "");
    setLocationMeta(cleaned);
  }, []);

  const replacePreviewImg = useCallback((nextUrl: string | null) => {
    if (previewObjectUrlRef.current) {
      URL.revokeObjectURL(previewObjectUrlRef.current);
      previewObjectUrlRef.current = null;
    }
    if (nextUrl) previewObjectUrlRef.current = nextUrl;
    setPreviewImg(nextUrl);
  }, []);

  const uploadLocalImage = useCallback(async (file: File): Promise<string> => {
    const fd = new FormData();
    fd.append("file", file);
    const up = await fetch(apiUrl("/api/uploads"), { method: "POST", body: fd });
    if (!up.ok) {
      const err = await up.text().catch(() => "");
      throw new Error(err || `upload failed: ${up.status}`);
    }
    const data = (await up.json()) as { url?: string };
    if (!data.url) throw new Error("upload failed: missing url");
    return data.url;
  }, []);

  // ---- 相册（CUSTOM_ALBUM）照片管理 ----
  // 照片按账号存在服务端 runtime_album/U<user_id>/；设备作用域下服务端会解析到这台设备 owner 的账号，
  // 所以这里用设备路由，前端只负责上传/删除/预览。
  const albumApiBase = useCallback(() => {
    if (!mac) return "";
    return `/api/album/${encodeURIComponent(mac)}`;
  }, [mac]);

  const loadAlbumPhotos = useCallback(async () => {
    const base = albumApiBase();
    if (!base) {
      setAlbumPhotos([]);
      return;
    }
    setAlbumLoading(true);
    try {
      const res = await fetch(apiUrl(`${base}/photos`), { headers: authHeaders() });
      if (!res.ok) throw new Error(`list failed: ${res.status}`);
      const data = (await res.json()) as { photos?: AlbumPhoto[] };
      setAlbumPhotos(Array.isArray(data.photos) ? data.photos : []);
    } catch (err) {
      console.error("[CONFIG] Failed to load album photos:", err);
      setAlbumPhotos([]);
    } finally {
      setAlbumLoading(false);
    }
  }, [albumApiBase]);

  const uploadAlbumPhotos = useCallback(async (files: FileList | null) => {
    const base = albumApiBase();
    if (!base || !files || files.length === 0) return;
    setAlbumUploading(true);
    try {
      for (const file of Array.from(files)) {
        if (albumPhotos.length + Array.from(files).indexOf(file) >= ALBUM_MAX_PHOTOS) {
          showToast(`最多 ${ALBUM_MAX_PHOTOS} 张照片`, "error");
          break;
        }
        const fd = new FormData();
        fd.append("file", file);
        const res = await fetch(apiUrl(`${base}/photos`), {
          method: "POST",
          body: fd,
          headers: authHeaders(),
        });
        if (!res.ok) {
          const msg = await res.text().catch(() => "");
          throw new Error(msg || `upload failed: ${res.status}`);
        }
      }
      await loadAlbumPhotos();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "上传失败";
      showToast(msg, "error");
    } finally {
      setAlbumUploading(false);
    }
  }, [albumApiBase, albumPhotos.length, loadAlbumPhotos, showToast]);

  const deleteAlbumPhoto = useCallback(async (photoId: string) => {
    const base = albumApiBase();
    if (!base) return;
    try {
      const res = await fetch(apiUrl(`${base}/photos/${encodeURIComponent(photoId)}`), {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!res.ok) {
        const msg = await res.text().catch(() => "");
        throw new Error(msg || `delete failed: ${res.status}`);
      }
      await loadAlbumPhotos();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "删除失败";
      showToast(msg, "error");
    }
  }, [albumApiBase, loadAlbumPhotos, showToast]);

  useEffect(() => {
    return () => {
      if (previewObjectUrlRef.current) {
        URL.revokeObjectURL(previewObjectUrlRef.current);
        previewObjectUrlRef.current = null;
      }
    };
  }, []);

  const nextConfigPath = useMemo(() => {
    const params = new URLSearchParams();
    if (mac) {
      params.set("mac", mac);
    } else {
      if (preferMac) params.set("prefer_mac", preferMac);
      if (prefillCode) params.set("code", prefillCode);
    }
    const query = params.toString();
    return query ? `${withLocalePath(locale, "/config")}?${query}` : withLocalePath(locale, "/config");
  }, [locale, mac, preferMac, prefillCode]);

  const refreshCatalog = useCallback(async () => {
    const params = new URLSearchParams();
    if (mac) params.append("mac", mac);
    try {
      const res = await fetch(apiUrl(`/api/modes/catalog?${params.toString()}`), { headers: authHeaders() });
      if (!res.ok) {
        console.error("[CONFIG] Catalog request failed:", res.status, res.statusText);
        return;
      }
      const data = await res.json().catch((err) => {
        console.error("[CONFIG] Failed to parse catalog JSON:", err);
        return {};
      });
      if (data.items && Array.isArray(data.items)) {
        setCatalogItems(data.items);
        setComposerCatalog((data.custom_mode_composer && typeof data.custom_mode_composer === "object")
          ? (data.custom_mode_composer as ComposerCatalog)
          : null);
      } else {
        console.error("[CONFIG] Invalid catalog response:", data);
      }
    } catch (err) {
      console.error("[CONFIG] Failed to load catalog:", err);
    }
  }, [mac]);

  useEffect(() => {
    refreshCatalog();
  }, [refreshCatalog]);

  useEffect(() => {
    if (!composerCatalog) return;
    setComposerState((prev) => {
      if (prev.bodyPreset || prev.fragments.length > 0) return prev;
      return defaultComposerState(composerCatalog);
    });
  }, [composerCatalog]);

  const syncComposerFromModeDef = useCallback((modeDef: unknown) => {
    const parsed = parseComposerModeDefinition(modeDef, composerCatalog);
    if (parsed.composer) {
      setComposerState(parsed.composer);
      setComposerSyncError(null);
      return true;
    }
    setComposerSyncError(parsed.error);
    return false;
  }, [composerCatalog]);

  const openCreateCustomMode = useCallback(() => {
    setCustomDesc("");
    setCustomModeName("");
    setCustomModeDescription("");
    setCustomJson("");
    setCustomJsonError(null);
    setCustomEditorSource(null);
    setComposerSyncError(null);
    setComposerState(defaultComposerState(composerCatalog));
    setCustomEditorTab("ai");
    setEditingCustomMode(true);
  }, [composerCatalog]);

  const handleComposerChange = useCallback((updater: (prev: ComposerState) => ComposerState) => {
    setCustomEditorSource("composer");
    setComposerState(updater);
    setComposerSyncError(null);
  }, []);

  const fetchComposerPreview = useCallback(async () => {
    if (!composerCatalog) return;
    const modeDef = buildComposerModeDefinition({
      composer: composerState,
      catalog: composerCatalog,
      modeName: customModeName || "Preview",
      modeDescription: customModeDescription,
    });
    setComposerPreviewLoading(true);
    try {
      const res = await fetch(apiUrl("/api/modes/custom/preview"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode_def: modeDef, mac: mac || undefined, colors: previewColors, w: previewWidth, h: previewHeight, layout_only: true }),
      });
      if (!res.ok) {
        setComposerPreviewUrl(null);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      setComposerPreviewUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return url; });
    } catch {
      setComposerPreviewUrl(null);
    } finally {
      setComposerPreviewLoading(false);
    }
  }, [composerCatalog, composerState, customModeName, customModeDescription, false, mac, previewColors]);

  useEffect(() => {
    if (!composerCatalog) return;
    if (customEditorSource !== "composer") return;
    const modeDef = buildComposerModeDefinition({
      composer: composerState,
      catalog: composerCatalog,
      modeName: customModeName,
      modeDescription: customModeDescription,
    });
    setCustomJson(JSON.stringify(modeDef, null, 2));
    setCustomEditorSource("composer");
    setCustomJsonError(null);
  }, [composerCatalog, composerState, customEditorSource, customModeDescription, customModeName, false]);

  useEffect(() => {
    if (mac && currentUser) {
      loadDeviceMembers(mac);
      loadPendingRequests();
    }
  }, [currentUser, loadDeviceMembers, loadPendingRequests, mac]);

  useEffect(() => {
    setMacAccessDenied(false);
  }, [mac]);

  useEffect(() => {
    if (!mac) return;
    fetch(apiUrl(`/api/device/${encodeURIComponent(mac)}/state`), { headers: authHeaders() })
      .then((r) => {
        if (r.status === 401 || r.status === 403) {
          setMacAccessDenied(true);
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then(async (d) => {
        if (!d?.last_persona) return;
        setCurrentMode(d.last_persona);
        setPreviewMode(d.last_persona);
      })
      .catch(() => {});
  }, [mac]);

  useEffect(() => {
    if (!mac) return;
    setLoading(true);
    fetch(apiUrl(`/api/config/${encodeURIComponent(mac)}`), { headers: authHeaders() })
      .then((r) => {
        if (r.status === 401 || r.status === 403) {
          setMacAccessDenied(true);
          throw new Error("无权限");
        }
        if (!r.ok) throw new Error("无配置");
        return r.json();
      })
      .then((cfg: DeviceConfig) => {
        setConfig(cfg);
        if (cfg.modes?.length) setSelectedModes(new Set(cfg.modes.map((m) => m.toUpperCase())));
        if (cfg.refreshStrategy || cfg.refresh_strategy) setStrategy((cfg.refreshStrategy || cfg.refresh_strategy) as string);
        if (cfg.refreshInterval || cfg.refresh_minutes) setRefreshMin((cfg.refreshInterval || cfg.refresh_minutes) as number);
        applyGlobalLocation(extractLocationValue(cfg as Record<string, unknown>));
        setModeLanguage((cfg as Record<string, unknown>).modeLanguage as string || (cfg as Record<string, unknown>).mode_language as string || "zh");
        if (cfg.contentTone || cfg.content_tone) setContentTone(normalizeTone(cfg.contentTone || cfg.content_tone));
        if (cfg.characterTones || cfg.character_tones) setCharacterTones((cfg.characterTones || cfg.character_tones) as string[]);
        if (cfg.mode_overrides) setModeOverrides(cfg.mode_overrides);
        else if (cfg.modeOverrides) setModeOverrides(cfg.modeOverrides);
        setIsFocusListening(Boolean(cfg.is_focus_listening ?? Number(cfg.focus_listening || 0) === 1));
        setAlwaysActive(Boolean(cfg.is_always_active ?? Number(cfg.always_active || 0) === 1));
        const loadedOverrides = ((cfg.mode_overrides || cfg.modeOverrides || {}) as Record<string, ModeOverride>);
        const adaptiveOv = loadedOverrides?.MY_ADAPTIVE;
        if (adaptiveOv) {
          const urls = adaptiveOv.image_urls;
          if (Array.isArray(urls) && urls.length > 0) {
            setAdaptiveImageUrls(urls.filter((u: unknown) => typeof u === "string" && (u as string).trim()) as string[]);
          } else if (typeof adaptiveOv.image_url === "string" && (adaptiveOv.image_url as string).trim()) {
            setAdaptiveImageUrls([adaptiveOv.image_url as string]);
          }
        }
        const memoFromOverride = loadedOverrides?.MEMO?.memo_text;
        if (typeof memoFromOverride === "string" && memoFromOverride.trim()) {
          setMemoText(memoFromOverride);
        } else if (cfg.memoText || cfg.memo_text) {
          setMemoText((cfg.memoText || cfg.memo_text) as string);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [applyGlobalLocation, mac]);

  const getModeOverride = useCallback((modeId: string) => {
    return modeOverrides[modeId] || {};
  }, [modeOverrides]);

  const sanitizeModeOverride = useCallback((input: ModeOverride) => {
    const cleaned: ModeOverride = {};
    for (const [k, raw] of Object.entries(input)) {
      if (
        k === "city" ||
        k === "timezone" ||
        k === "admin1" ||
        k === "country"
      ) {
        if (typeof raw === "string" && raw.trim()) cleaned[k] = raw.trim();
        continue;
      }
      if (k === "latitude" || k === "longitude") {
        if (typeof raw === "number" && Number.isFinite(raw)) cleaned[k] = raw;
        continue;
      }
      if (typeof raw === "string") {
        if (raw.trim()) cleaned[k] = raw.trim();
        continue;
      }
      if (typeof raw === "number") {
        if (!Number.isNaN(raw)) cleaned[k] = raw;
        continue;
      }
      if (typeof raw === "boolean") {
        cleaned[k] = raw;
        continue;
      }
      if (Array.isArray(raw)) {
        if (raw.length > 0) cleaned[k] = raw;
        continue;
      }
      if (raw && typeof raw === "object") {
        if (Object.keys(raw).length > 0) cleaned[k] = raw as Record<string, unknown>;
      }
    }
    return cleaned;
  }, []);

  const updateModeOverride = useCallback((modeId: string, patch: Partial<ModeOverride>) => {
    setModeOverrides((prev) => {
      const next = { ...(prev[modeId] || {}), ...patch } as ModeOverride;
      const cleaned = sanitizeModeOverride(next);
      if (!Object.keys(cleaned).length) {
        const copied = { ...prev };
        delete copied[modeId];
        return copied;
      }
      return { ...prev, [modeId]: cleaned };
    });
  }, [sanitizeModeOverride]);

  const modeSchemaMap = useMemo(
    () => Object.fromEntries(catalogItems.map((m) => [m.mode_id.toUpperCase(), m.settings_schema || []])),
    [catalogItems]
  );

  // 模式自己在 settings_schema 里声明 type="location" → 需要用户为该模式指定城市。
  // WEATHER 之外的模式（如 CUSTOM_DAYOVERVIEW）据此获得与天气模式同样的城市选择入口。
  // 声明了多个 location 的模式（如诗笺日课的双城）走自选多城弹窗。
  const modeLocationItems = useCallback((modeId: string) => {
    const m = (modeId || "").toUpperCase();
    const schema = modeSchemaMap[m] || [];
    return schema.filter((item) => item.type === "location");
  }, [modeSchemaMap]);

  const modeNeedsLocation = useCallback((modeId: string) => {
    return modeLocationItems(modeId).length > 0;
  }, [modeLocationItems]);

  const requiresParamModal = useCallback((modeId: string) => {
    const m = (modeId || "").toUpperCase();
    return m === "WEATHER" || m === "MEMO" || m === "MY_QUOTE" || m === "COUNTDOWN" || m === "HABIT" || m === "LIFEBAR" || m === "CALENDAR" || m === "TIMETABLE" || m === "VOCAB_REVIEW" || modeNeedsLocation(m);
  }, [modeNeedsLocation]);

  const openParamModal = useCallback((modeId: string, action: "preview" | "apply") => {
    const m = (modeId || "").toUpperCase();
    const locationItems = modeLocationItems(m);
    if (locationItems.length > 1) {
      // 自选多城：每个槽位一个地点选择器，存到 mode_settings 下（不覆盖其它字段）
      const saved = (modeOverrides[m] || {}) as Record<string, unknown>;
      const savedSettings =
        saved.mode_settings && typeof saved.mode_settings === "object" && !Array.isArray(saved.mode_settings)
          ? (saved.mode_settings as Record<string, unknown>)
          : {};
      const drafts: Record<string, LocationValue> = {};
      for (const item of locationItems) {
        drafts[item.key] = extractLocationValue(savedSettings[item.key] as Record<string, unknown>);
      }
      setCityPairDraft(drafts);
      setParamModal({ type: "cities", mode: m, action });
      return;
    }
    if (m === "WEATHER" || modeNeedsLocation(m)) {
      setWeatherDraftLocation({});
      setParamModal({ type: "weather", mode: m, action });
      return;
    }
    if (m === "MEMO") {
      const savedMemo = (modeOverrides[m] || {}) as Record<string, unknown>;
      const ms = (
        savedMemo.mode_settings && typeof savedMemo.mode_settings === "object" && !Array.isArray(savedMemo.mode_settings)
          ? savedMemo.mode_settings
          : savedMemo
      ) as Record<string, string>;
      setMemoDraft({
        title1: ms.memo_title_1 || "",
        text1: ms.memo_text_1 || "",
        title2: ms.memo_title_2 || "",
        text2: ms.memo_text_2 || "",
        title3: ms.memo_title_3 || "",
        text3: ms.memo_text_3 || "",
      });
      setParamModal({ type: "memo", mode: m, action });
      return;
    }
    if (m === "MY_QUOTE") {
      const savedOv = (modeOverrides[m] || {}) as Record<string, unknown>;
      setQuoteDraft(typeof savedOv.quote === "string" ? savedOv.quote : "");
      setAuthorDraft(typeof savedOv.author === "string" ? savedOv.author : "");
      setParamModal({ type: "quote", mode: m, action });
      return;
    }
    if (m === "COUNTDOWN") {
      setParamModal({ type: "countdown", mode: m, action });
      return;
    }
    if (m === "HABIT") {
      const savedOv = modeOverrides["HABIT"] || {};
      const savedItems = Array.isArray(savedOv.habitItems) ? (savedOv.habitItems as Array<{ name: string; done?: boolean }>) : null;
      if (savedItems && savedItems.length > 0) {
        setHabitItems(savedItems.map((h) => ({ name: h.name, done: h.done ?? false })));
      }
      setParamModal({ type: "habit", mode: m, action });
      return;
    }
    if (m === "LIFEBAR") {
      const savedOv = (modeOverrides[m] || {}) as Record<string, unknown>;
      const savedAge = Number(savedOv.age);
      const savedExpect = Number(savedOv.life_expect);
      if (Number.isFinite(savedAge) && savedAge > 0) {
        setUserAge(savedAge);
      }
      if ([80, 90, 100, 120].includes(savedExpect)) {
        setLifeExpectancy(savedExpect);
      }
      setParamModal({ type: "lifebar", mode: m, action });
      return;
    }
    if (m === "CALENDAR") {
      setParamModal({ type: "calendar", mode: m, action });
      return;
    }
    if (m === "TIMETABLE") {
      const existing = (modeOverrides[m] || {}) as Record<string, unknown>;
      if (existing.periods && existing.courses) {
        setTimetableData({
          style: (existing.style as "daily" | "weekly") || "daily",
          weekdays: Array.isArray(existing.weekdays) ? existing.weekdays as string[] : undefined,
          periods: existing.periods as string[],
          courses: existing.courses as Record<string, string>,
        });
      }
      setParamModal({ type: "timetable", mode: m, action });
      return;
    }
    if (m === "VOCAB_REVIEW") {
      const ov = modeOverrides[m] || {};
      setVocabDeckId(String(ov.deck_id || "core_en"));
      setVocabDailyLimit(Number(ov.daily_limit || 30));
      setParamModal({ type: "vocab", mode: m, action });
      return;
    }
  }, [modeLocationItems, modeOverrides, modeNeedsLocation]);



  // 配置落库的唯一出口：页面级「保存配置」、偏好保存、弹窗「保存并预览」共用同一条路径。
  // 2026-10-31 修：此前弹窗的「保存并预览」只改 React state，从不 POST /api/config，
  // 预览看着对、刷新就没 —— 诗笺日课双城保存不进去就是这个原因。
  const buildConfigPayload = useCallback((
    overrides?: Record<string, ModeOverride>,
    modes?: Set<string>,
  ) => {
    const source = overrides ?? modeOverrides;
    const normalizedModeOverrides = Object.fromEntries(
      Object.entries(source)
        .map(([modeId, ov]) => [modeId.toUpperCase(), sanitizeModeOverride(ov)] as const)
        .filter(([, ov]) => Object.keys(ov).length > 0)
    );
    return {
      mac,
      modes: Array.from(modes ?? selectedModes),
      refreshStrategy: strategy,
      refreshInterval: refreshMin,
      ...currentLocation,
      modeLanguage,
      contentTone,
      characterTones: characterTones,
      modeOverrides: normalizedModeOverrides,
      memoText: memoText,
      is_focus_listening: isFocusListening,
      always_active: alwaysActive,
      timeSlotRules: timeSlotRules,
    };
  }, [alwaysActive, characterTones, contentTone, currentLocation, isFocusListening, mac, memoText, modeLanguage, modeOverrides, refreshMin, sanitizeModeOverride, selectedModes, strategy, timeSlotRules]);

  const persistConfig = useCallback(async (
    overrides?: Record<string, ModeOverride>,
    modes?: Set<string>,
  ) => {
    if (!mac) throw new Error("缺少设备 MAC");
    const body = buildConfigPayload(overrides, modes);
    const res = await fetch(apiUrl("/api/config"), {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("保存失败");
    return body;
  }, [buildConfigPayload]);

  const handleSave = async () => {
    if (!mac) { showToast("请先完成刷机和配网以获取设备 MAC", "error"); return; }
    if (macAccessDenied) { showToast("你无权配置该设备", "error"); return; }
    setSaving(true);
    try {
      await persistConfig();
      let onlineNow = isOnline;
      let refreshQueued = false;
      let latestLastSeen: string | null = lastSeen;
      const syncResult = await queueImmediateRefreshIfOnline(fetch, mac, authHeaders());
      onlineNow = syncResult.onlineNow ?? isOnline;
      refreshQueued = syncResult.refreshQueued;
      latestLastSeen = syncResult.lastSeen;
      if (syncResult.onlineNow !== null) {
        setIsOnline(syncResult.onlineNow);
      }
      setLastSeen(latestLastSeen);
      showToast(
        syncResult.onlineNow === null
          ? "配置已保存，暂时无法确认设备状态"
          : onlineNow
            ? (refreshQueued
                ? "配置已保存，已通知在线设备尽快同步"
                : "配置已保存，设备在线，但同步通知失败")
            : "配置已保存，设备当前不在活跃同步状态，将在设备下次上线后生效",
        syncResult.onlineNow === null || !refreshQueued ? "info" : "success",
      );
      setPreviewNoCacheOnce(true);
    } catch {
      showToast("保存失败", "error");
    } finally {
      setSaving(false);
    }
  };

  const [savingPrefs, setSavingPrefs] = useState(false);
  const handleSavePreferences = async () => {
    if (!mac) { showToast("请先完成刷机和配网以获取设备 MAC", "error"); return; }
    if (macAccessDenied) { showToast("你无权配置该设备", "error"); return; }
    setSavingPrefs(true);
    try {
      await persistConfig();
      showToast("配置已保存", "success");
      setPreviewNoCacheOnce(true);
    } catch {
      showToast("保存失败", "error");
    } finally {
      setSavingPrefs(false);
    }
  };

  const handleToggleFocusListening = useCallback(async () => {
    if (!mac) return;
    const next = !isFocusListening;
    setFocusToggleLoading(true);
    try {
      const res = await fetch(apiUrl(
        `/api/config/${encodeURIComponent(mac)}/focus-listening?enabled=${next}`),
        { method: "PATCH", headers: authHeaders() },
      );
      if (!res.ok) {
        const errorText = await res.text().catch(() => "");
        console.error("[FOCUS] Toggle failed:", res.status, errorText);
        throw new Error("focus-toggle-failed");
      }
      const data = (await res.json().catch(() => ({}))) as { alert_token?: string | null };
      setIsFocusListening(next);
      if (next && data?.alert_token) {
        setFocusAlertToken(data.alert_token);
        setShowFocusTokenModal(true);
      }
      showToast(
        next
          ? "Focus 专注模式就绪，OpenCLAW 守护中"
          : "专注监听已关闭，设备将按原计划轮播内容",
        "success",
      );
    } catch (err) {
      console.error("[FOCUS] Toggle error:", err);
      showToast("切换专注监听失败，请稍后重试", "error");
    } finally {
      setFocusToggleLoading(false);
    }
  }, [isFocusListening, mac, showToast]);

  const buildPreviewParams = useCallback((mode?: string, forceNoCache = false, forcedModeOverride?: ModeOverride, clearSavedOverride = false) => {
    const m = mode || previewMode;
    const consumeNoCacheOnce = previewNoCacheOnce;
    const forceFresh = forceNoCache || consumeNoCacheOnce;
    const params = new URLSearchParams({ persona: m });
    if (mac) params.set("mac", mac);
    if (clearSavedOverride && m === "COUNTDOWN") {
      params.set("mode_override", JSON.stringify({ countdownEvents: [] }));
      if (previewColors > 2) params.set("colors", String(previewColors));
      params.set("w", String(previewWidth));
      params.set("h", String(previewHeight));
      if (forceFresh) params.set("no_cache", "1");
      return { m, params, consumeNoCacheOnce };
    }
    const modeOverrideSource = sanitizeModeOverride(
      clearSavedOverride
        ? {
            ...(m === "COUNTDOWN" ? { countdownEvents: [] } : {}),
            ...(forcedModeOverride || {}),
          }
        : {
            ...(modeOverrides[m] || {}),
            ...(forcedModeOverride || {}),
          }
    );
    const savedOverrides = (config.mode_overrides || config.modeOverrides || {}) as Record<string, ModeOverride>;
    const effectiveLocation = cleanLocationValue(
      modeOverrideSource.city
        ? extractLocationValue(modeOverrideSource as Record<string, unknown>)
        : currentLocation,
    );
    const savedEffectiveLocation = cleanLocationValue(
      savedOverrides[m]?.city
        ? extractLocationValue(savedOverrides[m] as Record<string, unknown>)
        : extractLocationValue(config as Record<string, unknown>),
    );
    const locationChanged = Boolean(effectiveLocation.city) && !locationsEqual(effectiveLocation, savedEffectiveLocation);

    const activeModeOverride = sanitizeModeOverride({
      ...modeOverrideSource,
      ...(locationChanged ? effectiveLocation : {}),
    });
    if (!clearSavedOverride && m === "MEMO" && memoText.trim() && !("memo_text" in activeModeOverride)) {
      activeModeOverride.memo_text = memoText.trim();
    }
    const hasModeOverride = Object.keys(activeModeOverride).length > 0;
    if (hasModeOverride) {
      params.set("mode_override", JSON.stringify(activeModeOverride));
    }
    if (m === "MEMO") {
      const memoCandidate = (
        typeof forcedModeOverride?.memo_text === "string" && forcedModeOverride.memo_text.trim()
          ? forcedModeOverride.memo_text
          : typeof activeModeOverride.memo_text === "string" && activeModeOverride.memo_text.trim()
          ? activeModeOverride.memo_text
          : memoText
      ).trim();
      if (memoCandidate) {
        params.set("memo_text", memoCandidate);
      }
      // 传递 3 组标题+内容字段
      const ms = (forcedModeOverride?.mode_settings || activeModeOverride.mode_settings || {}) as Record<string, string>;
      for (const k of Object.keys(ms)) {
        if ((k.startsWith("memo_title_") || k.startsWith("memo_text_")) && ms[k]) {
          params.set(k, ms[k]);
        }
      }
    }
    if (locationChanged && effectiveLocation.city) params.set("city_override", effectiveLocation.city);
    if (previewColors > 2) params.set("colors", String(previewColors));
    params.set("w", String(previewWidth));
    params.set("h", String(previewHeight));
    if (forceFresh || locationChanged || hasModeOverride) params.set("no_cache", "1");
    return { m, params, consumeNoCacheOnce };
  }, [config, currentLocation, mac, memoText, modeOverrides, previewColors, previewWidth, previewHeight, previewMode, previewNoCacheOnce, sanitizeModeOverride]);

  const ownerUsername = useMemo(
    () => deviceMembers.find((member) => member.role === "owner")?.username || "",
    [deviceMembers],
  );

  const handlePreview = useCallback(async (mode?: string, forceNoCache = false, forcedModeOverride?: ModeOverride, confirmed = false, clearSavedOverride = false) => {
    const { m, params, consumeNoCacheOnce } = buildPreviewParams(mode, forceNoCache, forcedModeOverride, clearSavedOverride);
    if (!m) return;

    if (mac && !confirmed) {
      try {
        const intentParams = new URLSearchParams(params);
        intentParams.set("intent", "1");
        const intentRes = await fetch(apiUrl(`/api/preview?${intentParams.toString()}`), {
          cache: "no-store",
          headers: authHeaders(),
        });
        if (intentRes.ok) {
          const intentData = (await intentRes.json()) as {
            cache_hit?: boolean;
            llm_required?: boolean;
          };
          if (!intentData.cache_hit && intentData.llm_required) {
            setPreviewConfirm({
              mode: m,
              forceNoCache,
              forcedModeOverride,
            });
            return;
          }
        }
      } catch {}
    }

    setPreviewConfirm(null);
    setPreviewCacheHit(null);
    setPreviewLlmStatus(null);
    setPreviewLoading(true);
    setPreviewStatusText("正在生成...");
    try {
      previewStreamRef.current?.close();
      const stream = new EventSource(apiUrl(`/api/preview/stream?${params.toString()}`));
      previewStreamRef.current = stream;

      await new Promise<void>((resolve, reject) => {
        let settled = false;
        stream.addEventListener("status", (event) => {
          try {
            const data = JSON.parse((event as MessageEvent<string>).data) as { message?: string };
            setPreviewStatusText(data.message || "正在生成...");
          } catch {
            setPreviewStatusText("正在生成...");
          }
        });

        stream.addEventListener("error", (event) => {
          if (settled) return;
          try {
            const data = JSON.parse((event as MessageEvent<string>).data) as {
              error?: string;
              message?: string;
            };
            settled = true;
            stream.close();
            previewStreamRef.current = null;
            setPreviewLoading(false);
            reject(new Error(data.message || "预览失败"));
          } catch {
            settled = true;
            stream.close();
            previewStreamRef.current = null;
            setPreviewLoading(false);
            reject(new Error("Preview failed"));
          }
        });

        stream.addEventListener("result", async (event) => {
          try {
            const data = JSON.parse((event as MessageEvent<string>).data) as {
              message?: string;
              image_url?: string;
              cache_hit?: boolean;
              preview_status?: string;
              llm_required?: boolean;
            };
            console.log("[PREVIEW] Result event received:", { hasImageUrl: !!data.image_url, message: data.message });
            if (!data.image_url) {
              settled = true;
              console.error("[PREVIEW] Missing image_url in result event");
              setPreviewLoading(false);
              reject(new Error("Preview image missing"));
              return;
            }
            settled = true;
            const imageResponse = await fetch(data.image_url, { cache: "no-store" });
            if (!imageResponse.ok) {
              setPreviewLoading(false);
              reject(new Error("Preview image unavailable"));
              return;
            }
            const imageBlob = await imageResponse.blob();
            const objectUrl = URL.createObjectURL(imageBlob);
            console.log("[PREVIEW] Setting preview image:", data.image_url.substring(0, 50) + "...");
            replacePreviewImg(objectUrl);
            setPreviewCacheHit(typeof data.cache_hit === "boolean" ? data.cache_hit : null);
            setPreviewStatusText(data.message || "完成");
            const status = (data.preview_status || "").toLowerCase();
            const llmRequired = data.llm_required;
            if (status === "no_llm_required" || llmRequired === false) {
              setPreviewLlmStatus(null);
            } else if (status === "model_generated") {
              setPreviewLlmStatus("大模型调用成功");
            } else if (status === "fallback_used") {
              setPreviewLlmStatus("大模型调用失败，使用默认内容");
            } else {
              setPreviewLlmStatus(null);
            }
            setPreviewLoading(false); // 重置加载状态
            stream.close();
            previewStreamRef.current = null;
            resolve();
          } catch (error) {
            console.error("[PREVIEW] Error processing result event:", error);
            setPreviewLoading(false);
            reject(error);
          }
        });

        stream.onerror = () => {
          if (settled) return;
          settled = true;
          stream.close();
          previewStreamRef.current = null;
          setPreviewLoading(false);
          reject(new Error("Preview failed"));
        };
      });
    } catch {
      showToast("预览失败", "error");
      setPreviewCacheHit(null);
      setPreviewStatusText("");
    } finally {
      setPreviewLoading(false);
      if (consumeNoCacheOnce) setPreviewNoCacheOnce(false);
    }
  }, [buildPreviewParams, mac, replacePreviewImg, showToast]);

  const loadStats = useCallback(async () => {
    if (!mac) return;
    try {
      const res = await fetch(apiUrl(`/api/stats/${encodeURIComponent(mac)}`), { headers: authHeaders() });
      if (res.ok) setStats(await res.json());
    } catch {}
  }, [mac]);

  const loadFavorites = useCallback(async (force = false) => {
    if (!mac) return;
    if (!force && favoritesLoadedMacRef.current === mac) return;
    try {
      const res = await fetch(apiUrl(`/api/device/${encodeURIComponent(mac)}/favorites?limit=100`), { headers: authHeaders() });
      if (res.status === 401 || res.status === 403) {
        setMacAccessDenied(true);
        return;
      }
      if (!res.ok) return;
      const data = await res.json();
      const modes = new Set<string>(
        (data.favorites || [])
          .map((item: { mode_id?: string }) => (item.mode_id || "").toUpperCase())
          .filter((modeId: string) => modeId.length > 0),
      );
      setFavoritedModes(modes);
      favoritesLoadedMacRef.current = mac;
    } catch {}
  }, [mac]);

  const loadRuntimeMode = useCallback(async () => {
    if (!mac) return;
    try {
      const res = await fetch(apiUrl(`/api/device/${encodeURIComponent(mac)}/state`), { cache: "no-store", headers: authHeaders() });
      if (res.status === 401 || res.status === 403) {
        setMacAccessDenied(true);
        return;
      }
      if (!res.ok) return;
      const data = await res.json();
      setIsOnline(Boolean(data?.is_online));
      setLastSeen(typeof data?.last_seen === "string" && data.last_seen ? data.last_seen : null);
      const mode = data?.runtime_mode;
      if (mode === "active" || mode === "interval") {
        setRuntimeMode(mode);
      } else {
        setRuntimeMode("interval");
      }
    } catch {
      setIsOnline(false);
      setLastSeen(null);
      setRuntimeMode("interval");
    }
  }, [mac]);

  useEffect(() => {
    if (activeTab === "stats" && mac) loadStats();
  }, [activeTab, mac, loadStats]);

  useEffect(() => {
    if (!mac) return;
    favoritesLoadedMacRef.current = "";
    loadFavorites();
  }, [mac, loadFavorites]);

  useEffect(() => {
    if (!mac) return;
    loadRuntimeMode();
  }, [mac, loadRuntimeMode]);

  useEffect(() => {
    return () => {
      previewStreamRef.current?.close();
      previewStreamRef.current = null;
    };
  }, []);

  const handleGenerateMode = async () => {
    if (!customDesc.trim()) { showToast("请输入模式描述", "error"); return; }
    setCustomGenerating(true);
    try {
      const res = await fetch(apiUrl("/api/modes/generate"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          description: customDesc, 
          provider: "deepseek", 
          model: "deepseek-chat",
          mac: mac || undefined,
        }),
      });

      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "生成失败");
      setCustomJson(JSON.stringify(data.mode_def, null, 2));
      setCustomJsonError(null);
      setCustomModeName((data.mode_def?.display_name || "").toString());
      setCustomModeDescription((data.mode_def?.description || "").toString());
      setCustomEditorSource("ai");
      setCustomEditorTab("composer");
      syncComposerFromModeDef(data.mode_def);
      showToast("模式生成成功", "success");

      // Close modal right after generation, then start preview on the right panel
      const finalName = (customModeName || data.mode_def?.display_name || "").toString().trim();
      setPreviewModeLabelOverride(finalName || null);
      setEditingCustomMode(false);
      requestAnimationFrame(() => {
        previewPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });

      // show "previewing" on the right panel while preview is being built
      setPreviewLoading(true);
      setPreviewStatusText("模式预览中...");

      await handleCustomPreview(data.mode_def);
    } catch (e) {
      showToast(`${"生成失败"}: ${e instanceof Error ? e.message : "未知错误"}`, "error");
    } finally {
      setCustomGenerating(false);
    }
  };

  const handleCustomPreview = async (defOverride?: unknown) => {
    if (!defOverride && !customJson.trim()) return;
    setCustomPreviewLoading(true);
    setPreviewLoading(true);
    if (!previewStatusText) setPreviewStatusText("模式预览中...");
    try {
      const def = defOverride ? (defOverride as Record<string, unknown>) : (JSON.parse(customJson) as Record<string, unknown>);
      if (customModeName.trim()) {
        (def as Record<string, unknown>).display_name = customModeName.trim();
      }
      if (customModeDescription.trim()) {
        (def as Record<string, unknown>).description = customModeDescription.trim();
      }
      const modeIdRaw = (def as Record<string, unknown>).mode_id;
      if (typeof modeIdRaw !== "string" || !modeIdRaw.trim()) {
        (def as Record<string, unknown>).mode_id = normalizeModeIdFromName(customModeName || "Custom Preview");
      }
      let modeHint = "CUSTOM_PREVIEW";
      try {
        if (customModeName.trim()) {
          modeHint = customModeName.trim().toUpperCase().replace(/[^A-Z0-9_]/g, "_");
        } else {
          const modeIdRaw = (def as Record<string, unknown>)["mode_id"];
          if (typeof modeIdRaw === "string" && modeIdRaw.trim()) {
            modeHint = modeIdRaw.trim().toUpperCase();
          }
        }
      } catch {}
      const res = await fetch(apiUrl("/api/modes/custom/preview"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode_def: def, mac: mac || undefined, colors: previewColors, w: previewWidth, h: previewHeight }),
      });

      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.error || "预览失败");
      }
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      if (customPreviewImg) {
        try { URL.revokeObjectURL(customPreviewImg); } catch {}
      }
      setCustomPreviewImg(objectUrl);

      // show custom-mode preview in the right E-Ink panel
      setPreviewMode(modeHint);
      setPreviewCacheHit(null);
      const status = (res.headers.get("x-preview-status") || "").toLowerCase();
      const llmRequiredHeader = (res.headers.get("x-llm-required") || "").toLowerCase();
      if (status === "no_llm_required" || llmRequiredHeader === "0" || llmRequiredHeader === "false") {
        setPreviewLlmStatus(null);
      } else if (status === "model_generated") {
        setPreviewLlmStatus("大模型调用成功");
      } else if (status === "fallback_used") {
        setPreviewLlmStatus("大模型调用失败，使用默认内容");
      } else {
        setPreviewLlmStatus(null);
      }
      replacePreviewImg(objectUrl);
    } catch (e) {
      if (e instanceof SyntaxError) {
        setCustomJsonError(e.message);
      }
      showToast(`${"预览失败"}: ${e instanceof Error ? e.message : ""}`, "error");
    } finally {
      setCustomPreviewLoading(false);
      setPreviewLoading(false);
    }
  };

  const handleSaveCustomMode = async () => {
    if (!customJson.trim()) return;
    if (!mac) {
      showToast("请先选择设备", "error");
      return;
    }
    try {
      const def = JSON.parse(customJson);
      
      // Ensure mode_id exists - generate from display_name if missing
      if (!def.mode_id || !def.mode_id.trim()) {
        if (customModeName.trim()) {
          def.mode_id = customModeName.trim().toUpperCase().replace(/[^A-Z0-9_]/g, "_");
          // Ensure it starts with a letter
          if (!/^[A-Z]/.test(def.mode_id)) {
            def.mode_id = "CUSTOM_" + def.mode_id;
          }
        } else if (def.display_name) {
          def.mode_id = def.display_name.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
          if (!/^[A-Z]/.test(def.mode_id)) {
            def.mode_id = "CUSTOM_" + def.mode_id;
          }
        } else {
          // Generate a random mode_id if no name is available
          def.mode_id = "CUSTOM_" + Math.random().toString(36).substring(2, 10).toUpperCase();
        }
      }
      
      if (customModeName.trim()) {
        def.display_name = customModeName.trim();
      }
      if (customModeDescription.trim()) {
        def.description = customModeDescription.trim();
      }
      
      // Add mac to the request body
      def.mac = mac;
      
      const res = await fetch(apiUrl("/api/modes/custom"), {
        method: "POST",
        headers: { 
          "Content-Type": "application/json",
          ...authHeaders(),
        },
        body: JSON.stringify(def),
      });
      
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.error || `${"保存失败"}: ${res.status}`);
      }
      
      const data = await res.json();
      if (data.ok || data.status === "ok") {
        showToast(`${"模式"} ${def.mode_id} ${"已保存"}`, "success");
        // Refresh catalog (modes + categories + settings schema)
        refreshCatalog();
        setEditingCustomMode(false);
        setCustomJson("");
        setCustomDesc("");
        setCustomModeName("");
        setCustomModeDescription("");
        if (customPreviewImg) {
          try { URL.revokeObjectURL(customPreviewImg); } catch {}
        }
        setCustomPreviewImg(null);
        setCustomEditorSource(null);
      } else {
        throw new Error(data.error || "保存失败");
      }
    } catch (e) {
      if (e instanceof SyntaxError) {
        setCustomJsonError(e.message);
      }
      showToast(`${"保存失败"}: ${e instanceof Error ? e.message : ""}`, "error");
    }
  };

  const toggleMode = useCallback((modeId: string) => {
    setSelectedModes((prev) => {
      const next = new Set(prev);
      if (next.has(modeId)) next.delete(modeId);
      else next.add(modeId);
      return next;
    });
  }, []);

  const handleModePreview = (m: string) => {
    const modeId = (m || "").toUpperCase();
    setPreviewMode(modeId);
    if (modeId === "MY_ADAPTIVE") {
      setAdaptiveModal({ action: "preview" });
      return;
    }
    if (modeId === "CUSTOM_ALBUM") {
      setAlbumModal({ action: "preview" });
      void loadAlbumPhotos();
      return;
    }
    if (requiresParamModal(modeId)) {
      openParamModal(modeId, "preview");
      return;
    }
    // Config page preview should bypass cache so it reflects latest overrides.
    handlePreview(modeId, true);
  };

  const handleModeApply = async (m: string) => {
    const modeId = (m || "").toUpperCase();
    if (modeId === "MY_ADAPTIVE" && !selectedModes.has(modeId)) {
      setAdaptiveModal({ action: "apply" });
      return;
    }
    if (modeId === "CUSTOM_ALBUM" && !selectedModes.has(modeId)) {
      setAlbumModal({ action: "apply" });
      void loadAlbumPhotos();
      return;
    }
    if (requiresParamModal(modeId) && !selectedModes.has(modeId)) {
      // only require params when adding to carousel
      openParamModal(modeId, "apply");
      return;
    }
    const wasSelected = selectedModes.has(modeId);
    toggleMode(modeId);
    showToast(
      wasSelected
        ? "已从轮播移除"
        : "已加入轮播",
      "success",
    );
  };

  const handleCustomModeDelete = async (m: string) => {
    const modeId = (m || "").toUpperCase();
    if (!mac) {
      showToast("请先选择设备", "error");
      return;
    }
    const confirmed = window.confirm(
      `确定删除自定义模式 ${modeId} 吗？`,
    );
    if (!confirmed) return;
    try {
      const res = await fetch(apiUrl(
        `/api/modes/custom/${encodeURIComponent(modeId)}?mac=${encodeURIComponent(mac)}`),
        {
          method: "DELETE",
          headers: authHeaders(),
        },
      );
      const data = await res.json().catch(() => ({} as { error?: string }));
      if (!res.ok) {
        throw new Error(data.error || "删除失败");
      }
      setSelectedModes((prev) => {
        const next = new Set(prev);
        next.delete(modeId);
        return next;
      });
      setModeOverrides((prev) => {
        const copied = { ...prev };
        delete copied[modeId];
        return copied;
      });
      if (previewMode === modeId) {
        setPreviewMode("");
      }
      refreshCatalog();
      showToast("自定义模式已删除", "success");
    } catch (e) {
      showToast(`${"删除失败"}: ${e instanceof Error ? e.message : ""}`, "error");
    }
  };

  // persist=true 的分支承诺的是「保存并预览」——那就必须真的落库。
  // 2026-10-31 修：此前所有弹窗分支都只改 React state，值活不过刷新，
  // 诗笺日课双城因此存不进去（DB 里 CUSTOM_POETRY_DAY 从未出现过）。
  const commitModalAction = useCallback(async (
    modeId: string,
    action: "preview" | "apply",
    forcedOverride?: ModeOverride,
    clearSavedOverride = false,
  ) => {
    setParamModal(null);

    // setState 拿不到同步结果，落库必须用自己算出来的最终快照。
    let nextOverrides = modeOverrides;
    let nextModes = selectedModes;
    let hasChange = false;

    if (clearSavedOverride) {
      const copied = { ...modeOverrides };
      delete copied[modeId];
      nextOverrides = copied;
      hasChange = true;
    } else if (forcedOverride && Object.keys(forcedOverride).length > 0) {
      let patch = forcedOverride;
      if (modeId === "MEMO") {
        const ms = (patch.mode_settings || {}) as Record<string, string>;
        if (!ms.memo_title_1 && !ms.memo_text_1) {
          // 新格式字段直接在 override 顶层
          const newMs: Record<string, string> = {};
          for (const k of Object.keys(patch)) {
            if (k.startsWith("memo_title_") || k.startsWith("memo_text_")) {
              newMs[k] = String(patch[k]);
            }
          }
          if (Object.keys(newMs).length > 0) {
            patch = { ...patch, mode_settings: newMs };
          }
        }
      }
      const merged = sanitizeModeOverride({ ...(modeOverrides[modeId] || {}), ...patch });
      if (Object.keys(merged).length > 0) {
        nextOverrides = { ...modeOverrides, [modeId]: merged };
      } else {
        const copied = { ...modeOverrides };
        delete copied[modeId];
        nextOverrides = copied;
      }
      hasChange = true;
    }

    const joining = action === "apply" && !selectedModes.has(modeId);
    if (joining) {
      nextModes = new Set([...selectedModes, modeId]);
    }

    if (hasChange) {
      setModeOverrides(nextOverrides);
    }
    if (joining) {
      toggleMode(modeId);
    }

    let saved = false;
    if (hasChange) {
      if (!mac || macAccessDenied) {
        showToast("设置未保存：缺少设备 MAC 或无权限", "error");
      } else {
        try {
          await persistConfig(nextOverrides, nextModes);
          saved = true;
          void queueImmediateRefreshIfOnline(fetch, mac, authHeaders());
        } catch {
          showToast("设置保存失败", "error");
          return;
        }
      }
    }

    setPreviewMode(modeId);
    await handlePreview(modeId, true, forcedOverride, false, clearSavedOverride);

    if (saved) {
      showToast(joining ? "设置已保存，已加入轮播" : "设置已保存", "success");
    } else if (joining) {
      showToast("已加入轮播", "success");
    }
  }, [handlePreview, mac, macAccessDenied, modeOverrides, persistConfig, sanitizeModeOverride, selectedModes, showToast, toggleMode]);

  const saveVocabReviewSettings = useCallback(async (modeId: string, override: ModeOverride) => {
    if (!mac) {
      showToast("请先完成刷机和配网以获取设备 MAC", "error");
      return;
    }
    if (macAccessDenied) {
      showToast("你无权配置该设备", "error");
      return;
    }

    const normalizedModeId = modeId.toUpperCase();
    const nextModeOverrides = {
      ...modeOverrides,
      [normalizedModeId]: sanitizeModeOverride({
        ...(modeOverrides[normalizedModeId] || {}),
        ...override,
      }),
    };
    const normalizedModeOverrides: Record<string, ModeOverride> = Object.fromEntries(
      Object.entries(nextModeOverrides)
        .map(([id, ov]) => [id.toUpperCase(), sanitizeModeOverride(ov)] as const)
        .filter(([, ov]) => Object.keys(ov).length > 0),
    );

    setSaving(true);
    try {
      await persistConfig(normalizedModeOverrides);
      setModeOverrides(normalizedModeOverrides);
      setParamModal(null);
      showToast("背单词设置已保存", "success");
    } catch {
      showToast("保存失败", "error");
    } finally {
      setSaving(false);
    }
  }, [
    alwaysActive,
    characterTones,
    contentTone,
    currentLocation,
    isFocusListening,
    mac,
    macAccessDenied,
    memoText,
    modeLanguage,
    modeOverrides,
    refreshMin,
    sanitizeModeOverride,
    selectedModes,
    showToast,
    strategy,
    timeSlotRules,
  ]);

  const handleApplyPreviewToScreen = async () => {
    if (!mac || !previewMode || !previewImg) return;
    setApplyToScreenLoading(true);
    try {
      const stateRes = await fetch(apiUrl(`/api/device/${encodeURIComponent(mac)}/state`), { cache: "no-store", headers: authHeaders() });
      if (!stateRes.ok) {
        showToast("无法确认设备状态，已阻止发送", "error");
        return;
      }
      const stateData = await stateRes.json();
      const mode = stateData?.runtime_mode;
      if (mode === "active" || mode === "interval") {
        setRuntimeMode(mode);
      }
      if (mode !== "active") {
        showToast("设备处于间歇状态，不可发送", "error");
        return;
      }

      const previewResponse = await fetch(previewImg);
      if (!previewResponse.ok) throw new Error("preview image unavailable");
      const previewBlob = await previewResponse.blob();

      const qs = new URLSearchParams();
      qs.set("mode", previewMode);
      const res = await fetch(apiUrl(`/api/device/${encodeURIComponent(mac)}/apply-preview?${qs.toString()}`), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "image/png" }),
        body: previewBlob,
      });
      if (!res.ok) throw new Error("apply-preview failed");
      setCurrentMode(previewMode);
      await loadRuntimeMode();
      showToast("已下发到墨水屏", "success");
    } catch {
      showToast("下发失败", "error");
    } finally {
      setApplyToScreenLoading(false);
    }
  };

  const handleAddCustomPersona = () => {
    const v = customPersonaTone.trim();
    if (!v) return;
    setCharacterTones((prev) => (prev.includes(v) ? prev : [...prev, v]));
    setCustomPersonaTone("");
  };

  const modeMeta = useMemo(() => {
    const map: Record<string, { name: string; tip: string }> = {};
    for (const item of catalogItems) {
      const mid = (item.mode_id || "").toUpperCase();
      if (!mid) continue;
      const lang = item.i18n?.zh;
      const name = (lang?.name && String(lang.name)) || (item.display_name && String(item.display_name)) || mid;
      const tip = (lang?.tip && String(lang.tip)) || (item.description && String(item.description)) || "";
      map[mid] = { name, tip };
    }
    return map;
  }, [catalogItems, false]);

  const coreModes = useMemo(
    () => catalogItems.filter((m) => m.category === "core").map((m) => m.mode_id.toUpperCase()),
    [catalogItems],
  );
  const extraModes = useMemo(
    () => catalogItems.filter((m) => m.category === "more").map((m) => m.mode_id.toUpperCase()),
    [catalogItems],
  );
  const customModes = useMemo(
    () => catalogItems.filter((m) => m.category === "custom").map((m) => m.mode_id.toUpperCase()),
    [catalogItems],
  );
  // 只有用户自建的模式可删除；内置目录项（相册、相框）不显示删除按钮
  const deletableCustomModes = useMemo(
    () =>
      catalogItems
        .filter((m) => m.category === "custom" && m.source === "custom")
        .map((m) => m.mode_id.toUpperCase()),
    [catalogItems],
  );
  const customModeMeta = useMemo(
    () =>
      Object.fromEntries(
        catalogItems
          .filter((m) => m.category === "custom")
          .map((m) => {
            const lang = m.i18n?.zh;
            return [
              m.mode_id.toUpperCase(),
              {
                name: (lang?.name && String(lang.name)) || m.display_name || m.mode_id,
                tip: (lang?.tip && String(lang.tip)) || m.description || "",
              },
            ];
          }),
      ),
    [catalogItems, false],
  );

  const batteryPct = calculateBatteryPct(stats?.last_battery_voltage);
  const currentDeviceMembership = userDevices.find((d) => d.mac.toUpperCase() === mac.toUpperCase()) || null;
  const denyByMembership = Boolean(mac && currentUser && !devicesLoading && !currentDeviceMembership);
  const currentUserRole = currentDeviceMembership?.role || "";
  const statusLabel = !isOnline
    ? "离线"
    : runtimeMode === "active"
    ? "活跃状态"
    : "间歇状态";
  const statusClass = !isOnline
    ? "bg-paper-dark text-ink-light border border-ink/10"
    : runtimeMode === "active"
    ? "bg-green-50 text-green-700 border border-green-200"
    : "bg-amber-50 text-amber-700 border border-amber-200";
  const statusIconClass = !isOnline
    ? "text-ink-light"
    : runtimeMode === "active"
    ? "text-green-600"
    : "text-amber-600";
  const tabs = TABS;

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      {/* Hidden file picker for MY_ADAPTIVE multi-image upload */}
      <input
        ref={adaptiveFileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={async (e) => {
          const f = e.target.files?.[0] || null;
          e.currentTarget.value = "";
          if (!f) return;
          setAdaptiveUploading2(true);
          try {
            const url = await uploadLocalImage(f);
            setAdaptiveImageUrls((prev) => {
              if (prev.length >= 6) return prev;
              return [...prev, url];
            });
          } catch (err) {
            const msg = err instanceof Error ? err.message : "请选择一张本地图片";
            showToast(msg, "error");
          } finally {
            setAdaptiveUploading2(false);
          }
        }}
      />
      {/* Hidden file picker for album (CUSTOM_ALBUM) photo upload */}
      <input
        ref={albumFileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={async (e) => {
          const files = e.target.files;
          e.currentTarget.value = "";
          await uploadAlbumPhotos(files);
        }}
      />
      {/* Header */}
      <div className="mb-8">
        <h1 className="font-serif text-3xl font-bold text-ink mb-2">{"设备配置"}</h1>
        {currentUser === undefined ? (
          <div className="flex items-center gap-2 text-ink-light text-sm py-4">
            <Loader2 size={16} className="animate-spin" /> {"加载中..."}
          </div>
        ) : currentUser === null ? (
          <div className="flex items-start gap-2 p-3 rounded-sm border border-amber-200 bg-amber-50 text-sm text-amber-800">
            <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium">{"请先登录"}</p>
              <p className="text-xs mt-0.5">{mac ? "登录后才能配置设备。" : "登录后可以管理你的设备列表。"}</p>
              <Link href={`${withLocalePath(locale, "/login")}?next=${encodeURIComponent(nextConfigPath)}`}>
                <Button size="sm" className="mt-2">{"登录 / 注册"}</Button>
              </Link>
            </div>
          </div>
        ) : (macAccessDenied || denyByMembership) ? (
          <div className="flex items-start gap-2 p-3 rounded-sm border border-red-200 bg-red-50 text-sm text-red-800">
            <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium">{"无权访问该设备"}</p>
              <p className="text-xs mt-0.5">{"该设备未绑定到当前账号，或你不是被授权成员。"}</p>
              <Link href={withLocalePath(locale, "/config")}>
                <Button size="sm" variant="outline" className="mt-2">{"返回设备列表"}</Button>
              </Link>
            </div>
          </div>
        ) : mac ? (
          <DeviceInfo
            mac={mac}
            currentUserRole={currentUserRole}
            statusIconClass={statusIconClass}
            statusClass={statusClass}
            statusLabel={statusLabel}
            lastSeen={lastSeen}
            localeConfigPath={withLocalePath(locale, "/config")}
            isFocusListening={isFocusListening}
            onToggleFocus={handleToggleFocusListening}
            focusToggleLoading={focusToggleLoading}
          />
        ) : (
          <div className="space-y-4">
            {requestsLoading ? (
              <div className="flex items-center gap-2 text-ink-light text-sm py-2">
                <Loader2 size={16} className="animate-spin" /> {"加载待处理请求..."}
              </div>
            ) : pendingRequests.length > 0 ? (
              <div className="p-3 rounded-sm border border-amber-200 bg-amber-50">
                <p className="text-sm font-medium text-amber-900 mb-2">{"待你处理的绑定请求"}</p>
                <div className="space-y-2">
                  {pendingRequests.map((item) => (
                    <div key={item.id} className="flex items-center justify-between gap-3 text-sm">
                      <div>
                        <p className="font-medium text-amber-900">{item.requester_username}</p>
                        <p className="text-xs text-amber-800 font-mono">{item.mac}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button size="sm" variant="outline" onClick={() => handleRejectRequest(item.id)}>{"拒绝"}</Button>
                        <Button size="sm" onClick={() => handleApproveRequest(item.id)}>{"同意"}</Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="p-3 rounded-sm border border-ink/10 bg-paper">
              <p className="text-sm font-medium text-ink mb-2 flex items-center gap-1">
                <Monitor size={14} /> {"配对设备"}
              </p>
              <p className="text-xs text-ink-light mb-3">{"在设备配网页查看配对码，输入后即可认领或申请绑定设备。"}</p>
              <div className="flex gap-2 flex-wrap items-center">
                <input
                  value={pairCodeInput}
                  onChange={(e) => setPairCodeInput(e.target.value.toUpperCase())}
                  placeholder={"配对码"}
                  className="w-full sm:w-64 rounded-sm border border-ink/20 px-3 py-1.5 text-sm font-mono uppercase tracking-[0.2em]"
                />
                <Button size="sm" variant="outline" onClick={handlePairDevice} disabled={!pairCodeInput.trim() || pairingDevice}>
                  {pairingDevice ? <Loader2 size={14} className="animate-spin mr-1" /> : null}
                  {"立即配对"}
                </Button>
              </div>
            </div>

            <div className="p-3 rounded-sm border border-ink/10 bg-paper">
              <p className="text-sm font-medium text-ink mb-2 flex items-center gap-1">
                <Plus size={14} /> {"按 MAC 手动绑定"}
              </p>
              <p className="text-xs text-ink-light mb-3">{"请优先使用配对码配对。"}</p>
              <div className="flex gap-2 flex-wrap items-center">
                <input
                  value={bindMacInput}
                  onChange={(e) => setBindMacInput(e.target.value)}
                  placeholder={"MAC 地址 (如 AA:BB:CC:DD:EE:FF)"}
                  className="w-full sm:w-[360px] rounded-sm border border-ink/20 px-3 py-1.5 text-sm font-mono"
                />
                <input
                  value={bindNicknameInput}
                  onChange={(e) => setBindNicknameInput(e.target.value)}
                  placeholder={"别名（可选）"}
                  className="w-32 rounded-sm border border-ink/20 px-3 py-1.5 text-sm"
                />
                <Button size="sm" variant="outline" onClick={async () => {
                  const targetMac = bindMacInput.trim();
                  if (!targetMac) return;
                  const result = await handleBindDevice(targetMac, bindNicknameInput.trim());
                  if (!result) return;
                  if (result.status === "claimed" || result.status === "active") {
                    showToast("设备已绑定", "success");
                    window.location.href = `${siteUrl(withLocalePath(locale, "/config"))}?mac=${encodeURIComponent(targetMac)}`;
                    return;
                  }
                  if (result.status === "pending_approval") {
                    showToast("已提交绑定申请，等待 owner 同意", "info");
                  }
                }}>
                  {"绑定"}
                </Button>
              </div>
            </div>

            {/* Device list */}
            {devicesLoading ? (
              <div className="flex items-center gap-2 text-ink-light text-sm py-4">
                <Loader2 size={16} className="animate-spin" /> {"加载设备列表..."}
              </div>
            ) : userDevices.length > 0 ? (
              <div className="space-y-2">
                {userDevices.map((d) => (
                  <div key={d.mac} className="flex items-center justify-between p-3 rounded-sm border border-ink/10 bg-paper hover:border-ink/30 transition-colors">
                    <div className="flex items-center gap-3">
                      <Monitor size={18} className="text-ink-light" />
                      <div>
                        <p className="text-sm font-medium text-ink">
                          {d.nickname || d.mac}
                        </p>
                        {d.nickname && (
                          <p className="text-xs text-ink-light font-mono">{d.mac}</p>
                        )}
                        <p className="text-xs text-ink-light">
                          {"权限"}: {d.role === "owner" ? "Owner" : "Member"}
                        </p>
                        <p className="text-xs text-ink-light">
                          {d.last_seen
                            ? `${"上次在线"}: ${new Date(d.last_seen).toLocaleString("zh-CN")}`
                            : "尚未上线"}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Link href={`${withLocalePath(locale, "/config")}?mac=${encodeURIComponent(d.mac)}`}>
                        <Button size="sm" variant="outline">
                          <Settings size={14} className="mr-1" /> {"配置"}
                        </Button>
                      </Link>
                      <button
                        onClick={() => handleUnbindDevice(d.mac)}
                        className="p-1.5 text-ink-light hover:text-red-600 transition-colors"
                        title={"解绑设备"}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex items-start gap-2 p-3 rounded-sm border border-amber-200 bg-amber-50 text-sm text-amber-800">
                <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
                <div>
                  <p className="font-medium">{"未绑定设备"}</p>
                  <p className="text-xs mt-0.5">{"当前账号下还没有设备。"}</p>
                </div>
              </div>
            )}

          </div>
        )}
      </div>

      {mac && currentUser && !(macAccessDenied || denyByMembership) && loading && (
        <div className="flex items-center justify-center py-20 text-ink-light">
          <Loader2 size={24} className="animate-spin mr-2" /> {"加载配置中..."}
        </div>
      )}

      {mac && currentUser && !(macAccessDenied || denyByMembership) && !loading && (
        <div className="space-y-4">
          <div className="flex gap-6">
            {/* Sidebar tabs */}
            <nav className="w-44 flex-shrink-0 hidden md:block">
            <div className="sticky top-24 space-y-1">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-sm text-sm transition-colors ${
                    activeTab === tab.id
                      ? "bg-ink text-white font-medium"
                      : "text-ink-light hover:bg-paper-dark hover:text-ink"
                  }`}
                >
                  <tab.icon size={16} />
                  {tab.label}
                </button>
              ))}
              <div className="pt-4">
                <Button
                  variant="outline"
                  onClick={handleSave}
                  disabled={!mac || saving}
                  className="w-full bg-white text-ink border-ink/20 hover:bg-ink hover:text-white active:bg-ink active:text-white disabled:bg-white disabled:text-ink/50"
                >
                  {saving ? <Loader2 size={14} className="animate-spin mr-1" /> : <Save size={14} className="mr-1" />}
                  {"保存到设备"}
                </Button>
              </div>
            </div>
          </nav>

            {/* Mobile tabs */}
            <div className="md:hidden w-full mb-4 overflow-x-auto">
            <div className="flex gap-1 min-w-max pb-2">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`px-3 py-2 rounded-sm text-xs whitespace-nowrap transition-colors ${
                    activeTab === tab.id ? "bg-ink text-white" : "bg-paper-dark text-ink-light"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
            {/* Modes Tab */}
            {activeTab === "modes" && (
              <div className="space-y-6">
                <div className="grid grid-cols-1 lg:grid-cols-[520px_1fr] gap-6 items-start">
                  <ModeSelector
                    selectedModes={selectedModes}
                    customModes={customModes}
                    customModeMeta={customModeMeta}
                    deletableCustomModes={deletableCustomModes}
                    modeMeta={modeMeta}
                    coreModes={coreModes}
                    extraModes={extraModes}
                    handleModePreview={handleModePreview}
                    handleModeApply={handleModeApply}
                    handleCustomModeDelete={handleCustomModeDelete}
                    onCreateCustomMode={openCreateCustomMode}
                    previewColors={previewColors}
                    onColorsChange={setPreviewColors}
                    previewWidth={previewWidth}
                    previewHeight={previewHeight}
                    onScreenSizeChange={(w, h) => { setPreviewWidth(w); setPreviewHeight(h); }}
                  />

                  <div ref={previewPanelRef}>
                  <EInkPreviewPanel
                    screenW={previewWidth}
                    screenH={previewHeight}
                    previewModeLabel={
                      previewModeLabelOverride ||
                      (previewMode
                        ? (modeMeta[previewMode]?.name || customModeMeta[previewMode]?.name || previewMode)
                        : "请选择模式")
                    }
                    previewLoading={previewLoading}
                    previewStatusText={previewStatusText}
                    previewImg={previewImg}
                    previewCacheHit={previewCacheHit}
                    previewLlmStatus={previewLlmStatus}
                    canApplyToScreen={Boolean(mac && previewMode && previewImg)}
                    applyToScreenLoading={applyToScreenLoading}
                    onRegenerate={() => handlePreview(previewMode, true)}
                    onApplyToScreen={handleApplyPreviewToScreen}
                    rightActions={
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleSaveCustomMode}
                        disabled={!Boolean(customJson.trim())}
                        className="bg-white text-ink border-ink/20 hover:bg-ink hover:text-white active:bg-ink active:text-white disabled:bg-white disabled:text-ink/50"
                      >
                        {"保存模式"}
                      </Button>
                    }
                  />
                  </div>
                </div>

                <Dialog
                  open={editingCustomMode}
                  onClose={() => {
                    setEditingCustomMode(false);
                  }}
                >
                  <DialogContent className="max-w-5xl">
                    <DialogHeader
                      onClose={() => {
                        setEditingCustomMode(false);
                      }}
                    >
                      <div>
                        <DialogTitle>{"创建自定义模式"}</DialogTitle>
                        <DialogDescription>
                          {"用文字描述让 AI 帮你生成，或手动选择布局模板自由搭配。"}
                        </DialogDescription>
                      </div>
                    </DialogHeader>

                    <div className="space-y-4">
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <div>
                          <div className="text-xs text-ink-light mb-1">{"模式名称"}</div>
                          <input
                            value={customModeName}
                            onChange={(e) => setCustomModeName(e.target.value)}
                            placeholder={"模式名称（例如：今日英语）"}
                            className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                            disabled={customGenerating}
                          />
                        </div>
                        <div>
                          <div className="text-xs text-ink-light mb-1">{"模式简介"}</div>
                          <input
                            value={customModeDescription}
                            onChange={(e) => setCustomModeDescription(e.target.value)}
                            placeholder={"用于模式列表展示"}
                            className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                            disabled={customGenerating}
                          />
                        </div>
                      </div>

                      <div className="flex gap-2 border-b border-ink/10">
                        <button
                          type="button"
                          onClick={() => setCustomEditorTab("ai")}
                          className={`px-3 py-2 text-sm border-b-2 ${customEditorTab === "ai" ? "border-ink text-ink font-medium" : "border-transparent text-ink-light"}`}
                        >
                          {"描述生成"}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setCustomEditorTab("composer");
                            if (!customJson.trim()) {
                              setCustomEditorSource("composer");
                            }
                          }}
                          className={`px-3 py-2 text-sm border-b-2 ${customEditorTab === "composer" ? "border-ink text-ink font-medium" : "border-transparent text-ink-light"}`}
                        >
                          {"模板搭建"}
                        </button>
                      </div>

                      {customEditorTab === "ai" ? (
                        <div className="space-y-3">
                          {customGenerating ? (
                            <div className="rounded-sm border border-ink/10 bg-paper px-3 py-3 text-sm text-ink-light flex items-center gap-2">
                              <Loader2 size={16} className="animate-spin" />
                              {"模式生成中..."}
                            </div>
                          ) : null}
                          <textarea
                            value={customDesc}
                            onChange={(e) => setCustomDesc(e.target.value)}
                            rows={4}
                            maxLength={2000}
                            placeholder={"描述你想要的模式，如：每天显示一个英语单词和释义，单词要大号字体居中"}
                            className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm resize-y bg-white"
                            disabled={customGenerating}
                          />
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              onClick={() => {
                                void handleGenerateMode();
                              }}
                              disabled={customGenerating || !customDesc.trim()}
                            >
                              {"AI 生成预览"}
                            </Button>
                          </div>
                        </div>
                      ) : null}

                      {customEditorTab === "composer" ? (
                        composerCatalog ? (
                          <ModeComposer
                            catalog={composerCatalog}
                            state={composerState}
                            onChange={handleComposerChange}
                            syncError={composerSyncError}
                            onPreview={async () => {
                              const modeDef = buildComposerModeDefinition({
                                composer: composerState,
                                catalog: composerCatalog,
                                modeName: customModeName,
                                modeDescription: customModeDescription,
                              });
                              const json = JSON.stringify(modeDef, null, 2);
                              setCustomJson(json);
                              setCustomEditorSource("composer");
                              await handleCustomPreview(modeDef);
                              setEditingCustomMode(false);
                              requestAnimationFrame(() => {
                                previewPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
                              });
                            }}
                            previewDisabled={!customJson.trim()}
                            previewImgUrl={composerPreviewUrl}
                            previewLoading={composerPreviewLoading}
                            onRequestPreview={fetchComposerPreview}
                          />
                        ) : (
                          <div className="text-sm text-ink-light">{"正在加载拼装器配置..."}</div>
                        )
                      ) : null}

                    </div>
                  </DialogContent>
                </Dialog>

              </div>
            )}

            {/* Preferences Tab */}
            {activeTab === "preferences" && (
              <div className="space-y-4">
                <RefreshStrategyEditor
                  locale={"zh"}
                  location={currentLocation}
                  setLocation={applyGlobalLocation}
                  modeLanguage={modeLanguage}
                  setModeLanguage={setModeLanguage}
                  modeLanguageOptions={MODE_LANGUAGE_OPTIONS}
                  contentTone={contentTone}
                  setContentTone={setContentTone}
                  characterTones={characterTones}
                  setCharacterTones={setCharacterTones}
                  customPersonaTone={customPersonaTone}
                  setCustomPersonaTone={setCustomPersonaTone}
                  handleAddCustomPersona={handleAddCustomPersona}
                  strategy={strategy}
                  setStrategy={setStrategy}
                  refreshMin={refreshMin}
                  setRefreshMin={setRefreshMin}
                  toneOptions={TONE_OPTIONS}
                  personaPresets={PERSONA_PRESETS}
                  strategies={STRATEGIES}
                  timeSlotRules={timeSlotRules}
                  setTimeSlotRules={setTimeSlotRules}
                  availableModes={selectedModes ? Array.from(selectedModes) : []}
                  modeMeta={modeMeta}
                />
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">{"设备活跃状态"}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <label className="flex items-start gap-3 text-sm text-ink">
                      <input
                        type="checkbox"
                        checked={alwaysActive}
                        onChange={(e) => setAlwaysActive(e.target.checked)}
                        className="mt-1"
                      />
                      <div>
                        <div className="font-medium">{"始终保持活跃"}</div>
                        <div className="text-xs text-ink-light mt-1">
                          {"开启后，设备会持续保持活跃，不进入间歇状态或深度睡眠。设备在线时会立即生效；若设备在深睡，请先短按 BOOT 唤醒后再保存，或等待下次唤醒后生效。"}
                        </div>
                      </div>
                    </label>
                  </CardContent>
                </Card>
                <Button
                  variant="outline"
                  onClick={handleSavePreferences}
                  disabled={!mac || savingPrefs}
                  className="w-full bg-white text-ink border-ink/20 hover:bg-ink hover:text-white active:bg-ink active:text-white disabled:bg-white disabled:text-ink/50"
                >
                  {savingPrefs ? <Loader2 size={14} className="animate-spin mr-1" /> : <Save size={14} className="mr-1" />}
                  {"保存"}
                </Button>
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Keyboard size={16} className="text-ink-light" />
                      {"设备按键"}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <table className="w-full text-xs border-collapse border border-ink/20">
                      <thead>
                        <tr className="text-left text-ink-light">
                          <th className="px-2 py-1.5 font-medium">{"按键"}</th>
                          <th className="px-2 py-1.5 font-medium">{"动作"}</th>
                          <th className="px-2 py-1.5 font-medium">{"何时用"}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[
                          ["RST", "硬复位重启，固件不感知", "设备无响应；想让新配置立刻生效"],
                          ["BOOT 短按", "醒着时无动作", "—"],
                          ["BOOT 短按", "深睡中唤醒，给 10 分钟临时在线窗口", "设备睡着时叫醒它"],
                          ["BOOT 长按 2 秒", "进入配网门户", "改服务器地址 / 换 WiFi"],
                          ["BOOT 开机按住", "强制进配网", "救援"],
                        ].map(([btn, action, when]) => (
                          <tr key={btn + action} className="border-t border-ink/10 align-top">
                            <td className="px-2 py-1.5 whitespace-nowrap">
                              <code className="bg-paper-dark px-1.5 py-0.5 rounded">{btn}</code>
                            </td>
                            <td className="px-2 py-1.5 text-ink">{action}</td>
                            <td className="px-2 py-1.5 text-ink-light">{when}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="text-xs text-ink-light">
                      {"设备休眠时心跳稀疏，保存配置后提示「不在活跃同步状态」属正常：它会在下次唤醒时生效；想立刻看到，按 RST 唤醒。"}
                    </p>
                  </CardContent>
                </Card>
              </div>
            )}

            {/* Sharing Tab */}
            {activeTab === "sharing" && (
              <div className="space-y-4">
                {currentUserRole === "owner" ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">{"共享成员"}</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="flex gap-2 flex-wrap">
                        <input
                          value={shareUsernameInput}
                          onChange={(e) => setShareUsernameInput(e.target.value)}
                          placeholder={"输入要共享的用户名"}
                          className="flex-1 min-w-[220px] rounded-sm border border-ink/20 px-3 py-2 text-sm"
                        />
                        <Button variant="outline" size="sm" onClick={handleShareDevice} disabled={!shareUsernameInput.trim()}>
                          {"分享"}
                        </Button>
                      </div>
                      {membersLoading ? (
                        <div className="flex items-center gap-2 text-sm text-ink-light">
                          <Loader2 size={14} className="animate-spin" /> {"加载成员中..."}
                        </div>
                      ) : (
                        <div className="space-y-2">
                          {deviceMembers.map((member) => (
                            <div key={member.user_id} className="flex items-center justify-between rounded-sm border border-ink/10 p-2 text-sm">
                              <div>
                                <p className="font-medium text-ink">{member.username}</p>
                                <p className="text-xs text-ink-light">{member.role === "owner" ? "Owner" : "Member"}</p>
                              </div>
                              {member.role !== "owner" ? (
                                <Button variant="outline" size="sm" onClick={() => handleRemoveMember(member.user_id)}>
                                  {"移除"}
                                </Button>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ) : (
                  <div className="p-3 rounded-sm border border-ink/10 bg-paper text-sm text-ink-light">
                    {"只有设备 Owner 可以管理共享成员。"}
                  </div>
                )}

                {pendingRequests.some((item) => item.mac === mac) ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">{"待处理绑定请求"}</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      {pendingRequests.filter((item) => item.mac === mac).map((item) => (
                        <div key={item.id} className="flex items-center justify-between gap-2 rounded-sm border border-ink/10 p-2 text-sm">
                          <div>
                            <p className="font-medium text-ink">{item.requester_username}</p>
                            <p className="text-xs text-ink-light">{"请求绑定此设备"}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            <Button variant="outline" size="sm" onClick={() => handleRejectRequest(item.id)}>
                              {"拒绝"}
                            </Button>
                            <Button size="sm" onClick={() => handleApproveRequest(item.id)}>
                              {"同意"}
                            </Button>
                          </div>
                        </div>
                      ))}
                    </CardContent>
                  </Card>
                ) : null}
              </div>
            )}


            {/* Stats Tab */}
            {activeTab === "stats" && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <BarChart3 size={18} /> {"设备状态"}
                    {mac && <Button variant="ghost" size="sm" onClick={loadStats}><RefreshCw size={12} /></Button>}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {!mac && <p className="text-sm text-ink-light">{"需要连接设备后才能查看状态"}</p>}
                  {mac && !stats && <p className="text-sm text-ink-light">{"暂无统计数据"}</p>}
                  {stats && (
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                      <StatCard label={"总渲染次数"} value={stats.total_renders ?? "-"} />
                      <StatCard label={"缓存命中率"} value={stats.cache_hit_rate != null ? `${Math.round(stats.cache_hit_rate)}%` : "-"} />
                      <StatCard label={"电量"} value={batteryPct != null ? `${batteryPct}%` : "-"} />
                      <StatCard label={"电压"} value={stats.last_battery_voltage ? `${stats.last_battery_voltage.toFixed(2)}V` : "-"} />
                      <StatCard label={"WiFi 信号"} value={stats.last_rssi ? `${stats.last_rssi} dBm` : "-"} />
                      <StatCard label={"错误次数"} value={stats.error_count ?? "-"} />
                      {stats.last_refresh && <StatCard label={"上次刷新"} value={new Date(stats.last_refresh).toLocaleString("zh-CN")} />}
                    </div>
                  )}
                  {stats?.mode_frequency && Object.keys(stats.mode_frequency).length > 0 && (
                    <div className="mt-6">
                      <h4 className="text-sm font-medium mb-3">{"模式使用频率"}</h4>
                      <div className="space-y-2">
                        {Object.entries(stats.mode_frequency)
                          .sort(([, a], [, b]) => b - a)
                          .map(([mode, count]) => {
                            const max = Math.max(...Object.values(stats.mode_frequency!));
                            return (
                              <div key={mode} className="flex items-center gap-2 text-sm">
                                <span className="w-20 text-ink-light truncate">{modeMeta[mode]?.name || customModeMeta[mode]?.name || mode}</span>
                                <div className="flex-1 bg-paper-dark rounded-full h-4 overflow-hidden">
                                  <div className="bg-ink h-full rounded-full" style={{ width: `${(count / max) * 100}%` }} />
                                </div>
                                <span className="w-8 text-right text-ink-light text-xs">{count}</span>
                              </div>
                            );
                          })}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}
          </div>

          </div>
        </div>
      )}

      <Dialog
        open={showFocusTokenModal}
        onClose={() => {
          setShowFocusTokenModal(false);
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader
            onClose={() => {
              setShowFocusTokenModal(false);
            }}
          >
            <div>
              <DialogTitle>{"设备告警 Token 已生成"}</DialogTitle>
              <DialogDescription>
                {"将下面的 Token 配置到你的 OpenCLAW（或自建 Agent）里，用于向该设备发送紧急告警。"}
              </DialogDescription>
              <div className="mt-2 text-[11px] text-ink-light">
                {"提示：开启专注监听后，设备端通常需要重启/重新进入启动流程，才会开始 10 秒轮询告警并在屏幕显示内容。"}
              </div>
            </div>
          </DialogHeader>

          <div className="space-y-3">
            <div className="rounded-sm border border-ink/20 bg-white px-3 py-2 text-xs font-mono break-all">
              {focusAlertToken || "（空）"}
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(focusAlertToken);
                    showToast("已复制 Token", "success");
                  } catch {
                    showToast("复制失败，请手动选中复制", "error");
                  }
                }}
                disabled={!focusAlertToken}
              >
                {"复制 Token"}
              </Button>
              <Button
                type="button"
                onClick={() => {
                  setShowFocusTokenModal(false);
                }}
              >
                {"关闭"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Mobile save button */}
      {mac && (
        <div className="md:hidden fixed bottom-0 left-0 right-0 p-4 bg-white border-t border-ink/10">
          <Button
            variant="outline"
            onClick={handleSave}
            disabled={!mac || saving}
            className="w-full bg-white text-ink border-ink/20 hover:bg-ink hover:text-white active:bg-ink active:text-white disabled:bg-white disabled:text-ink/50"
          >
            {saving ? <Loader2 size={14} className="animate-spin mr-1" /> : <Save size={14} className="mr-1" />}
            {"保存到设备"}
          </Button>
        </div>
      )}

      {previewConfirm ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <Card className="w-full max-w-md mx-4">
            <CardHeader>
              <CardTitle>{"确认预览"}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm leading-6 text-ink">
                {"当前未命中缓存，将生成新的预览。是否继续？"}
              </p>
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => {
                    setPreviewConfirm(null);
                  }}
                >
                  {"取消"}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    const pending = previewConfirm;
                    setPreviewConfirm(null);
                    if (pending) {
                      handlePreview(pending.mode, pending.forceNoCache, pending.forcedModeOverride, true);
                    }
                  }}
                  className="bg-white text-ink border-ink/20 hover:bg-ink hover:text-white active:bg-ink active:text-white"
                >
                  {"确定"}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      ) : null}

      {/* 邀请码输入弹窗 */}

      {/* 相框多图管理弹窗 */}
      {adaptiveModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40" onClick={() => setAdaptiveModal(null)} />
          <div className="relative w-[min(520px,calc(100vw-32px))] rounded-sm border border-ink/15 bg-white shadow-xl">
            <div className="px-4 py-3 border-b border-ink/10 flex items-center justify-between">
              <div className="text-sm font-semibold text-ink">
                {"相框图片管理"}
              </div>
              <button className="text-ink-light hover:text-ink" onClick={() => setAdaptiveModal(null)}>
                ✕
              </button>
            </div>
            <div className="px-4 py-4 space-y-3">
              <div className="text-xs text-ink-light">
                {"上传至多 6 张图片，设备每次刷新时循环显示下一张。"}
              </div>
              <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                {"预览或下发只会临时生效；如需下次打开仍保留这些图片，请点击页面上的“保存到设备”。"}
              </div>
              <div className="grid grid-cols-3 gap-2">
                {adaptiveImageUrls.map((url, i) => (
                  <div key={`${url}-${i}`} className="relative group aspect-[4/3] rounded border border-ink/15 overflow-hidden bg-paper-dark">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={uploadSrc(url)} alt={`photo ${i + 1}`} className="w-full h-full object-cover" />
                    <button
                      className="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/60 text-white text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={() => setAdaptiveImageUrls((prev) => prev.filter((_, idx) => idx !== i))}
                      title={"移除"}
                    >
                      ✕
                    </button>
                    <div className="absolute bottom-0 left-0 right-0 bg-black/40 text-white text-[10px] text-center py-0.5">
                      {i + 1}
                    </div>
                  </div>
                ))}
                {adaptiveImageUrls.length < 6 && (
                  <button
                    className="aspect-[4/3] rounded border-2 border-dashed border-ink/20 flex flex-col items-center justify-center text-ink-light hover:text-ink hover:border-ink/40 transition-colors"
                    onClick={() => adaptiveFileInputRef.current?.click()}
                    disabled={adaptiveUploading2}
                  >
                    {adaptiveUploading2 ? (
                      <Loader2 size={20} className="animate-spin" />
                    ) : (
                      <>
                        <span className="text-xl leading-none">+</span>
                        <span className="text-[10px] mt-0.5">{"添加图片"}</span>
                      </>
                    )}
                  </button>
                )}
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button
                  variant="outline"
                  onClick={() => setAdaptiveModal(null)}
                >
                  {"取消"}
                </Button>
                <Button
                  variant="outline"
                  disabled={adaptiveImageUrls.length === 0 || previewLoading}
                  onClick={async () => {
                    const action = adaptiveModal.action;
                    const override: ModeOverride = { image_urls: [...adaptiveImageUrls], image_url: adaptiveImageUrls[0] || "" };
                    setAdaptiveModal(null);
                    await commitModalAction("MY_ADAPTIVE", action, override);
                  }}
                >
                  {adaptiveModal.action === "apply"
                    ? "确认并加入轮播"
                    : "确认并预览"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* 相册照片管理弹窗 */}
      {albumModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40" onClick={() => setAlbumModal(null)} />
          <div className="relative w-[min(520px,calc(100vw-32px))] rounded-sm border border-ink/15 bg-white shadow-xl">
            <div className="px-4 py-3 border-b border-ink/10 flex items-center justify-between">
              <div className="text-sm font-semibold text-ink">
                {"相册照片管理"}
              </div>
              <button className="text-ink-light hover:text-ink" onClick={() => setAlbumModal(null)}>
                ✕
              </button>
            </div>
            <div className="px-4 py-4 space-y-3">
              <div className="text-xs text-ink-light">
                {`上传至多 ${ALBUM_MAX_PHOTOS} 张照片，设备每天轮播显示一张，同一天始终显示同一张。`}
              </div>
              <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                {"照片已保存在服务器；把相册加入轮播后，点击页面上的“保存到设备”即可生效。"}
              </div>
              <div className="grid grid-cols-3 gap-2">
                {albumPhotos.map((photo, i) => (
                  <div key={photo.id} className="relative group aspect-[4/3] rounded border border-ink/15 overflow-hidden bg-paper-dark">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={apiUrl(`/api/album/${encodeURIComponent(mac || "")}/photos/${encodeURIComponent(photo.id)}/raw`)}
                      alt={photo.filename || `photo ${i + 1}`}
                      className="w-full h-full object-cover"
                    />
                    <button
                      className="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/60 text-white text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={() => deleteAlbumPhoto(photo.id)}
                      title={"删除"}
                    >
                      ✕
                    </button>
                    <div className="absolute bottom-0 left-0 right-0 bg-black/40 text-white text-[10px] text-center py-0.5">
                      {i + 1}
                    </div>
                  </div>
                ))}
                {albumPhotos.length < ALBUM_MAX_PHOTOS && (
                  <button
                    className="aspect-[4/3] rounded border-2 border-dashed border-ink/20 flex flex-col items-center justify-center text-ink-light hover:text-ink hover:border-ink/40 transition-colors"
                    onClick={() => albumFileInputRef.current?.click()}
                    disabled={albumUploading}
                  >
                    {albumUploading ? (
                      <Loader2 size={20} className="animate-spin" />
                    ) : (
                      <>
                        <span className="text-xl leading-none">+</span>
                        <span className="text-[10px] mt-0.5">{"添加照片"}</span>
                      </>
                    )}
                  </button>
                )}
              </div>
              <div className="text-xs text-ink-light">
                {albumLoading ? "加载中…" : `已上传 ${albumPhotos.length} / ${ALBUM_MAX_PHOTOS} 张`}
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button
                  variant="outline"
                  onClick={() => setAlbumModal(null)}
                >
                  {"取消"}
                </Button>
                <Button
                  variant="outline"
                  disabled={albumPhotos.length === 0 || previewLoading}
                  onClick={async () => {
                    const action = albumModal.action;
                    setAlbumModal(null);
                    await commitModalAction("CUSTOM_ALBUM", action);
                  }}
                >
                  {albumModal.action === "apply"
                    ? "确认并加入轮播"
                    : "确认并预览"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* 参数弹窗：与 /preview 页面一致（预览/加入轮播都会触发） */}
      {paramModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40" onClick={() => setParamModal(null)} />
          <div className="relative w-[min(520px,calc(100vw-32px))] rounded-sm border border-ink/15 bg-white shadow-xl">
            <div className="px-4 py-3 border-b border-ink/10 flex items-center justify-between">
              <div className="text-sm font-semibold text-ink">
                {paramModal.type === "quote"
                  ? "自定义语录"
                  : paramModal.type === "weather"
                  ? modeNeedsLocation(paramModal.mode)
                    ? "城市设置"
                    : "天气设置"
                  : paramModal.type === "cities"
                  ? "自选城市"
                  : paramModal.type === "memo"
                  ? "便签内容"
                  : paramModal.type === "countdown"
                  ? "倒计时设置"
                  : paramModal.type === "habit"
                  ? "习惯打卡"
                  : paramModal.type === "calendar"
                  ? "日历提醒"
                  : paramModal.type === "timetable"
                  ? "课程表设置"
                  : paramModal.type === "vocab"
                  ? "背单词设置"
                  : "人生进度条"}
              </div>
              <button className="text-ink-light hover:text-ink" onClick={() => setParamModal(null)}>
                ✕
              </button>
            </div>
            <div className="px-4 py-4 space-y-3">
              {paramModal.type === "quote" ? (
                <>
                  <div className="text-xs text-ink-light">
                    {"随机生成一条有深度的语录，或粘贴你自己的文字。"}
                  </div>
                  <textarea
                    value={quoteDraft}
                    onChange={(e) => setQuoteDraft(e.target.value)}
                    placeholder={"输入语录内容..."}
                    className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm min-h-28 bg-white"
                  />
                  <input
                    value={authorDraft}
                    onChange={(e) => setAuthorDraft(e.target.value)}
                    placeholder={"作者（可选）"}
                    className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                  />
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2">
                    <Button
                      variant="outline"
                      onClick={() => {
                        commitModalAction(paramModal.mode, paramModal.action);
                      }}
                      disabled={previewLoading}
                    >
                      {"随机生成"}
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => {
                        const q = quoteDraft.trim();
                        const a = authorDraft.trim();
                        commitModalAction(
                          paramModal.mode,
                          paramModal.action,
                          q ? ({ quote: q, author: a } as ModeOverride) : undefined,
                        );
                      }}
                      disabled={previewLoading}
                    >
                      {"使用我的输入"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "weather" ? (
                <>
                  <div className="text-xs text-ink-light">
                    {modeNeedsLocation(paramModal.mode)
                      ? "为该模式指定一个城市，留空则跟随设备位置。"
                      : "搜索并选择具体地点查看天气，避免重名城市误匹配。"}
                  </div>
                  <LocationPicker
                    value={weatherDraftLocation}
                    onChange={setWeatherDraftLocation}
                    locale={"zh"}
                    placeholder={"输入地点名称（如：上海、巴黎、Singapore）"}
                    helperText={"建议从候选列表中点选具体地点。"}
                    className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                    autoFocus
                  />
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2">
                    <Button
                      variant="outline"
                      onClick={() =>
                        setWeatherDraftLocation(
                          modeNeedsLocation(paramModal.mode) ? {} : defaultWeatherLocation,
                        )
                      }
                      disabled={previewLoading}
                    >
                      {modeNeedsLocation(paramModal.mode) ? "跟随设备位置" : "使用默认城市"}
                    </Button>
                    <Button
                      onClick={() => {
                        const nextLocation = cleanLocationValue(weatherDraftLocation);
                        if (modeNeedsLocation(paramModal.mode) && !nextLocation.city) {
                          // 未选城市 → 清掉该模式的专属城市覆盖，回到设备位置
                          commitModalAction(paramModal.mode, paramModal.action, undefined, true);
                          return;
                        }
                        commitModalAction(
                          paramModal.mode,
                          paramModal.action,
                          nextLocation.city ? (nextLocation as ModeOverride) : undefined,
                        );
                      }}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {modeNeedsLocation(paramModal.mode) ? "保存并预览" : "预览天气"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "cities" ? (
                <>
                  <div className="text-xs text-ink-light">
                    {"为该模式自选城市的天气，都留空则沿用蓝牙事件里的天气。"}
                  </div>
                  {modeLocationItems(paramModal.mode).map((item) => (
                    <div key={item.key} className="space-y-1">
                      <div className="text-xs font-medium text-ink-light">{item.label}</div>
                      <LocationPicker
                        value={cityPairDraft[item.key] || {}}
                        onChange={(next) => setCityPairDraft((prev) => ({ ...prev, [item.key]: next }))}
                        locale={"zh"}
                        placeholder={`搜索${item.label}`}
                        helperText={"建议从候选列表中点选具体地点。"}
                        className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                      />
                    </div>
                  ))}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2">
                    <Button
                      variant="outline"
                      onClick={() => commitModalAction(paramModal.mode, paramModal.action, undefined, true)}
                      disabled={previewLoading}
                    >
                      {"清除设置"}
                    </Button>
                    <Button
                      onClick={() => {
                        const slots: Record<string, LocationValue> = {};
                        for (const item of modeLocationItems(paramModal.mode)) {
                          const value = cleanLocationValue(cityPairDraft[item.key]);
                          if (value.city) slots[item.key] = value;
                        }
                        if (!Object.keys(slots).length) {
                          commitModalAction(paramModal.mode, paramModal.action, undefined, true);
                          return;
                        }
                        commitModalAction(paramModal.mode, paramModal.action, {
                          mode_settings: slots,
                        } as ModeOverride);
                      }}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"保存并预览"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "memo" ? (
                <>
                  <div className="text-xs text-ink-light mb-2">
                    {"设置便签标题和内容（最多3组，空的组不会显示）"}
                  </div>
                  {([1, 2, 3] as const).map((i) => {
                    const tKey = `title${i}` as keyof typeof memoDraft;
                    const cKey = `text${i}` as keyof typeof memoDraft;
                    return (
                      <div key={i} className="space-y-1">
                        <label className="text-xs font-medium text-ink-light">
                          {`标题 ${i}`}
                          {i > 1 && <span className="text-ink-light/50 ml-1">({"可选"})</span>}
                        </label>
                        <input
                          type="text"
                          value={memoDraft[tKey]}
                          onChange={(e) => setMemoDraft(prev => ({ ...prev, [tKey]: e.target.value }))}
                          placeholder={i === 1
                            ? "输入标题，如：今日待办"
                            : "可选标题"}
                          className="w-full rounded-sm border border-ink/20 px-3 py-1.5 text-sm bg-white"
                          autoFocus={i === 1}
                        />
                        <textarea
                          value={memoDraft[cKey]}
                          onChange={(e) => setMemoDraft(prev => ({ ...prev, [cKey]: e.target.value }))}
                          placeholder={"输入便签内容..."}
                          className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm min-h-16 bg-white"
                        />
                      </div>
                    );
                  })}
                  <div className="flex justify-end pt-2">
                    <Button
                      onClick={() => {
                        const settings: ModeOverride = {};
                        for (const i of [1, 2, 3]) {
                          const tKey = `title${i}` as keyof typeof memoDraft;
                          const cKey = `text${i}` as keyof typeof memoDraft;
                          settings[`memo_title_${i}`] = memoDraft[tKey].trim();
                          settings[`memo_text_${i}`] = memoDraft[cKey].trim();
                        }
                        commitModalAction(
                          paramModal.mode,
                          paramModal.action,
                          settings,
                        );
                      }}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"预览便签"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "countdown" ? (
                <>
                  <div className="text-xs text-ink-light mb-3">
                    {"设置倒计时事件名称和日期"}
                  </div>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs text-ink mb-1.5">
                        {"事件名称"}
                      </label>
                      <input
                        value={countdownName}
                        onChange={(e) => setCountdownName(e.target.value)}
                        placeholder={"例如：元旦、生日"}
                        className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-ink mb-1.5">
                        {"目标日期"}
                      </label>
                      <input
                        type="date"
                        value={countdownDate}
                        onChange={(e) => setCountdownDate(e.target.value)}
                        className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 pt-3">
                    <Button
                      onClick={() => commitModalAction(paramModal.mode, paramModal.action, undefined, true)}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"使用默认"}
                    </Button>
                    <Button
                      onClick={() => {
                        const today = new Date();
                        const target = new Date(countdownDate);
                        const days = Math.ceil((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
                        commitModalAction(paramModal.mode, paramModal.action, {
                          events: [
                            {
                              name: countdownName || ("倒计时"),
                              date: countdownDate,
                              type: "countdown",
                              days,
                            },
                          ],
                        } as ModeOverride);
                      }}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"预览倒计时"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "habit" ? (
                <>
                  <div className="text-xs text-ink-light mb-3">
                    {"管理你的习惯列表，勾选今日已完成的习惯。用 ✕ 移除不想追踪的习惯。"}
                  </div>
                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {habitItems.map((item, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={item.done}
                          onChange={(e) => {
                            const next = [...habitItems];
                            next[idx] = { ...next[idx], done: e.target.checked };
                            setHabitItems(next);
                          }}
                          className="w-4 h-4"
                        />
                        <input
                          value={item.name}
                          onChange={(e) => {
                            const next = [...habitItems];
                            next[idx] = { ...next[idx], name: e.target.value };
                            setHabitItems(next);
                          }}
                          className="flex-1 rounded-sm border border-ink/20 px-3 py-1.5 text-sm bg-white"
                        />
                        <button
                          onClick={() => setHabitItems(habitItems.filter((_, i) => i !== idx))}
                          className="text-ink-light hover:text-red-500 px-2"
                          title={"移除此习惯"}
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={() => setHabitItems([...habitItems, { name: "", done: false }])}
                    className="w-full mt-2 px-3 py-2 rounded-sm border border-dashed border-ink/20 text-sm text-ink-light hover:text-ink hover:border-ink/40 transition-colors"
                  >
                    + {"添加习惯"}
                  </button>
                  <div className="grid grid-cols-2 gap-2 pt-3">
                    <Button
                      onClick={() => commitModalAction(paramModal.mode, paramModal.action, undefined, true)}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"使用默认"}
                    </Button>
                    <Button
                      onClick={() => {
                        const tracked = habitItems.filter((h) => h.name.trim());
                        commitModalAction(paramModal.mode, paramModal.action, {
                          habitItems: tracked.map((h) => ({ name: h.name.trim(), done: h.done })),
                        } as ModeOverride);
                      }}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"预览打卡"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "lifebar" ? (
                <>
                  <div className="text-xs text-ink-light mb-3">
                    {"设置你的年龄和预期寿命"}
                  </div>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs text-ink mb-1.5">
                        {"芳龄几何？"}
                      </label>
                      <input
                        type="number"
                        value={userAge}
                        onChange={(e) => setUserAge(parseInt(e.target.value) || 0)}
                        min="0"
                        max="120"
                        className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-ink mb-1.5">
                        {"退休金领到？"}
                      </label>
                      <div className="grid grid-cols-4 gap-2">
                        {[80, 90, 100, 120].map((years) => (
                          <button
                            key={years}
                            onClick={() => setLifeExpectancy(years)}
                            className={`px-3 py-2 rounded-sm text-sm transition-colors ${
                              lifeExpectancy === years
                                ? "bg-ink text-white"
                                : "bg-paper-dark text-ink hover:bg-ink/10"
                            }`}
                          >
                            {years} {"岁"}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 pt-3">
                    <Button
                      onClick={() => commitModalAction(paramModal.mode, paramModal.action, undefined, true)}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"使用默认"}
                    </Button>
                    <Button
                      onClick={() => {
                        const lifePct = ((userAge / lifeExpectancy) * 100).toFixed(1);
                        commitModalAction(paramModal.mode, paramModal.action, {
                          age: userAge,
                          life_expect: lifeExpectancy,
                          life_pct: parseFloat(lifePct),
                          life_label: "人生",
                        } as ModeOverride);
                      }}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"预览进度"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "calendar" ? (
                <>
                  <div className="text-xs text-ink-light mb-3">
                    {"为日历中的特定日期添加提醒事项，提醒会显示在日期下方。"}
                  </div>
                  <CalendarReminders
                    reminders={
                      (getModeOverride("CALENDAR") as Record<string, unknown>)?.reminders as Record<string, string> ?? {}
                    }
                    onChange={(r) => {
                      updateModeOverride("CALENDAR", {
                        reminders: Object.keys(r).length > 0 ? r : undefined,
                      } as Record<string, unknown>);
                    }}
                  />
                  <div className="grid grid-cols-2 gap-2 pt-3">
                    <Button
                      onClick={() => commitModalAction(paramModal.mode, paramModal.action, undefined, true)}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"跳过预览"}
                    </Button>
                    <Button
                      onClick={() => {
                        const reminders = (getModeOverride("CALENDAR") as Record<string, unknown>)?.reminders as Record<string, string> | undefined;
                        commitModalAction(paramModal.mode, paramModal.action,
                          reminders && Object.keys(reminders).length > 0
                            ? { reminders } as ModeOverride
                            : undefined,
                        );
                      }}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"预览日历"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "timetable" ? (
                <>
                  <div className="text-xs text-ink-light mb-3">
                    {"选择课表类型并编辑课程安排，点击单元格即可修改。"}
                  </div>
                  <TimetableEditor
                    data={timetableData}
                    onChange={setTimetableData}
                  />
                  <div className="grid grid-cols-2 gap-2 pt-3">
                    <Button
                      onClick={() => commitModalAction(paramModal.mode, paramModal.action, undefined, true)}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"使用默认"}
                    </Button>
                    <Button
                      onClick={() => {
                        commitModalAction(paramModal.mode, paramModal.action, {
                          style: timetableData.style,
                          weekdays: timetableData.weekdays,
                          periods: timetableData.periods,
                          courses: timetableData.courses,
                        } as ModeOverride);
                      }}
                      disabled={previewLoading}
                      variant="outline"
                    >
                      {"预览课程表"}
                    </Button>
                  </div>
                </>
              ) : paramModal.type === "vocab" ? (
                <>
                  <div className="text-xs text-ink-light mb-3">
                    {"选择词库并设置每日目标。保存后，下次进入背词模式会使用新的设置。"}
                  </div>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs text-ink-light mb-1">{"词库"}</label>
                      <select
                        value={vocabDeckId}
                        onChange={(e) => setVocabDeckId(e.target.value)}
                        className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                      >
                        <option value="primary_en">{"小学英语"}</option>
                        <option value="middle_school_en">{"初中英语"}</option>
                        <option value="high_school_en">{"高中英语"}</option>
                        <option value="cet4_en">{"四级词汇"}</option>
                        <option value="cet6_en">{"六级词汇"}</option>
                        <option value="ielts_en">{"雅思词汇"}</option>
                        <option value="toefl_en">{"托福词汇"}</option>
                        <option value="core_en">{"核心英语"}</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs text-ink-light mb-1">{"每日目标"}</label>
                      <input
                        type="number"
                        min={1}
                        max={200}
                        value={vocabDailyLimit}
                        onChange={(e) => setVocabDailyLimit(Math.max(1, Math.min(200, Number(e.target.value) || 30)))}
                        className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm bg-white"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 pt-3">
                    <Button
                      onClick={() =>
                        saveVocabReviewSettings(paramModal.mode, {
                          deck_id: "core_en",
                          daily_limit: 30,
                          new_cards_per_day: 30,
                        } as ModeOverride)
                      }
                      disabled={saving}
                      variant="outline"
                    >
                      {"保存默认"}
                    </Button>
                    <Button
                      onClick={() =>
                        saveVocabReviewSettings(paramModal.mode, {
                          deck_id: vocabDeckId,
                          daily_limit: vocabDailyLimit,
                          new_cards_per_day: vocabDailyLimit,
                        } as ModeOverride)
                      }
                      disabled={saving}
                      variant="outline"
                    >
                      {saving ? "保存中..." : "保存设置"}
                    </Button>
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {/* Toast */}
      {toast && (
        <div className={`fixed top-5 right-5 z-50 px-4 py-3 rounded-sm text-sm font-medium shadow-lg animate-fade-in ${
          toast.type === "success" ? "bg-green-50 text-green-800 border border-green-200"
          : toast.type === "error" ? "bg-red-50 text-red-800 border border-red-200"
          : "bg-amber-50 text-amber-800 border border-amber-200"
        }`}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

export default function ConfigPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center min-h-screen text-ink-light"><Loader2 size={24} className="animate-spin mr-2" /> 加载中...</div>}>
      <ConfigPageInner />
    </Suspense>
  );
}
