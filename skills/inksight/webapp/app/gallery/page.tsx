"use client";

import { useCallback, useEffect, useMemo, useState, Suspense } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { apiUrl } from "@/lib/api";
import { authHeaders, clearToken } from "@/lib/auth";
import { localeFromPathname, t, withLocalePath } from "@/lib/i18n";

interface GalleryMode {
  mode_id: string;
  name: string;
  form: "grid" | "text" | "wide";
}

interface Field {
  label: string;
  value: string;
  missing: boolean;
}

interface Entry {
  date: string;
  phase?: string;
  title: string;
  text: string;
  meta: string;
  has_screen: boolean;
  fields: Field[];
}

interface WideEntry {
  date: string;
  title: string;
  subtitle: string;
  text: string;
  meta: string;
  image: string;
}

function todayStr(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function shiftDay(day: string, delta: number): string {
  const [y, m, d] = day.split("-").map((part) => parseInt(part, 10));
  const target = new Date(y, (m || 1) - 1, d || 1);
  target.setDate(target.getDate() + delta);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(target.getDate())}`;
}

function monthLabel(key: string): string {
  const [y, m] = key.split("-");
  return `${y} 年 ${parseInt(m, 10)} 月`;
}

function GalleryInner() {
  const pathname = usePathname();
  const router = useRouter();
  const locale = localeFromPathname(pathname || "/");

  const [modes, setModes] = useState<GalleryMode[]>([]);
  const [active, setActive] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [wide, setWide] = useState<WideEntry | null>(null);
  const [wideDay, setWideDay] = useState(todayStr());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // 未登录：跟 preview / profile 页一样跳登录页，登录后回到回顾
  const redirectToLogin = useCallback(() => {
    clearToken();
    router.replace(
      `${withLocalePath(locale, "/login")}?next=${encodeURIComponent("/gallery")}`,
    );
  }, [router, locale]);

  const loadModes = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(apiUrl("/api/gallery"), {
        headers: authHeaders(),
      });
      if (res.status === 401) {
        redirectToLogin();
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list: GalleryMode[] = data.modes || [];
      setModes(list);
      setActive((prev) => (prev && list.some((m) => m.mode_id === prev) ? prev : list[0]?.mode_id || ""));
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [redirectToLogin]);

  useEffect(() => {
    loadModes();
  }, [loadModes]);

  const activeMode = useMemo(
    () => modes.find((m) => m.mode_id === active) || null,
    [modes, active],
  );

  const loadEntries = useCallback(async () => {
    if (!activeMode || activeMode.form === "wide") {
      setEntries([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        apiUrl(`/api/gallery?mode=${encodeURIComponent(activeMode.mode_id)}`),
        { headers: authHeaders() },
      );
      if (res.status === 401) {
        redirectToLogin();
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEntries(data.entries || []);
    } catch (err) {
      setError(String(err));
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [activeMode, redirectToLogin]);

  const loadWide = useCallback(async () => {
    if (!activeMode || activeMode.form !== "wide") return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        apiUrl(`/api/gallery?mode=${encodeURIComponent(activeMode.mode_id)}&day=${wideDay}`),
        { headers: authHeaders() },
      );
      if (res.status === 401) {
        redirectToLogin();
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setWide(data.entry || null);
    } catch (err) {
      setError(String(err));
      setWide(null);
    } finally {
      setLoading(false);
    }
  }, [activeMode, wideDay, redirectToLogin]);

  useEffect(() => {
    if (!activeMode) return;
    if (activeMode.form === "wide") {
      loadWide();
    } else {
      loadEntries();
    }
  }, [activeMode, loadEntries, loadWide]);

  const grouped = useMemo(() => {
    const map = new Map<string, Entry[]>();
    for (const entry of entries) {
      const key = entry.date.slice(0, 7);
      const bucket = map.get(key);
      if (bucket) bucket.push(entry);
      else map.set(key, [entry]);
    }
    return Array.from(map.entries()).sort((a, b) => (a[0] < b[0] ? 1 : -1));
  }, [entries]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <div className="mb-6">
        <h1 className="font-serif text-2xl font-bold text-ink">{t(locale, "nav.gallery", "回顾")}</h1>
        <p className="text-sm text-ink-light mt-2">这些天攒下的东西，都在这儿了。</p>
      </div>

      <div className="flex flex-wrap gap-2 mb-8 border-b border-ink/10 pb-3">
        {modes.map((mode) => (
          <button
            key={mode.mode_id}
            type="button"
            onClick={() => setActive(mode.mode_id)}
            className={
              mode.mode_id === active
                ? "rounded-full border border-ink bg-ink px-4 py-1.5 text-sm text-white"
                : "rounded-full border border-ink/15 bg-white px-4 py-1.5 text-sm text-ink-muted hover:border-ink/40"
            }
          >
            {mode.name}
          </button>
        ))}
      </div>

      {error && <p className="text-sm text-red-600 mb-4">{error}</p>}
      {loading && (
        <p className="flex items-center text-sm text-ink-light">
          <Loader2 size={16} className="animate-spin mr-2" />
          载入中…
        </p>
      )}

      {!loading && !error && !activeMode && (
        <p className="text-sm text-ink-light">还没有留下东西。</p>
      )}

      {!loading && activeMode?.form === "grid" && (
        <div className="space-y-10">
          {grouped.length === 0 && <p className="text-sm text-ink-light">还没有留下东西。</p>}
          {grouped.map(([month, items]) => (
            <section key={month}>
              <h2 className="text-sm text-ink-light mb-3">{monthLabel(month)}</h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((entry) => (
                  <figure
                    key={`${entry.date}-${entry.phase || entry.title}`}
                    className="rounded-lg border border-ink/10 bg-white p-3"
                  >
                    <img
                      src={apiUrl(
                        `/api/gallery/image?mode=${encodeURIComponent(activeMode.mode_id)}&day=${entry.date}` +
                          (entry.phase ? `&phase=${encodeURIComponent(entry.phase)}` : ""),
                      )}
                      alt={entry.title || entry.date}
                      className="w-full rounded border border-ink/5 bg-paper object-contain"
                    />
                    <figcaption className="mt-2">
                      <div className="text-xs text-ink-light">
                        {entry.date}
                        {entry.phase && ` · ${entry.title || entry.phase}`}
                      </div>
                      {entry.title && (
                        <div className="text-sm font-semibold text-ink mt-0.5">{entry.title}</div>
                      )}
                      {entry.fields.length > 0 && (
                        <dl className="mt-2 space-y-0.5">
                          {entry.fields.map((f) => (
                            <div key={f.label} className="flex gap-2 text-xs leading-relaxed">
                              <dt className="shrink-0 text-ink-light">{f.label}</dt>
                              <dd
                                className={
                                  f.missing ? "text-ink-light italic" : "text-ink-muted"
                                }
                              >
                                {f.value || "历史缺失"}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      )}
                    </figcaption>
                  </figure>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {!loading && activeMode?.form === "text" && (
        <div className="max-w-2xl">
          {entries.length === 0 && <p className="text-sm text-ink-light">还没有留下东西。</p>}
          <ol className="space-y-5">
            {[...entries].reverse().map((entry) => (
              <li key={`${entry.date}-${entry.text}`} className="border-l border-ink/15 pl-4">
                <div className="text-xs text-ink-light">{entry.date}</div>
                {entry.title && (
                  <div className="text-sm font-semibold text-ink mt-0.5">{entry.title}</div>
                )}
                <div className="text-ink mt-1 leading-relaxed">{entry.text}</div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {!loading && activeMode?.form === "wide" && (
        <div className="max-w-3xl">
          <div className="flex items-center justify-between mb-4">
            <button
              type="button"
              onClick={() => setWideDay(shiftDay(wideDay, -1))}
              className="flex items-center text-sm text-ink-muted hover:text-ink"
            >
              <ChevronLeft size={16} className="mr-1" />
              前一天
            </button>
            <div className="text-sm text-ink">{wideDay}</div>
            <button
              type="button"
              onClick={() => setWideDay(shiftDay(wideDay, 1))}
              disabled={wideDay >= todayStr()}
              className="flex items-center text-sm text-ink-muted hover:text-ink disabled:opacity-35 disabled:pointer-events-none"
            >
              后一天
              <ChevronRight size={16} className="ml-1" />
            </button>
          </div>
          {wide?.image ? (
            <img
              src={wide.image}
              alt={wide.title || wideDay}
              className="w-full rounded-lg border border-ink/10 bg-white"
            />
          ) : (
            <p className="text-sm text-ink-light">这一天没有内容。</p>
          )}
          {wide && (
            <div className="mt-4">
              {wide.title && <h2 className="font-serif text-lg font-bold text-ink">{wide.title}</h2>}
              {wide.subtitle && <div className="text-xs text-ink-light mt-0.5">{wide.subtitle}</div>}
              {wide.text && <p className="text-ink-muted mt-2 leading-relaxed">{wide.text}</p>}
              {wide.meta && <p className="text-sm text-ink-light mt-2">{wide.meta}</p>}
            </div>
          )}
          <p className="text-xs text-ink-light mt-6">
            电子世界按日期推演，过去的每一天都翻得到；明天还没发生。
          </p>
        </div>
      )}
    </main>
  );
}

export default function GalleryPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-screen text-ink-light">
          <Loader2 size={24} className="animate-spin mr-2" />
          加载中...
        </div>
      }
    >
      <GalleryInner />
    </Suspense>
  );
}
