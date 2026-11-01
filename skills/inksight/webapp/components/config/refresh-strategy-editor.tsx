"use client";

import { Globe, Plus, Trash2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Chip, Field } from "@/components/config/shared";
import { LocationPicker } from "@/components/config/location-picker";
import type { LocationValue } from "@/lib/locations";

/** 时段规则数据结构，与后端 time_slot_rules 字段对齐 */
export interface TimeSlotRule {
  startHour: number;
  endHour: number;
  modes: string[];
}

export function RefreshStrategyEditor({
  locale,
  location,
  setLocation,
  modeLanguage,
  setModeLanguage,
  modeLanguageOptions,
  contentTone,
  setContentTone,
  characterTones,
  setCharacterTones,
  customPersonaTone,
  setCustomPersonaTone,
  handleAddCustomPersona,
  strategy,
  setStrategy,
  refreshMin,
  setRefreshMin,
  nightRefreshMin,
  setNightRefreshMin,
  nightStartHour,
  setNightStartHour,
  nightEndHour,
  setNightEndHour,
  toneOptions,
  personaPresets,
  strategies,
  timeSlotRules,
  setTimeSlotRules,
  availableModes,
  modeMeta,
}: {
  locale: "zh" | "en";
  location: LocationValue;
  setLocation: (value: LocationValue) => void;
  modeLanguage: string;
  setModeLanguage: (value: string) => void;
  modeLanguageOptions: readonly { value: string; label: string }[];
  contentTone: string;
  setContentTone: (value: string) => void;
  characterTones: string[];
  setCharacterTones: React.Dispatch<React.SetStateAction<string[]>>;
  customPersonaTone: string;
  setCustomPersonaTone: (value: string) => void;
  handleAddCustomPersona: () => void;
  strategy: string;
  setStrategy: (value: string) => void;
  refreshMin: number;
  setRefreshMin: (value: number) => void;
  nightRefreshMin: number;
  setNightRefreshMin: (value: number) => void;
  nightStartHour: number;
  setNightStartHour: (value: number) => void;
  nightEndHour: number;
  setNightEndHour: (value: number) => void;
  toneOptions: readonly { value: string; label: string }[];
  personaPresets: readonly string[];
  strategies: Record<string, string>;
  timeSlotRules: TimeSlotRule[];
  setTimeSlotRules: (rules: TimeSlotRule[]) => void;
  availableModes: string[];
  modeMeta: Record<string, { name: string; tip: string }>;
}) {
  const customPresets = characterTones.filter(
    (value) => !personaPresets.includes(value),
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe size={18} /> {"个性化设置"}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <Field label={"城市（全局默认）"}>
          <LocationPicker
            value={location}
            onChange={setLocation}
            locale={locale}
            placeholder={"如：深圳"}
            helperText={"搜索后请选择具体地点，例如：上海 · 中国、巴黎 · 法国、Singapore · Singapore。"}
            className="w-full rounded-sm border border-ink/20 px-3 py-2 text-sm"
          />
        </Field>
        <Field label={"语言"}>
          <div className="flex flex-wrap gap-2">
            {modeLanguageOptions.map((opt) => (
              <Chip
                key={opt.value}
                selected={modeLanguage === opt.value}
                onClick={() => setModeLanguage(opt.value)}
              >
                {opt.label}
              </Chip>
            ))}
          </div>
        </Field>
        <Field label={"内容语气"}>
          <div className="flex flex-wrap gap-2">
            {toneOptions.map((opt) => (
              <Chip
                key={opt.value}
                selected={contentTone === opt.value}
                onClick={() => setContentTone(opt.value)}
              >
                {opt.label}
              </Chip>
            ))}
          </div>
        </Field>
        <Field label={"人设风格"}>
          <div className="flex flex-wrap gap-2">
            {personaPresets.map((value) => (
              <Chip
                key={value}
                selected={characterTones.includes(value)}
                onClick={() =>
                  setCharacterTones((prev) =>
                    prev.includes(value)
                      ? prev.filter((item) => item !== value)
                      : [...prev, value],
                  )
                }
              >
                {value}
              </Chip>
            ))}
          </div>
          <div className="mt-2 flex gap-2">
            <input
              value={customPersonaTone}
              onChange={(e) => setCustomPersonaTone(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleAddCustomPersona();
                }
              }}
              placeholder={"自定义人设风格"}
              className="flex-1 rounded-sm border border-ink/20 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={handleAddCustomPersona}
              className="rounded-sm border border-ink/20 px-3 py-2 text-sm"
            >
              {"添加"}
            </button>
          </div>
          {customPresets.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {customPresets.map((value) => (
                <Chip
                  key={value}
                  selected
                  onClick={() =>
                    setCharacterTones((prev) => prev.filter((item) => item !== value))
                  }
                >
                  {value}
                </Chip>
              ))}
            </div>
          )}
        </Field>
        <Field label={"刷新策略"}>
          <div className="grid grid-cols-2 gap-2 mb-3">
            {Object.entries(strategies).map(([key, desc]) => (
              <button
                key={key}
                onClick={() => setStrategy(key)}
                className={`group p-3 rounded-sm border text-left transition-colors ${
                  strategy === key
                    ? "border-ink bg-ink text-white"
                    : "border-ink/10 hover:bg-ink hover:text-white"
                }`}
              >
                <div className="text-sm font-medium">{key}</div>
                <div
                  className={`text-xs mt-1 ${
                    strategy === key
                      ? "text-white/70"
                      : "text-ink-light group-hover:text-white/80"
                  }`}
                >
                  {desc}
                </div>
              </button>
            ))}
          </div>
          <label className="block text-sm font-medium mb-2">
            {"刷新间隔 (分钟)"}
          </label>
          <input
            type="number"
            min={10}
            max={1440}
            value={refreshMin}
            onChange={(e) => setRefreshMin(Number(e.target.value))}
            className="w-32 rounded-sm border border-ink/20 px-3 py-2 text-sm"
          />
          <p className="mt-2 text-xs text-ink-light">
            {"可设置范围：10-1440 分钟"}
          </p>

          {/* 夜间睡眠：夜间时段改用更长的刷新间隔，减少墨水屏刷新次数 */}
          <div className="mt-4 rounded-sm border border-ink/15 bg-paper p-4 space-y-3">
            <label className="flex items-start gap-3 text-sm text-ink">
              <input
                type="checkbox"
                checked={nightRefreshMin > 0}
                onChange={(e) => setNightRefreshMin(e.target.checked ? 360 : 0)}
                className="mt-1"
              />
              <div className="font-medium">{"启用夜间睡眠"}</div>
            </label>

            {nightRefreshMin > 0 && (
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium mb-2">
                    {"夜间刷新间隔 (分钟)"}
                  </label>
                  <input
                    type="number"
                    min={10}
                    max={1440}
                    value={nightRefreshMin}
                    onChange={(e) => setNightRefreshMin(Number(e.target.value))}
                    className="w-32 rounded-sm border border-ink/20 px-3 py-2 text-sm"
                  />
                  <p className="mt-2 text-xs text-ink-light">
                    {"可设置范围：10-1440 分钟"}
                  </p>
                </div>

                <div className="flex gap-4 flex-wrap">
                  <div>
                    <label className="block text-sm font-medium mb-2">
                      {"夜间开始"}
                    </label>
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min={0}
                        max={23}
                        value={nightStartHour}
                        onChange={(e) => setNightStartHour(Number(e.target.value))}
                        className="w-20 rounded-sm border border-ink/20 px-2 py-1.5 text-sm text-center"
                      />
                      <span className="text-sm text-ink-light">{"时"}</span>
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-2">
                      {"夜间结束"}
                    </label>
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min={0}
                        max={24}
                        value={nightEndHour}
                        onChange={(e) => setNightEndHour(Number(e.target.value))}
                        className="w-20 rounded-sm border border-ink/20 px-2 py-1.5 text-sm text-center"
                      />
                      <span className="text-sm text-ink-light">{"时"}</span>
                    </div>
                  </div>
                </div>

                <p className="text-xs text-ink-light">
                  {"夜间时段内设备改用这个间隔，白天照旧；夜间不刷的内容要等早上那一次才上屏。"}
                </p>
              </div>
            )}
          </div>
        </Field>

        {/* 时段规则编辑区：仅 time_slot 策略时显示 */}
        {strategy === "time_slot" && (
          <TimeSlotRulesEditor
            rules={timeSlotRules}
            onChange={setTimeSlotRules}
            availableModes={availableModes}
            modeMeta={modeMeta}
          />
        )}
      </CardContent>
    </Card>
  );
}

/** 时段规则编辑器子组件 */
function TimeSlotRulesEditor({
  rules,
  onChange,
  availableModes,
  modeMeta,
}: {
  rules: TimeSlotRule[];
  onChange: (rules: TimeSlotRule[]) => void;
  availableModes: string[];
  modeMeta: Record<string, { name: string; tip: string }>;
}) {
  const addRule = () => {
    if (rules.length >= 12) return;
    onChange([...rules, { startHour: 9, endHour: 12, modes: [] }]);
  };

  const updateRule = (index: number, patch: Partial<TimeSlotRule>) => {
    onChange(rules.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  };

  const removeRule = (index: number) => {
    onChange(rules.filter((_, i) => i !== index));
  };

  return (
    <div className="rounded-sm border border-ink/15 bg-paper p-4 space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium text-ink">{"时段规则"}</label>
        <span className="text-xs text-ink-light">{rules.length}/12</span>
      </div>

      {rules.length === 0 && (
        <p className="text-xs text-ink-light">{"暂无时段规则，点击下方按钮添加"}</p>
      )}

      <div className="space-y-3 max-h-72 overflow-y-auto">
        {rules.map((rule, index) => (
          <TimeSlotRuleRow
            key={index}
            rule={rule}
            onChange={(patch) => updateRule(index, patch)}
            onRemove={() => removeRule(index)}
            availableModes={availableModes}
            modeMeta={modeMeta}
          />
        ))}
      </div>

      <button
        type="button"
        onClick={addRule}
        disabled={rules.length >= 12}
        className="flex items-center gap-2 rounded-sm border border-ink/20 px-3 py-2 text-sm text-ink hover:bg-ink hover:text-white transition-colors disabled:opacity-40 disabled:hover:bg-white disabled:hover:text-ink"
      >
        <Plus size={14} />
        {"添加时段"}
      </button>

      <p className="text-xs text-ink-light">{"为不同时间段配置不同的内容模式，未覆盖的时段使用随机选择"}</p>
    </div>
  );
}

/** 单条时段规则行 */
function TimeSlotRuleRow({
  rule,
  onChange,
  onRemove,
  availableModes,
  modeMeta,
}: {
  rule: TimeSlotRule;
  onChange: (patch: Partial<TimeSlotRule>) => void;
  onRemove: () => void;
  availableModes: string[];
  modeMeta: Record<string, { name: string; tip: string }>;
}) {
  const addMode = (mode: string) => {
    if (!rule.modes.includes(mode)) {
      onChange({ modes: [...rule.modes, mode] });
    }
  };

  const removeMode = (mode: string) => {
    onChange({ modes: rule.modes.filter((m) => m !== mode) });
  };

  const unselectedModes = availableModes.filter((m) => !rule.modes.includes(m));

  return (
    <div className="rounded-sm border border-ink/10 bg-white p-3 space-y-2">
      {/* 时间段输入 */}
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="number"
          min={0}
          max={23}
          value={rule.startHour}
          onChange={(e) => onChange({ startHour: Number(e.target.value) })}
          className="w-16 rounded-sm border border-ink/20 px-2 py-1.5 text-sm text-center"
        />
        <span className="text-sm text-ink-light">—</span>
        <input
          type="number"
          min={0}
          max={24}
          value={rule.endHour}
          onChange={(e) => onChange({ endHour: Number(e.target.value) })}
          className="w-16 rounded-sm border border-ink/20 px-2 py-1.5 text-sm text-center"
        />
        <span className="text-sm text-ink-light">{"时"}</span>
        <button
          type="button"
          onClick={onRemove}
          className="ml-auto p-1.5 rounded-sm text-ink hover:bg-red-50 hover:text-red-600 transition-colors"
          title={"删除此规则"}
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* 模式选择：下拉添加 + Chip 展示已选 */}
      <div className="flex items-center gap-2 flex-wrap">
        {/* 已选模式：Chip 可点击移除 */}
        {rule.modes.map((mode) => (
          <Chip
            key={mode}
            selected
            onClick={() => removeMode(mode)}
          >
            {modeMeta[mode]?.name || mode}
          </Chip>
        ))}

        {/* 下拉选择器添加模式 */}
        {unselectedModes.length > 0 && (
          <select
            value=""
            onChange={(e) => {
              const val = e.target.value;
              if (val) addMode(val);
              e.target.value = ""; // reset
            }}
            className="rounded-sm border border-ink/20 px-2 py-1.5 text-sm bg-white text-ink min-w-[120px] cursor-pointer"
          >
            <option value="">{"+ 添加模式"}</option>
            {unselectedModes.map((mode) => (
              <option key={mode} value={mode}>
                {modeMeta[mode]?.name || mode}
              </option>
            ))}
          </select>
        )}
      </div>

      {rule.modes.length === 0 && (
        <p className="text-xs text-ink-light">{"请至少选择一个模式"}</p>
      )}
    </div>
  );
}
